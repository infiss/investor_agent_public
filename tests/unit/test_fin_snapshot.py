import numpy as np
import pandas as pd

from kquant.fin.snapshot import snapshot

LAG = {"1Q": 45, "2Q": 45, "3Q": 45, "4Q": 90}


def _company(symbol: str, fy_end_month: int, n_years: int = 2, start_year: int = 2024, skip=()) -> list[dict]:
    """분기 매출 3M = 100·(분기 순번), YTD·FY 누적, 현금흐름은 YTD/FY만, 자산총계 instant = 1000+순번."""
    rows = []
    k = 0
    for y in range(n_years):
        ytd = cf = 0
        for qi in range(1, 5):
            k += 1
            pe = pd.Timestamp(year=start_year + y, month=fy_end_month, day=1) + pd.offsets.MonthEnd(0)
            pe = (pe - pd.DateOffset(months=3 * (4 - qi))) + pd.offsets.MonthEnd(0)
            fq = f"{qi}Q"
            if (start_year + y, fq) in skip:
                continue
            rev = 100 * k
            ytd += rev
            cf += 10 * k
            base = {"symbol": symbol, "corp_name": symbol, "currency": "KRW", "statement_type": "CFS",
                        "fiscal_year": start_year + y, "fiscal_q": fq, "period_end": pe,
                        "available_date": pe + pd.Timedelta(days=LAG[fq])}
            flow_basis = "FY" if qi == 4 else "YTD"
            rows += [
                {**base, "item": "매출액", "basis": "3M", "value": rev},
                {**base, "item": "매출액", "basis": flow_basis, "value": ytd},
                {**base, "item": "영업활동현금흐름", "basis": flow_basis, "value": cf},
                {**base, "item": "자산총계", "basis": "instant", "value": 1000 + k},
            ]
    return rows


def _long(*companies) -> pd.DataFrame:
    df = pd.DataFrame([r for c in companies for r in c])
    df["period_end"] = df["period_end"].astype("datetime64[us]")
    df["available_date"] = df["available_date"].astype("datetime64[us]")
    return df


def test_december_fy_values_and_lookahead():
    long = _long(_company("A", 12))  # 2024 1Q(k=1) … 2025 4Q(k=8)
    s = snapshot(long, "2025-08-14").set_index("symbol").loc["A"]  # 2025 2Q(k=6) 공시일
    assert s["period_end"] == pd.Timestamp("2025-06-30") and s["fiscal_q"] == "2Q"
    assert s["매출액_q"] == 600
    assert s["매출액_ttm"] == 300 + 400 + 500 + 600
    assert s["매출액_fy"] == 100 + 200 + 300 + 400
    assert s["매출액_q_1y"] == 200
    assert np.isnan(s["매출액_ttm_1y"])  # 2023 FY가 없어 계산 불가
    assert s["영업활동현금흐름_q"] == 60  # YTD 차분
    assert s["영업활동현금흐름_ttm"] == 30 + 40 + 50 + 60
    assert s["자산총계"] == 1006 and s["자산총계_1y"] == 1002

    before = snapshot(long, "2025-08-13").set_index("symbol").loc["A"]  # 하루 전에는 1Q가 최신
    assert before["period_end"] == pd.Timestamp("2025-03-31")


def test_q4_uses_fy():
    long = _long(_company("A", 12))
    s = snapshot(long, "2026-03-31").set_index("symbol").loc["A"]
    assert s["fiscal_q"] == "4Q"
    assert s["매출액_ttm"] == s["매출액_fy"] == 500 + 600 + 700 + 800
    assert s["매출액_q"] == 800
    assert s["매출액_ttm_1y"] == 100 + 200 + 300 + 400


def test_march_fiscal_year():
    long = _long(_company("B", 3))  # 결산월 3월: 1Q 말 = 6월
    s = snapshot(long, "2025-02-14").set_index("symbol").loc["B"]  # 2025 회계연도 3Q(2024-12-31, k=7)
    assert s["period_end"] == pd.Timestamp("2024-12-31") and s["fiscal_q"] == "3Q"
    assert s["매출액_ttm"] == 400 + 500 + 600 + 700
    assert s["매출액_fy"] == 100 + 200 + 300 + 400


def test_missing_quarter_gives_nan_not_stale_value():
    long = _long(_company("C", 12, skip={(2024, "2Q")}))
    s = snapshot(long, "2025-08-14").set_index("symbol").loc["C"]
    assert np.isnan(s["매출액_ttm"])  # 전년 동기 YTD(2024 2Q)가 없다
    assert np.isnan(s["매출액_q_1y"])
    assert s["매출액_q"] == 600


def test_nothing_available():
    long = _long(_company("A", 12))
    assert snapshot(long, "2024-01-01").empty
