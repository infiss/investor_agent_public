"""레드팀: look-ahead (P4 완료 기준 3). 기준일 이후 정보를 망가뜨려도 선정 결과가 같아야 한다."""

import numpy as np
import pandas as pd
import pytest

from kquant.strategies.select import select

pytestmark = pytest.mark.data
REB = pd.Timestamp("2024-11-01")
ALL = ["value", "salesmt", "momentum"]


@pytest.fixture(scope="module")
def corrupted_ctx(real_ctx):
    from kquant.context import DataContext

    d = real_ctx.prices.last_trading_day(REB - pd.Timedelta(days=1))
    L = real_ctx.long.copy()
    future = L["available_date"] > d
    rng = np.random.default_rng(0)
    L.loc[future, "value"] = rng.integers(-10**12, 10**12, future.sum())  # 미래 공시값을 무작위로
    ctx = DataContext(real_ctx.paths)
    ctx.__dict__["long"] = L
    ctx.__dict__["prices"] = real_ctx.prices
    return ctx


@pytest.mark.parametrize("names", [["value"], ["salesmt"], ["momentum"], ALL])
def test_future_fin_values_do_not_change_selection(real_ctx, corrupted_ctx, names):
    a = select(real_ctx, REB, names, 20)
    b = select(corrupted_ctx, REB, names, 20)
    pd.testing.assert_frame_equal(a.ranked, b.ranked)


def test_selection_reads_no_prices_after_decision_date(real_ctx, monkeypatch):
    seen = []
    store = real_ctx.prices
    orig_cs, orig_range = store.cross_section, store.range
    monkeypatch.setattr(store, "cross_section", lambda d: seen.append(pd.Timestamp(d)) or orig_cs(d))
    monkeypatch.setattr(store, "range", lambda s, e: seen.append(pd.Timestamp(e)) or orig_range(s, e))
    s = select(real_ctx, REB, ALL, 20)
    assert seen and max(seen) <= s.decision_date < REB
