import pandas as pd

from kquant.fin.dart_txt import discover
from kquant.fin.long import build_long

HEAD = ["재무제표종류", "종목코드", "회사명", "결산월", "결산기준일", "항목코드", "항목명"]


def _write(tmp_path, name, value_cols, rows):
    df = pd.DataFrame(rows, columns=HEAD + value_cols)
    df.insert(len(HEAD), "통화", "KRW")  # DART 01–04 표에는 항상 통화 컬럼이 있다
    df.to_csv(tmp_path / name, sep="\t", index=False, encoding="cp949")


def _val(long, item, basis, q="2Q", symbol="00000A"):
    r = long[(long.symbol == symbol) & (long.item == item) & (long.basis == basis) & (long.fiscal_q == q)]
    assert len(r) == 1, (item, basis, len(r))
    return int(r["value"].iloc[0])


def _half_year(tmp_path, a_rows_cf=None):
    y, r = 2024, "반기"
    _write(tmp_path, f"{y}_{r}보고서_01_재무상태표_20250101.txt", ["당기반기말"], [
        ["BS", "[00000A]", "에이", "12", "2024-06-30", "ifrs-full_Equity", "자본총계", "1,000"],
        ["BS", "[00000A]", "에이", "12", "2024-06-30", "ifrs-full_NoncontrollingInterests", "비지배지분", "-100"],
        ["BS", "[00000A]", "에이", "12", "2024-06-30", "x_IssuedCapital", "보통주자본금", "7"],
        ["BS", "[00000A]", "에이", "12", "2024-06-30", "y_IssuedCapital", "자본금", "9"],
    ])
    _write(tmp_path, f"{y}_{r}보고서_02_손익계산서_20250101.txt", ["당기반기3개월", "당기반기누적"], [
        ["PL", "[00000A]", "에이", "12", "2024-06-30", "ifrs-full_ProfitLoss", "당기순이익", "10", "25"],
    ])
    _write(tmp_path, f"{y}_{r}보고서_03_포괄손익계산서_20250101.txt", ["당기반기3개월", "당기반기누적"], [
        ["CI", "[00000A]", "에이", "12", "2024-06-30", "ifrs-full_ProfitLoss", "당기순이익", "11", "26"],
    ])
    _write(tmp_path, f"{y}_{r}보고서_04_현금흐름표_20250101.txt", ["당기반기"], a_rows_cf or [
        ["CF", "[00000A]", "에이", "12", "2024-06-30", "ifrs-full_ProfitLoss", "당기순이익", "25"],
        ["CF", "[00000A]", "에이", "12", "2024-06-30", "ifrs-full_CashFlowsFromUsedInOperatingActivities",
         "영업활동현금흐름", "40"],
    ])


def test_net_income_not_mixed_with_cash_flow(tmp_path):
    """L5: 현금흐름표의 누적 순이익이 손익 3개월 값과 섞이지 않는다. 손익계산서(02)가 포괄(03)보다 우선한다."""
    _half_year(tmp_path)
    long, rep = build_long(discover(tmp_path))
    assert _val(long, "당기순이익", "3M") == 10
    assert _val(long, "당기순이익", "YTD") == 25
    assert _val(long, "영업활동현금흐름", "YTD") == 40
    assert _val(long, "지배당기순이익", "3M") == 10  # 지배 귀속 줄 없음 → 당기순이익
    assert _val(long, "지배자본총계", "instant") == 900
    assert _val(long, "자본금", "instant") == 9  # 표준 항목명이 추가 항목명보다 우선
    row = long[long.item == "당기순이익"].iloc[0]
    assert row["available_date"] == pd.Timestamp("2024-08-14")  # 6/30 + 45일
    assert row["statement_type"] == "OFS"
    assert len(rep.conflicts) == 0


def test_ifrs_prefix_variant_both_directions(tmp_path):
    """L24: 매핑표가 ifrs-full_ 인데 파일이 ifrs_ 코드를 써도 매칭된다."""
    _half_year(tmp_path, a_rows_cf=[
        ["CF", "[00000A]", "에이", "12", "2024-06-30", "ifrs_CashFlowsFromUsedInOperatingActivities",
         "영업에서 창출된 현금", "41"],
    ])
    long, _ = build_long(discover(tmp_path))
    assert _val(long, "영업활동현금흐름", "YTD") == 41


def test_two_period_ends_in_one_file_kept_apart(tmp_path):
    """L25: 결산월 변경으로 한 파일에 결산기준일이 둘이면 따로 보존하고, 연결 우선도 기준일별로 판단한다."""
    y = 2022
    _write(tmp_path, f"{y}_사업보고서_01_재무상태표_연결_20230101.txt", ["당기"], [
        ["BS", "[00000A]", "에이", "03", "2022-03-31", "ifrs-full_Assets", "자산총계", "100"],
    ])
    _write(tmp_path, f"{y}_사업보고서_01_재무상태표_20230101.txt", ["당기"], [
        ["BS", "[00000A]", "에이", "03", "2022-03-31", "ifrs-full_Assets", "자산총계", "90"],
        ["BS", "[00000A]", "에이", "12", "2022-12-31", "ifrs-full_Assets", "자산총계", "120"],
    ])
    long, rep = build_long(discover(tmp_path))
    a = long[long.item == "자산총계"].set_index("period_end")
    assert a.loc[pd.Timestamp("2022-03-31"), "value"] == 100
    assert a.loc[pd.Timestamp("2022-03-31"), "statement_type"] == "CFS"
    assert a.loc[pd.Timestamp("2022-12-31"), "value"] == 120
    assert a.loc[pd.Timestamp("2022-12-31"), "available_date"] == pd.Timestamp("2023-03-31")
    assert len(rep.conflicts) == 0


def test_q4_three_month_derived(tmp_path):
    _write(tmp_path, "2023_3분기보고서_02_손익계산서_20240101.txt", ["당기3분기3개월", "당기3분기누적"], [
        ["PL", "[00000A]", "에이", "12", "2023-09-30", "ifrs-full_Revenue", "매출액", "30", "80"],
    ])
    _write(tmp_path, "2023_사업보고서_02_손익계산서_20240101.txt", ["당기"], [
        ["PL", "[00000A]", "에이", "12", "2023-12-31", "ifrs-full_Revenue", "매출액", "110"],
    ])
    long, _ = build_long(discover(tmp_path))
    assert _val(long, "매출액", "FY", q="4Q") == 110
    assert _val(long, "매출액", "3M", q="4Q") == 30
