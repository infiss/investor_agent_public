import pandas as pd

from kquant.fin.quality import apply_scale_fix, detect_scale_errors

BS = ["자산총계", "자본총계", "부채총계", "유동자산", "자본금", "이익잉여금"]
ENDS = pd.to_datetime(["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31", "2024-03-31"])


def _raw(scale: dict | None = None, symbol: str = "000001") -> pd.DataFrame:
    """분기마다 재무상태표 6개 + 매출액 3M 1개. scale = {분기 index: 배수}."""
    scale = scale or {}
    rows = []
    for i, pe in enumerate(ENDS):
        f = scale.get(i, 1)
        for j, it in enumerate(BS):
            v = (1000 + 10 * i + j) * 1_000_000
            rows.append((symbol, pe, it, "instant", int(v * f) if f >= 1 else v // int(1 / f)))
        rows.append((symbol, pe, "매출액", "3M", int(300_000_000 * f) if f >= 1 else 300))
    df = pd.DataFrame(rows, columns=["symbol", "period_end", "item", "basis", "value"])
    return df.assign(corp_name="테스트", fiscal_year=df["period_end"].dt.year, fiscal_q="1Q")


def test_middle_filing_x1000_corrected():
    raw = _raw({2: 1000})
    flags = detect_scale_errors(raw)
    assert flags[["status", "k", "method"]].values.tolist() == [["corrected", 3, "both_neighbors"]]
    fixed = apply_scale_fix(raw, flags)
    pd.testing.assert_series_equal(fixed["value"], _raw()["value"])


def test_divided_filing_scaled_up():
    raw = _raw({1: 1e-6})
    flags = detect_scale_errors(raw)
    assert flags["k"].tolist() == [-6]
    fixed = apply_scale_fix(raw, flags)
    got = fixed[(fixed.period_end == ENDS[1]) & (fixed.item == "자산총계")].value.item()
    assert got == 1010 * 1_000_000


def test_edge_filing_needs_marcap():
    raw = _raw({4: 1000})
    flags = detect_scale_errors(raw)
    assert flags[["status", "method"]].values.tolist() == [["review", "edge_unconfirmed"]]
    assert apply_scale_fix(raw, flags).equals(raw)

    keys = raw[["symbol", "period_end"]].drop_duplicates()
    mc = keys.assign(marcap=2_000_000_000.0)  # 자산/시총 ≈ 0.5, 마지막만 ≈ 500
    flags = detect_scale_errors(raw, mc)
    assert flags[["status", "method", "k"]].values.tolist() == [["corrected", "edge_marcap", 3]]


def test_real_growth_not_flagged():
    raw = _raw({3: 5, 4: 5})  # 실제 5배 성장(수준 이동)은 오기입이 아님
    assert detect_scale_errors(raw).empty


def test_large_values_exact():
    raw = _raw({2: 1_000_000})
    raw.loc[(raw.period_end == ENDS[2]) & (raw.item == "자산총계"), "value"] = 842_787_340_982_000_000
    fixed = apply_scale_fix(raw, detect_scale_errors(raw))
    assert fixed.loc[(fixed.period_end == ENDS[2]) & (fixed.item == "자산총계"), "value"].item() == 842_787_340_982


def test_currency_switch_not_flagged():
    # 3번째 분기부터 USD 보고(값이 약 1/1300로 줄어듦). 오기입이 아니고, USD 구간은 시총 비교도 하지 않는다.
    raw = _raw({2: 1e-3, 3: 1e-3, 4: 1e-3})
    raw["currency"] = raw["period_end"].map(lambda d: "KRW" if d < ENDS[2] else "USD")
    mc = raw[["symbol", "period_end"]].drop_duplicates().assign(marcap=2_000_000_000.0)
    assert detect_scale_errors(raw, mc).empty
