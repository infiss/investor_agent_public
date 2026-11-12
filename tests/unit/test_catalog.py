import math

import pandas as pd
import pytest

from kquant.catalog.amount import AmountError, parse_number
from kquant.catalog.catalog import list_fields, load_catalog, resolve
from kquant.catalog.features import RATIOS
from kquant.catalog.screen import screen


@pytest.mark.parametrize("text,unit,expected", [
    ("2000억", "KRW", 2e11), ("2,000억원", "KRW", 2e11), ("1조 5000억", "KRW", 1.5e12), ("1.5조", "KRW", 1.5e12),
    ("300만", "KRW", 3e6), ("-50억", "KRW", -5e9), ("1,000", "KRW", 1000), ("15%", "%", 15), ("1.2배", "배", 1.2),
    (10, "배", 10), (2.5, "%", 2.5),
])
def test_parse_number(text, unit, expected):
    assert math.isclose(parse_number(text, unit), expected)


@pytest.mark.parametrize("text,unit", [
    ("억", "KRW"), ("100억조", "KRW"), ("10억 abc", "KRW"), ("15억", "%"), ("", "KRW"), (True, "KRW"),
    (float("nan"), "배"), (float("inf"), "KRW"), (None, "KRW"), ([1], "KRW"), ("1" * 50, "KRW"), ("1e5", "KRW"),
])
def test_parse_number_rejects(text, unit):
    with pytest.raises(AmountError):
        parse_number(text, unit)


def test_catalog_integrity():
    cat = load_catalog()
    for f in cat.values():
        if f.kind == "ratio":
            assert f.id in RATIOS and f.formula, f.id
        if f.kind in ("flow", "stock"):
            assert f.item, f.id
        if f.kind == "price":
            assert f.column, f.id
    assert len(list_fields()) == len(cat)
    assert resolve("시총").id == "market_cap" and resolve("Market Cap").id == "market_cap"
    assert resolve("없는항목") is None


def test_screen_basic(ctx):
    r = screen({"where": [{"field": "market_cap", "op": ">=", "value": "1000억"},
                          {"field": "per", "op": "<=", "value": 10}],
                "sort": {"field": "per", "order": "asc"}}, ctx)
    assert r["ok"], r
    # PER: 000001 = 500/100 = 5, 000003 = 2000/200 = 10, 000002는 적자라 PER 없음
    assert [(x["symbol"], x["per.ttm"]) for x in r["rows"]] == [("000001", 5.0), ("000003", 10.0)]
    assert r["rows"][0]["market_cap.latest"] == 500_000_000_000
    assert "투자 권유가 아닙니다" in r["disclaimer"]


def test_screen_defaults_exclude_konex_and_non_krw(ctx):
    r = screen({"where": [{"field": "revenue", "period": "ttm", "op": ">", "value": 0}]}, ctx)
    symbols = {x["symbol"] for x in r["rows"]}
    assert symbols == {"000001", "000002", "000003"}  # 000004(USD) 재무 비움, 000005(KONEX) 기본 제외
    r = screen({"market": ["KONEX"], "where": [{"field": "revenue", "period": "ttm", "op": ">", "value": 0}]}, ctx)
    assert [x["symbol"] for x in r["rows"]] == ["000005"]


def test_screen_ratios(ctx):
    r = screen({"fields": [{"field": "revenue_growth", "period": "ttm"}, "roe", "debt_ratio", "pbr"],
                "sort": {"field": "market_cap"}, "limit": 5}, ctx)
    row = {x["symbol"]: x for x in r["rows"]}
    assert row["000001"]["revenue_growth.ttm"] == 25.0  # (1000-800)/800
    assert row["000002"]["revenue_growth.ttm"] == -16.67
    assert row["000001"]["roe.ttm"] == 25.0 and row["000001"]["debt_ratio.latest"] == 50.0
    assert row["000001"]["pbr.latest"] == 1.25
    assert row["000004"]["roe.ttm"] is None  # 비KRW


def test_screen_between_and_errors(ctx):
    r = screen({"where": [{"field": "market_cap", "op": "between", "value": ["500억", "1조"]}]}, ctx)
    assert {x["symbol"] for x in r["rows"]} == {"000001", "000002", "000004"}  # 양 끝 포함(000004 = 500억)
    cases = [
        ({"where": [{"field": "revenue", "op": ">", "value": 1}]}, "period_required"),
        ({"where": [{"field": "revenue", "period": "weekly", "op": ">", "value": 1}]}, "unsupported_period"),
        ({"where": [{"field": "pbr", "op": "LIKE", "value": 1}]}, "bad_operator"),
        ({"where": [{"field": "pbr", "op": ">", "value": "열"}]}, "bad_value"),
        ({"where": [{"field": "market_cap", "op": "between", "value": ["1조", "500억"]}]}, "bad_value"),
        ({"market": ["NYSE"]}, "bad_market"),
        ({"limit": 1000}, "bad_limit"),
        ({"sort": {"field": "per", "order": "up"}}, "bad_sort"),
        ({"as_of": "2015-01-01"}, "bad_as_of"),
        ({"select": "*"}, "unknown_key"),
        ([], "invalid_query"),
    ]
    for q, code in cases:
        out = screen(q, ctx)
        assert out["ok"] is False and out["problem"]["code"] == code, (q, out)


def test_screen_period_location_and_inherit(ctx):
    """2026-11-12 Hermes E2E: fields에 기간 없는 'revenue'가 있어 period_required가 났지만 위치가 없어
    에이전트가 where만 20번 가까이 고쳤다. 오류에 위치를 넣고, where에 한 기간으로 쓴 항목은 sort·fields가 그 기간을 쓴다."""
    out = screen({"where": [{"field": "pbr", "op": ">", "value": 0}], "fields": ["market_cap", "revenue"]}, ctx)
    assert out["ok"] is False and out["problem"]["code"] == "period_required"
    assert out["problem"]["location"] == "fields[1]" and out["problem"]["message"].startswith("fields[1]:")

    out = screen({"where": [{"field": "revenue", "op": ">", "value": 1}]}, ctx)
    assert out["problem"]["location"] == "where[0]"  # where에는 상속 없음 — 지금처럼 되묻는다
    out = screen({"sort": {"field": "revenue"}}, ctx)
    assert out["problem"]["location"] == "sort"
    out = screen({"where": [{"field": "revenue", "period": "weekly", "op": ">", "value": 1}]}, ctx)
    assert out["problem"]["code"] == "unsupported_period" and out["problem"]["location"] == "where[0]"

    r = screen({"where": [{"field": "revenue", "period": "ttm", "op": ">=", "value": 1}],
                "fields": ["revenue"], "sort": {"field": "revenue"}}, ctx)
    assert r["ok"], r
    assert [c["key"] for c in r["columns"]] == ["revenue.ttm"]

    # where에 같은 항목이 두 기간으로 있으면 어느 쪽인지 모호 → 상속하지 않는다
    out = screen({"where": [{"field": "revenue", "period": "ttm", "op": ">=", "value": 1},
                            {"field": "revenue", "period": "quarter", "op": ">=", "value": 1}],
                  "fields": ["revenue"]}, ctx)
    assert out["ok"] is False and out["problem"]["location"] == "fields[0]"


def test_format_value():
    from kquant.catalog.amount import format_value, parse_number

    assert format_value(55953285300000, "KRW") == "55.95조"  # 2026-11-13: 모델이 "559.5조"로 옮긴 값
    assert format_value(186254472000000, "KRW") == "186.25조"
    assert format_value(648e9, "KRW") == "6,480억" and format_value(1e9, "KRW") == "10억"
    assert format_value(-2.5e12, "KRW") == "-2.50조" and format_value(35e6, "KRW") == "3,500만"
    assert format_value(3500, "KRW") == "3,500원"
    assert format_value(5.92, "배") == "5.92배" and format_value(12.345, "%") == "12.35%"
    assert format_value(None, "KRW") is None and format_value(float("nan"), "KRW") is None
    for v in (55953285300000, 648e9, 1e9):  # 표시값을 다시 읽으면 원값과 1% 안
        assert abs(parse_number(format_value(v, "KRW"), "KRW") / v - 1) < 0.01


def test_screen_display_and_annual_end(ctx):
    r = screen({"where": [{"field": "revenue", "period": "ttm", "op": ">=", "value": "1000억"}],
                "fields": ["market_cap", {"field": "per", "period": "ttm"}], "sort": {"field": "market_cap"}}, ctx)
    row = {x["symbol"]: x for x in r["rows"]}["000001"]
    assert row["display"] == {"revenue.ttm": "1.00조", "market_cap.latest": "5,000억", "per.ttm": "5.00배"}
    assert "fin_annual_period_end" not in row  # annual 열이 없으면 넣지 않는다

    from kquant.catalog.screen import _annual_end

    assert _annual_end(pd.Timestamp("2026-06-30"), "2Q") == "2025-12-31"
    assert _annual_end(pd.Timestamp("2026-09-30"), "3Q") == "2025-12-31"
    assert _annual_end(pd.Timestamp("2026-03-31"), "4Q") == "2026-03-31"  # 3월 결산 사업보고서
    assert _annual_end(pd.Timestamp("2026-06-30"), None) is None
    r = screen({"fields": [{"field": "revenue", "period": "annual"}], "limit": 1}, ctx)
    assert r["rows"][0]["fin_annual_period_end"] == "2025-12-31"


def test_screen_rows_json_serializable_without_financials(ctx):
    """2026-11-10 Hermes E2E: 조건 없이 시총 정렬하면 재무 없는 종목(fin_period_end = NaT)이 들어와 JSON 변환이 실패했다."""
    import json

    import pandas as pd

    from kquant.catalog.screen import _jsonable

    assert _jsonable(pd.NaT) is None
    r = screen({"fields": ["market_cap"], "sort": {"field": "market_cap", "order": "desc"}, "limit": 5}, ctx)
    assert r["ok"], r
    json.dumps(r, ensure_ascii=False)
