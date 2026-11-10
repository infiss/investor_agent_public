import json

import pandas as pd
import pytest

from kquant.universe import build_universe

pytestmark = pytest.mark.data


def test_universe_keeps_later_delisted_stocks(real_ctx):
    """생존편향 점검(P5 완료 기준 4): 과거 기준일 유니버스에 이후 상장폐지된 종목이 남아 있어야 한다."""
    d = pd.Timestamp("2018-10-31")
    u, _ = build_universe(real_ctx.prices.cross_section(d), real_ctx.snapshot(d))
    alive = set(real_ctx.prices.cross_section(real_ctx.prices.latest_date())["Code"])
    gone = set(u["symbol"]) - alive
    assert len(gone) > 50, len(gone)


def test_stored_grid_chain(real_ctx):
    out = real_ctx.paths.grid_dir
    if not (out / "meta.json").exists():
        pytest.skip("그리드 결과 없음(kquant run-grid)")
    per = pd.read_parquet(out / "periods.parquet")
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert per["key"].nunique() == meta["keys"]
    for _, g in per.sort_values("rebalance_date").groupby("key"):
        assert (g["start_value"].iloc[1:].to_numpy() == g["end_value"].iloc[:-1].to_numpy()).all()
