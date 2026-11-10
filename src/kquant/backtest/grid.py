"""그리드 사전 계산과 증분 저장 (spec §8).

- 그리드 정의: 패키지의 ``grid.yaml``. 키 문자열 예: ``value|n10|small0.3|mc0|annual``.
- 기간마다 판단 기준일 스냅샷·유니버스·전략 순위를 한 번 계산해 모든 키가 공유한다.
- 선정: 조합 순위에서 리밸런싱일에 실제 거래가 없는 종목(행 없음 또는 거래량 0 = 거래정지)을 빼고 상위 N.
  빠진 종목은 기간 기록의 ``excluded``에 사유와 함께 남긴다. 판단 시점에 알 수 있는 정보만 쓴다.
- 이상 수익률(2026-11-07 변경): 기간 중 이상 수익률을 이유로 미리 빼지 않는다(미래 정보 → 정리매매·상폐 손실을
  지우는 편향). 대신 보유 종목에 이상일이 있으면 ``abnormal_held``에 (종목, 날짜, 수익률)을 기록해 리포트에
  참고용으로 보여 준다.
- 기간 말 정산(방식 A): 기간 마지막 날 거래가 없는(거래정지) 보유 종목은 기간 종료 후 첫 거래일 수정종가로
  정산한다(``settled``). 최신일까지 재개되지 않았으면 잠정적으로 마지막 가격을 쓰고 ``pending``으로 표시한다.
- 증분: 데이터·코드·그리드 지문(fingerprint)이 같으면 이미 저장된 **종료된 기간**은 다시 계산하지 않는다
  (단 ``pending`` 기간은 다시 계산). 진행 중 기간과 그 이후만 다시 계산한다. 지문이 바뀌면 전체를 다시 계산한다.
- 저장(``out_dir``): ``periods.parquet``, ``daily.parquet``(기간별 거래일 총액), ``benchmark.parquet``, ``meta.json``.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import pandas as pd
import yaml

from kquant.backtest.benchmark import market_index
from kquant.backtest.engine import simulate_period
from kquant.backtest.schedule import FIRST_YEAR, Period, periods
from kquant.prices.anomaly import abnormal_days
from kquant.strategies.base import combine, rank_strategies
from kquant.strategies.momentum import LOOKBACK_MONTHS, START_TOLERANCE_DAYS, add_momentum
from kquant.universe import UniverseParams, build_universe

CODE_FILES = [
    "strategies/base.py", "strategies/value.py", "strategies/salesmt.py", "strategies/momentum.py",
    "universe.py", "backtest/engine.py", "backtest/schedule.py", "backtest/grid.py", "fin/snapshot.py",
    "prices/store.py", "prices/anomaly.py", "prices/adjust.py", "backtest/benchmark.py",
    "reports/metrics.py", "reports/html.py",
]


@dataclass(frozen=True)
class GridKey:
    strategies: tuple[str, ...]
    n: int
    size: str
    cut: float | None
    min_marcap: float
    schedule: str

    @property
    def universe(self) -> UniverseParams:
        return UniverseParams(self.size, self.cut, self.min_marcap or None)

    @property
    def id(self) -> str:
        size = "all" if self.size == "all" else f"{self.size}{self.cut:g}"
        return f"{'+'.join(self.strategies)}|n{self.n}|{size}|mc{int(self.min_marcap)}|{self.schedule}"

    def to_dict(self) -> dict:
        return {"key": self.id, "strategies": list(self.strategies), "n": self.n, "size": self.size,
                "size_cut": self.cut, "min_marcap": self.min_marcap, "schedule": self.schedule}

    @classmethod
    def parse(cls, key: str) -> GridKey:
        strat, n, size, mc, sched = key.split("|")
        if size == "all":
            s, cut = "all", None
        else:
            s = "small" if size.startswith("small") else "large"
            cut = float(size[len(s):])
        return cls(tuple(strat.split("+")), int(n[1:]), s, cut, float(mc[2:]), sched)


def load_grid_config() -> dict:
    return yaml.safe_load(resources.files("kquant.backtest").joinpath("grid.yaml").read_text(encoding="utf-8"))


def grid_keys(cfg: dict | None = None) -> list[GridKey]:
    cfg = cfg or load_grid_config()
    base = cfg["strategies"]
    max_r = len(base) if cfg.get("combinations", False) else 1
    combos = [tuple(c) for r in range(1, max_r + 1) for c in itertools.combinations(sorted(base), r)]
    keys = []
    for combo, n, sz, mc, sched in itertools.product(combos, cfg["n"], cfg["size"], cfg["min_marcap"],
                                                     cfg["schedule"]):
        keys.append(GridKey(combo, int(n), sz["size"], sz.get("cut"), float(mc), sched))
    return keys


def fingerprint(paths, cfg: dict) -> str:
    h = hashlib.sha256()
    for f in (paths.fin_long, paths.adj_events):
        h.update(f.read_bytes() if f.exists() else b"-")
    pkg = Path(__file__).resolve().parents[1]
    for rel in CODE_FILES:
        h.update(rel.encode())
        h.update((pkg / rel).read_bytes())
    h.update(json.dumps(cfg, sort_keys=True).encode())
    return h.hexdigest()[:16]


@dataclass
class PeriodCandidates:
    decision_date: pd.Timestamp
    ranked: dict[tuple, pd.DataFrame]  # (UniverseParams, combo) → 조합 순위
    universe_size: dict[UniverseParams, int]
    abnormal: pd.DataFrame  # 보유 구간 이상일 (Code, Date, ret) — 선정에는 쓰지 않음
    tradable: set[str]
    buy_close: pd.Series


def period_candidates(ctx, period: Period, keys: list[GridKey]) -> PeriodCandidates:
    d = ctx.prices.last_trading_day(period.rebalance_date - pd.Timedelta(days=1))
    prices_d = ctx.prices.cross_section(d)
    snap = ctx.snapshot(d)
    base, _ = build_universe(prices_d, snap)
    start = d - pd.DateOffset(months=LOOKBACK_MONTHS) - pd.Timedelta(days=START_TOLERANCE_DAYS)
    hist = ctx.prices.range(start, d)
    mom = add_momentum(base, hist[["Date", "Code", "adj_close"]], d).set_index("symbol")
    ranked, usize = {}, {}
    needed = sorted({s for k in keys for s in k.strategies})
    for up in {k.universe for k in keys}:
        u, _ = build_universe(prices_d, snap, up)
        for c in ("mom_start_date", "mom_start_price", "mom_6m"):
            u[c] = u["symbol"].map(mom[c])
        usize[up] = len(u)
        single = rank_strategies(u, needed)
        for combo in {k.strategies for k in keys if k.universe == up}:
            ranked[(up, combo)] = combine({s: single[s] for s in combo})
    window = ctx.prices.range(period.rebalance_date, period.end_date)
    ab = abnormal_days(window[["Date", "Code", "adj_close"]])
    ab = ab[ab["Date"] > period.rebalance_date]
    reb = ctx.prices.cross_section(period.rebalance_date)
    traded = reb[reb["Volume"] > 0]
    return PeriodCandidates(d, ranked, usize, ab, set(traded["Code"]),
                            reb.set_index("Code")["Close"].astype(float))


def pick(c: PeriodCandidates, key: GridKey) -> tuple[list[str], list[dict]]:
    r = c.ranked[(key.universe, key.strategies)]
    picks, excluded = [], []
    for sym in r["symbol"]:
        if len(picks) >= key.n:
            break
        if sym not in c.tradable:
            excluded.append({"symbol": sym, "reason": "no_trade_on_rebalance"})
        else:
            picks.append(sym)
    return picks, excluded


def abnormal_held(c: PeriodCandidates, picks: list[str]) -> list[dict]:
    a = c.abnormal[c.abnormal["Code"].isin(picks)].sort_values(["Date", "Code"])
    return [{"symbol": r.Code, "date": str(r.Date.date()), "ret": round(float(r.ret), 4)} for r in a.itertuples()]


def settlement(ctx, p: Period, syms: set[str], win: pd.DataFrame) -> dict[str, dict]:
    """기간 말에 거래가 없는 보유 후보의 정산 정보. 종료된 기간에만 쓴다.

    status: resumed(종료 후 첫 거래일 가격), pending(최신일까지 아직 정지 — 잠정적으로 마지막 가격, 나중에 재계산),
    delisted(종료 후 행 없음 — 마지막 거래 가격).
    """
    w = win[win["Code"].isin(syms)]
    on_end = set(w[(w["Date"] == p.end_date) & (w["Volume"] > 0)]["Code"])
    halted = syms - on_end
    if not halted:
        return {}
    traded = w[w["Volume"] > 0].sort_values("Date").groupby("Code", observed=True).tail(1).set_index("Code")
    found, seen = ctx.prices.first_trade_after(halted, p.end_date)
    out = {}
    for s in halted:
        last_date = traded.loc[s, "Date"] if s in traded.index else None
        last_adj = float(traded.loc[s, "adj_close"]) if s in traded.index else float("nan")
        if s in found:
            d, a = found[s]
            out[s] = {"status": "resumed", "last_trade": last_date, "exit_date": d, "exit_adj": a,
                      "last_adj": last_adj}
        else:
            out[s] = {"status": "pending" if s in seen else "delisted", "last_trade": last_date,
                      "exit_date": None, "exit_adj": last_adj, "last_adj": last_adj}
    return out


def _load(out_dir: Path):
    meta_f = out_dir / "meta.json"
    if not meta_f.exists():
        return None, None, None
    return (json.loads(meta_f.read_text(encoding="utf-8")), pd.read_parquet(out_dir / "periods.parquet"),
            pd.read_parquet(out_dir / "daily.parquet"))


def run_grid(ctx, out_dir: Path, full: bool = False, only: list[str] | None = None, log=print) -> dict:
    """그리드 전체(또는 ``only`` 키들)를 계산해 ``out_dir``에 저장하고 요약을 돌려준다."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_grid_config()
    keys = grid_keys(cfg)
    if only:
        keys = [k for k in keys if k.id in set(only)]
    fp = fingerprint(ctx.paths, cfg)
    meta, old_periods, old_daily = (None, None, None) if full else _load(out_dir)
    reuse = meta is not None and meta.get("fingerprint") == fp
    latest = ctx.prices.latest_date()
    days = ctx.prices.trading_days(pd.Timestamp(year=FIRST_YEAR, month=1, day=1), latest)
    capital0 = float(cfg["initial_capital"])
    buy_rate = float(cfg.get("buy_cost_rate", 0.0))
    sell_rate = float(cfg.get("sell_cost_rate", 0.0))
    t0 = time.time()

    if reuse:
        old_idx = {(r.key, r.period_id): i for i, r in enumerate(old_periods.itertuples())}
        old_daily_g = dict(tuple(old_daily.groupby(["key", "period_id"])))
    period_rows, daily_parts = [], []
    stats = {"computed": 0, "reused": 0}
    for sched in sorted({k.schedule for k in keys}):
        skeys = [k for k in keys if k.schedule == sched]
        capital = {k.id: capital0 for k in skeys}
        chain_ok = {k.id: True for k in skeys}
        for p in periods(sched, days):
            todo = []
            for k in skeys:
                i = old_idx.get((k.id, p.id)) if reuse and chain_ok[k.id] and p.closed else None
                pending = (bool(old_periods["pending"].iloc[i])
                           if i is not None and "pending" in old_periods else False)
                if i is not None and bool(old_periods["closed"].iloc[i]) and not pending:
                    row = old_periods.iloc[i].to_dict()
                    period_rows.append(row)
                    daily_parts.append(old_daily_g[(k.id, p.id)])
                    capital[k.id] = float(row["end_value"])
                    stats["reused"] += 1
                else:
                    chain_ok[k.id] = False
                    todo.append(k)
            if not todo:
                continue
            cand = period_candidates(ctx, p, todo)
            win = ctx.prices.range(p.rebalance_date, p.end_date)
            adj_all = win.pivot_table(index="Date", columns="Code", values="adj_close", observed=True)
            adj_all = adj_all.reindex(days[(days >= p.rebalance_date) & (days <= p.end_date)])
            chosen = {k.id: pick(cand, k) for k in todo}
            held = {s for syms, _ in chosen.values() for s in syms}
            settle = settlement(ctx, p, held, win) if p.closed else {}
            for k in todo:
                syms, excl = chosen[k.id]
                st = {s: settle[s] for s in syms if s in settle}
                exit_rel = pd.Series({s: v["exit_adj"] / adj_all[s].iloc[0] for s, v in st.items()}, dtype=float)
                res = simulate_period(capital[k.id], cand.buy_close.reindex(syms),
                                      adj_all.reindex(columns=syms), buy_rate,
                                      sell_rate if p.closed else 0.0,  # 진행 중 기간은 아직 팔지 않았다(2026-11-10)
                                      exit_rel)
                start, end = capital[k.id], float(res.values.iloc[-1])
                period_rows.append({
                    "key": k.id, "schedule": sched, "period_id": p.id, "begin": p.begin, "end": p.end,
                    "rebalance_date": p.rebalance_date, "decision_date": cand.decision_date,
                    "end_date": p.end_date, "closed": p.closed, "start_value": start, "end_value": end,
                    "ret": end / start - 1, "picks": ",".join(syms), "n_picks": len(syms),
                    "excluded": json.dumps(excl, ensure_ascii=False),
                    "abnormal_held": json.dumps(abnormal_held(cand, syms), ensure_ascii=False),
                    "universe_size": cand.universe_size[k.universe], "cash_start": res.cash,
                    "settled": json.dumps([{
                        "symbol": s, "status": v["status"],
                        "last_trade": None if v["last_trade"] is None else str(v["last_trade"].date()),
                        "exit_date": None if v["exit_date"] is None else str(v["exit_date"].date()),
                        "ret_vs_last": (round(v["exit_adj"] / v["last_adj"] - 1, 4)
                                        if v["last_adj"] == v["last_adj"] else None),
                    } for s, v in st.items()], ensure_ascii=False),
                    "pending": any(v["status"] == "pending" for v in st.values()),
                })
                daily_parts.append(pd.DataFrame({"key": k.id, "period_id": p.id, "date": res.values.index,
                                                 "value": res.values.to_numpy()}))
                capital[k.id] = end
                stats["computed"] += 1
            log(f"[{sched}] {p.id} 계산 {len(todo)}키 ({time.time() - t0:.0f}s)")

    periods_df = pd.DataFrame(period_rows)
    daily_df = pd.concat(daily_parts, ignore_index=True) if daily_parts else pd.DataFrame(
        columns=["key", "period_id", "date", "value"])
    if only and reuse:
        periods_df = pd.concat([old_periods[~old_periods["key"].isin(only)], periods_df], ignore_index=True)
        daily_df = pd.concat([old_daily[~old_daily["key"].isin(only)], daily_df], ignore_index=True)
    bench = market_index(ctx.prices.range(days[0], latest))
    periods_df.to_parquet(out_dir / "periods.parquet", index=False)
    daily_df.to_parquet(out_dir / "daily.parquet", index=False)
    bench.rename_axis("date").reset_index().to_parquet(out_dir / "benchmark.parquet", index=False)
    meta = {"fingerprint": fp, "latest_date": str(latest.date()), "keys": int(periods_df["key"].nunique()),
            "buy_cost_rate": buy_rate, "sell_cost_rate": sell_rate, "elapsed_sec": round(time.time() - t0, 1), **stats}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def load_result(out_dir: Path, key: str) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    """(일별 총액 — 기간 사이 현금 구간은 직전 값 유지, 기간 기록, 벤치마크 지수)."""
    out_dir = Path(out_dir)
    per = pd.read_parquet(out_dir / "periods.parquet")
    per = per[per["key"] == key].sort_values("rebalance_date").reset_index(drop=True)
    if per.empty:
        raise KeyError(f"그리드에 없는 키: {key}")
    daily = pd.read_parquet(out_dir / "daily.parquet", filters=[("key", "==", key)])
    bench = pd.read_parquet(out_dir / "benchmark.parquet").set_index("date")
    s = daily.sort_values("date").drop_duplicates("date", keep="last").set_index("date")["value"]
    days = bench.index[bench.index >= s.index[0]]
    s = s.reindex(days.union(s.index)).ffill()
    return s, per, bench.loc[bench.index >= s.index[0]]
