"""실데이터 표본 손계산 대조 (P4 완료 기준 1). 스냅샷·유니버스를 거치지 않고 long·marcap 원본에서 다시 계산한다."""

import pandas as pd
import pytest

from kquant.strategies.select import select

pytestmark = pytest.mark.data
REB = "2025-11-03"


def _raw(real_ctx, symbol, item, basis, period_end):
    L = real_ctx.long
    r = L[(L.symbol == symbol) & (L.item == item) & (L.basis == basis) & (L.period_end == period_end)]
    assert len(r) == 1, (symbol, item, basis, period_end)
    return float(r["value"].iloc[0])


def test_value_top_pick_recomputed(real_ctx):
    s = select(real_ctx, REB, ["value"], 5)
    assert s.decision_date == pd.Timestamp("2025-10-31")
    top = s.ranked.iloc[0]
    sym = top["symbol"]
    # 2025-10-31에 쓸 수 있는 최신 보고서 = 2025 2Q(6/30, 8/14 공시). TTM = 2Q YTD + 2024 FY − 2024 2Q YTD
    ni = (_raw(real_ctx, sym, "지배당기순이익", "YTD", "2025-06-30") + _raw(real_ctx, sym, "지배당기순이익", "FY", "2024-12-31")
          - _raw(real_ctx, sym, "지배당기순이익", "YTD", "2024-06-30"))
    mc = pd.read_parquet(real_ctx.paths.marcap_file(2025), columns=["Date", "Code", "Marcap"])
    marcap = float(mc[(mc.Code == sym) & (pd.to_datetime(mc.Date) == "2025-10-31")]["Marcap"].iloc[0])
    from kquant.strategies.base import rank_strategies
    from kquant.universe import build_universe

    u, _ = build_universe(real_ctx.prices.cross_section(s.decision_date), real_ctx.snapshot(s.decision_date))
    v = rank_strategies(u, ["value"])["value"].set_index("symbol")
    assert v.loc[sym, "earnings_yield"] == pytest.approx(ni / marcap, rel=1e-12)


def test_salesmt_pick_recomputed(real_ctx):
    s = select(real_ctx, REB, ["salesmt"], 3)
    sym = s.picks.iloc[0]["symbol"]
    cur = _raw(real_ctx, sym, "영업이익", "3M", "2025-06-30")
    prev = _raw(real_ctx, sym, "영업이익", "3M", "2024-06-30")
    assert cur > 0 and prev > 0
    r = s.ranked.set_index("symbol")
    others = r[r.index != sym]
    assert (cur - prev) / prev > 0 and len(others) > 0


def test_momentum_pick_recomputed(real_ctx):
    s = select(real_ctx, REB, ["momentum"], 3)
    sym = s.picks.iloc[0]["symbol"]
    px = real_ctx.prices.range("2025-04-01", "2025-10-31")
    px = px[px.Code == sym].set_index("Date")["adj_close"]
    expected = px.loc["2025-10-31"] / px.loc[:"2025-04-30"].iloc[-1] - 1
    from kquant.strategies.momentum import add_momentum
    from kquant.universe import build_universe

    u, _ = build_universe(real_ctx.prices.cross_section(s.decision_date), real_ctx.snapshot(s.decision_date))
    u = add_momentum(u, real_ctx.prices.range("2025-03-01", "2025-10-31")[["Date", "Code", "adj_close"]],
                     s.decision_date)
    assert u.set_index("symbol").loc[sym, "mom_6m"] == pytest.approx(expected, rel=1e-12)
