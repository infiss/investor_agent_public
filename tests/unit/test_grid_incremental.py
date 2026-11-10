"""그리드 증분 = 전체 재계산 (P5 완료 기준 3). 합성 데이터셋(종목 6개, 2016–2019)으로 검증한다."""

import numpy as np
import pandas as pd
import pytest

from kquant.backtest import grid as grid_mod
from kquant.backtest.grid import load_result, run_grid
from kquant.config import Paths
from kquant.context import DataContext

SYMS = ["000010", "000020", "000030", "000040", "000050", "000060"]
SMALL_GRID = {"strategies": ["value", "momentum"], "n": [2], "size": [{"size": "all"}], "min_marcap": [0],
              "schedule": ["annual", "quarterly"], "initial_capital": 10_000_000, "sell_cost_rate": 0.002}


def _long() -> pd.DataFrame:
    rows = []
    for si, sym in enumerate(SYMS):
        for y in range(2016, 2020):
            ytd = {"매출액": 0, "지배당기순이익": 0, "영업활동현금흐름": 0, "영업이익": 0}
            for q in range(1, 5):
                pe = pd.Timestamp(year=y, month=3 * q, day=1) + pd.offsets.MonthEnd(0)
                fq = f"{q}Q"
                base = {"symbol": sym, "corp_name": f"회사{si}", "fiscal_year": y, "fiscal_q": fq,
                        "fiscal_month": 12, "period_end": pe,
                        "available_date": pe + pd.Timedelta(days=90 if q == 4 else 45),
                        "statement_type": "CFS", "currency": "KRW", "source_file": "synthetic"}
                flows = {"매출액": 100 + 10 * si + q, "지배당기순이익": 5 + si + (y - 2016),
                         "영업활동현금흐름": 7 + si, "영업이익": 6 + si * (y - 2015)}
                for item, v in flows.items():
                    v = int(v * 1e8)
                    ytd[item] += v
                    rows.append({**base, "item": item, "basis": "3M", "value": v})
                    rows.append({**base, "item": item, "basis": "FY" if q == 4 else "YTD", "value": ytd[item]})
                for item, v in {"자본총계": 1000 + si, "지배자본총계": 1000 + si, "자본금": 50}.items():
                    rows.append({**base, "item": item, "basis": "instant", "value": int(v * 1e8)})
    df = pd.DataFrame(rows)
    df["period_end"] = df["period_end"].astype("datetime64[us]")
    df["available_date"] = df["available_date"].astype("datetime64[us]")
    return df


def _prices(end: str) -> dict[int, pd.DataFrame]:
    days = pd.bdate_range("2017-01-02", "2019-06-28")
    rng = np.random.default_rng(7)
    frames = []
    for si, sym in enumerate(SYMS):
        close = np.round(1000 * np.cumprod(1 + rng.normal(0.0005 * si, 0.01, len(days))))
        df = pd.DataFrame({"Date": days, "Code": sym, "Name": f"회사{si}", "Market": "KOSPI", "Close": close})
        df["Changes"] = df["Close"].diff().fillna(0.0)
        df["Volume"] = 1000.0
        df["Amount"] = df["Close"] * 1000
        df["Stocks"] = 10_000_000
        df["Marcap"] = df["Close"] * df["Stocks"]
        frames.append(df)
    allp = pd.concat(frames, ignore_index=True)
    allp = allp[allp["Date"] <= end]
    return {y: g.reset_index(drop=True) for y, g in allp.groupby(allp["Date"].dt.year)}


def _write(tmp, end):
    (tmp / "fin").mkdir(exist_ok=True)
    _long().to_parquet(tmp / "fin" / "long.parquet", index=False)
    for y, g in _prices(end).items():
        g.to_parquet(tmp / f"marcap-{y}.parquet", index=False)


@pytest.fixture
def small_grid(monkeypatch):
    monkeypatch.setattr(grid_mod, "load_grid_config", lambda: SMALL_GRID)


def test_incremental_equals_full(tmp_path, small_grid):
    data = tmp_path / "data"
    data.mkdir()
    _write(data, "2019-06-14")
    out_inc = tmp_path / "inc"
    m1 = run_grid(DataContext(Paths(data)), out_inc, full=True, log=lambda *a: None)
    assert m1["computed"] > 0 and m1["reused"] == 0

    _write(data, "2019-06-28")  # 가격 2주 추가 → 진행 중 기간(annual 2019-03, quarterly 2019-05)만 다시 계산
    m2 = run_grid(DataContext(Paths(data)), out_inc, log=lambda *a: None)
    assert m2["reused"] > 0 and m2["computed"] == 2 * 2  # 일정 2개 × 단일 전략 키 2개

    out_full = tmp_path / "full"
    run_grid(DataContext(Paths(data)), out_full, full=True, log=lambda *a: None)
    for f in ("periods.parquet", "daily.parquet"):
        a = pd.read_parquet(out_inc / f).sort_values(["key", "period_id"] + (["date"] if f == "daily.parquet" else []))
        b = pd.read_parquet(out_full / f).sort_values(a.columns[:2].tolist() + (["date"] if f == "daily.parquet" else []))
        pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True), check_dtype=False)


def test_capital_chain_and_identity(tmp_path, small_grid):
    data = tmp_path / "data"
    data.mkdir()
    _write(data, "2019-06-28")
    out = tmp_path / "g"
    run_grid(DataContext(Paths(data)), out, full=True, log=lambda *a: None)
    s, per, bench = load_result(out, "value|n2|all|mc0|quarterly")
    # 기간 종료 총액 = 다음 기간 시작 자금, 기간 마지막 평가일(다음 판단일) 값 = 종료 총액
    assert (per["start_value"].iloc[1:].to_numpy() == per["end_value"].iloc[:-1].to_numpy()).all()
    assert all(s.loc[r.end_date] == r.end_value for r in per.itertuples() if r.closed)
    assert per["rebalance_date"].iloc[0] == pd.Timestamp("2018-04-10") and len(per) == 6
    assert per["start_value"].iloc[0] == 10_000_000 and (per["n_picks"] == 2).all()
    assert set(bench.columns) == {"KOSPI"} and bench.index[0] == pd.Timestamp("2018-04-10")  # 전략 시작일에 맞춤
    assert {k.split("|")[0] for k in pd.read_parquet(out / "periods.parquet")["key"]} == {"value", "momentum"}
    # 매도세(0.2%)는 종료된 기간의 마지막 날에만: 진행 중 기간(2019-05)의 최신일 값에는 빼지 않는다
    from kquant.backtest import engine

    calls = []
    real = engine.simulate_period

    def spy(*a, **k):
        calls.append(a[4] if len(a) > 4 else k.get("sell_rate"))
        return real(*a, **k)

    grid_mod.simulate_period = spy
    try:
        run_grid(DataContext(Paths(data)), tmp_path / "g2", full=True, log=lambda *a: None)
    finally:
        grid_mod.simulate_period = real
    closed = pd.read_parquet(tmp_path / "g2" / "periods.parquet")["closed"].tolist()
    assert calls == [0.002 if c else 0.0 for c in closed] and False in closed
