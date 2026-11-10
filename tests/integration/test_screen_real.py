import pytest

from kquant.catalog.screen import screen

pytestmark = pytest.mark.data


def test_largest_caps_include_samsung(real_ctx):
    r = screen({"sort": {"field": "market_cap"}, "limit": 5}, real_ctx)
    assert r["ok"] and "005930" in {x["symbol"] for x in r["rows"]}


def test_past_as_of_uses_only_available_fin(real_ctx):
    r = screen({"as_of": "2024-08-13", "where": [{"field": "revenue", "period": "ttm", "op": ">", "value": 0}],
                "fields": [{"field": "revenue_growth", "period": "ttm"}], "limit": 100}, real_ctx)
    assert r["ok"] and r["as_of"] == "2024-08-13"
    # 2024 2Q 보고서의 법정 기한(8/14) 전이므로 6월 결산 값이 보이면 안 된다
    assert all(x["fin_period_end"] < "2024-06-30" for x in r["rows"])
