"""레드팀(2026-11-10): 연·분기 일정의 실제 휴장일 처리, 기한 당일 판단의 look-ahead, 매도세, 연도 구간.

실데이터 테스트는 dataset/이 없으면 skip된다.
"""

import itertools

import numpy as np
import pandas as pd
import pytest

from kquant.backtest.engine import simulate_period
from kquant.backtest.schedule import periods

# (일정, period_id): (판단일, 매수일) — 기한 이후 첫 거래일 + 5거래일, 실제 휴장일 반영
KNOWN = {
    ("quarterly", "2018-03"): ("2018-04-09", "2018-04-10"),  # 03-31 토요일 → 04-02부터 셈
    ("quarterly", "2018-05"): ("2018-05-23", "2018-05-24"),  # 05-22 부처님오신날 건너뜀
    ("quarterly", "2020-08"): ("2020-08-24", "2020-08-25"),  # 08-17 임시공휴일 건너뜀
    ("quarterly", "2021-08"): ("2021-08-24", "2021-08-25"),  # 08-14 토요일, 08-16 대체공휴일
    ("quarterly", "2024-05"): ("2024-05-23", "2024-05-24"),  # 05-15 부처님오신날
    ("annual", "2020-03"): ("2020-04-07", "2020-04-08"),
}


def _days(ctx):
    return ctx.prices.trading_days(pd.Timestamp("2018-01-01"), ctx.prices.latest_date())


@pytest.mark.data
def test_known_holiday_dates_and_no_gaps(real_ctx):
    days = _days(real_ctx)
    for sched in ("annual", "quarterly"):
        ps = {p.id: p for p in periods(sched, days)}
        for (s, pid), (dec, buy) in KNOWN.items():
            if s == sched:
                p = ps[pid]
                assert days[days < p.rebalance_date][-1] == pd.Timestamp(dec), pid
                assert p.rebalance_date == pd.Timestamp(buy), pid
        ids = list(ps)
        for a, b in itertools.pairwise(ids):  # 기간 마지막 평가일 = 다음 판단일(겹침·공백 없음)
            assert ps[a].end_date == days[days < ps[b].rebalance_date][-1]


@pytest.mark.data
@pytest.mark.parametrize("pid", ["2021-03", "2022-11", "2023-05"])
def test_grid_candidates_ignore_future_fin(real_ctx, pid):
    from kquant.backtest.grid import grid_keys, period_candidates
    from kquant.context import DataContext

    days = _days(real_ctx)
    p = {x.id: x for x in periods("quarterly", days)}[pid]
    d = real_ctx.prices.last_trading_day(p.rebalance_date - pd.Timedelta(days=1))
    long = real_ctx.long.copy()
    assert (long["available_date"] == p.begin).any()  # 그 기한의 보고서가 있어야 의미 있는 테스트
    fut = long["available_date"] > d
    long.loc[fut, "value"] = np.random.default_rng(0).integers(-10**12, 10**12, fut.sum())
    bad = DataContext(real_ctx.paths)
    bad.__dict__["long"] = long
    bad.__dict__["prices"] = real_ctx.prices
    keys = grid_keys()
    a, b = period_candidates(real_ctx, p, keys), period_candidates(bad, p, keys)
    for k in a.ranked:
        pd.testing.assert_frame_equal(a.ranked[k].reset_index(drop=True), b.ranked[k].reset_index(drop=True))


def test_sell_cost_only_on_last_day():
    days = pd.bdate_range("2024-01-01", periods=5)
    buy = pd.Series({"A": 1000.0, "B": 500.0})
    adj = pd.DataFrame({"A": [10, 11, 12, 11, 13.0], "B": [5, 5, 6, 6, 4.0]}, index=days)
    a = simulate_period(1_000_000, buy, adj)
    b = simulate_period(1_000_000, buy, adj, sell_rate=0.002)
    assert (a.values.iloc[:-1] == b.values.iloc[:-1]).all()
    held_last = a.values.iloc[-1] - a.cash
    assert b.values.iloc[-1] == pytest.approx(a.values.iloc[-1] - held_last * 0.002)
