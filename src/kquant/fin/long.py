"""DART txt → long 포맷 재무 데이터 (새 시스템의 재무 원천).

한 행 = (종목, 보고 기간, 항목, 기간 기준) 하나의 값.

컬럼:
    symbol, corp_name, fiscal_year, fiscal_q, fiscal_month, period_end, available_date,
    statement_type(CFS|OFS), currency, item, basis(3M|YTD|FY|instant), value(int64), source_file

``value``는 ``currency``(DART ``통화`` 컬럼, 대부분 KRW) 단위다. 환산하지 않는다.

규칙:
- 연결 우선: 그 보고 기간에 연결 파일(01–04)에 나오는 종목은 연결만, 아니면 별도.
- 항목마다 출처 표를 고정한다. 손익은 02(손익계산서) → 03(포괄손익계산서), 현금흐름은 04, 재무상태는 01.
  현금흐름표의 누적 순이익이 손익 순이익에 섞이지 않는다.
- 매칭 우선순위: 그 연도 IFRS 코드 > ``ifrs_``↔``ifrs-full_`` 변형 > 항목명 정확 일치.
  같은 우선순위의 후보가 여럿이면 파일의 첫 행을 쓰고 ``conflicts``에 기록한다(평균하지 않음).
- ``available_date`` = 결산기준일 + 45일(1Q–3Q), + 90일(4Q). 법정 제출 기한 기준이다.
- 파생 항목: ``지배당기순이익``(지배 귀속 줄이 있으면 그 값, 없으면 당기순이익),
  ``지배자본총계``(자본총계 − |비지배지분|). 4Q의 3M 값은 ``FY − 3Q YTD``로 만든다(손익·현금흐름).
- 단위(10^k) 오기입 보고서는 파생 전에 보정한다(``kquant.fin.quality``, 판정표는 ``BuildReport.scale_flags``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from kquant.fin.dart_txt import Q_TO_REPORT, DartFile, read_txt
from kquant.fin.items import NCI_EQUITY, OWNER_NI, load_item_map
from kquant.fin.quality import apply_scale_fix, detect_scale_errors

BS_ITEMS = {
    "자본총계", "유동자산", "부채총계", "자산총계", "단기차입금", "장기차입금", "이익잉여금", "자본잉여금",
    "자본금", "무형자산", "유형자산", "매출채권", "현금및현금성자산", "재고자산", NCI_EQUITY,
}
PL_ITEMS = {"당기순이익", "영업이익", "매출액", "매출총이익", "기타비용", "법인세차감전순이익", OWNER_NI}
CF_ITEMS = {"영업활동현금흐름", "유형자산취득", "배당금지급"}
ITEM_STATEMENTS = {
    **{i: ("01",) for i in BS_ITEMS},
    **{i: ("02", "03") for i in PL_ITEMS},
    **{i: ("04",) for i in CF_ITEMS},
}
DISCLOSURE_LAG_DAYS = {"1Q": 45, "2Q": 45, "3Q": 45, "4Q": 90}

LONG_COLUMNS = [
    "symbol", "corp_name", "fiscal_year", "fiscal_q", "fiscal_month", "period_end", "available_date",
    "statement_type", "currency", "item", "basis", "value", "source_file",
]


@dataclass
class BuildReport:
    periods: list[tuple[int, str]] = field(default_factory=list)
    rows: int = 0
    unparsable: int = 0
    conflicts: pd.DataFrame | None = None  # 같은 우선순위 후보가 여럿이었던 (종목, 기간, 항목, basis)
    duplicate_periods: pd.DataFrame | None = None  # 같은 종목·결산기준일·분기가 여러 라벨 연도에 있음
    scale_flags: pd.DataFrame | None = None  # 단위(10^k) 오기입 판정표 (kquant.fin.quality)


def _value_columns(quarter: str, stmt: str) -> dict[str, str]:
    """표 번호별 {원본 컬럼: basis}."""
    if quarter == "4Q":
        return {"당기": "instant" if stmt == "01" else "FY"}
    r = Q_TO_REPORT[quarter]
    if stmt == "01":
        return {f"당기{r}말": "instant"}
    if stmt in ("02", "03"):
        return {f"당기{r}3개월": "3M", f"당기{r}누적": "YTD"}
    return {f"당기{r}": "YTD"}


def _match(df: pd.DataFrame, item_map: pd.DataFrame, year: int) -> pd.DataFrame:
    """각 행에 (item, match_rank) 부여. rank 0=코드, 1=변형 코드, 2=표준 항목명, 3=추가 항목명. 매칭 없으면 행 제거."""
    code_map: dict[str, tuple[str, int]] = {}
    name_map: dict[str, str] = {}
    for _, row in item_map.iterrows():
        key, code, extra = row["항목명"], row[f"{year}년"], row["추가항목명"]
        code_map.setdefault(code, (key, 0))
        if code.startswith("ifrs_"):
            code_map.setdefault("ifrs-full_" + code[len("ifrs_"):], (key, 1))
        elif code.startswith("ifrs-full_"):
            code_map.setdefault("ifrs_" + code[len("ifrs-full_"):], (key, 1))
        name_map.setdefault(key, (key, 2))
        if isinstance(extra, str):
            for n in extra.replace(" ", "").split(","):
                name_map.setdefault(n, (key, 3))
    by_code = df["항목코드"].map(lambda c: code_map.get(c, (None, None)))
    item = by_code.str[0]
    rank = by_code.str[1]
    by_name = df["항목명"].map(lambda n: name_map.get(n, (None, None)))
    use_name = item.isna() & by_name.str[0].notna()
    item = item.where(~use_name, by_name.str[0])
    rank = rank.where(~use_name, by_name.str[1])
    out = df.assign(item=item, match_rank=rank)
    out = out[out["item"].notna()]
    allowed = [s in ITEM_STATEMENTS.get(i, ()) for s, i in zip(out["stmt"], out["item"])]
    return out[allowed]


def _load_period(files: list[DartFile], year: int, quarter: str) -> pd.DataFrame:
    sel = [f for f in files if f.year == year and f.quarter == quarter and f.statement_no != "05"]
    parts = []
    for f in sel:
        d = read_txt(f.path)
        d["stmt"] = f.statement_no
        d["consolidated"] = f.consolidated
        d["source_file"] = f.path.name
        d["row_order"] = np.arange(len(d))
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    df["symbol"] = df["종목코드"].str.strip().str.strip("[]")
    # 연결 우선은 (종목, 결산기준일) 단위: 결산월 변경으로 한 파일에 결산기준일이 둘인 기업이 있다
    df["pkey"] = df["symbol"] + "|" + df["결산기준일"].astype(str)
    cons = set(df.loc[df["consolidated"], "pkey"])
    df = df[df["consolidated"] | ~df["pkey"].isin(cons)]
    df = df[df["항목명"].notna() & (df["symbol"] != "null")].copy()
    df["항목명"] = df["항목명"].str.strip()
    return df


def build_period(
    files: list[DartFile], year: int, quarter: str, item_map: pd.DataFrame
) -> tuple[pd.DataFrame, dict]:
    df = _match(_load_period(files, year, quarter), item_map, year)
    melted = []
    for stmt in ("01", "02", "03", "04"):
        part = df[df["stmt"] == stmt]
        for col, basis in _value_columns(quarter, stmt).items():
            if col not in part.columns:
                continue
            m = part[part[col].notna()].assign(basis=basis, raw=lambda x, c=col: x[c])
            melted.append(m)
    m = pd.concat(melted, ignore_index=True)
    m["value"] = pd.to_numeric(m["raw"].str.replace(",", "", regex=False).str.strip(), errors="coerce")
    unparsable = int(m["value"].isna().sum())
    m = m[m["value"].notna()]

    # 우선순위: 표 순서(02 > 03) → 매칭 순위 → 파일 내 행 순서
    m["stmt_pri"] = ((m["stmt"] == "03") & m["item"].isin(PL_ITEMS)).astype(int)
    m = m.sort_values(["pkey", "item", "basis", "stmt_pri", "match_rank", "row_order"])
    key = ["pkey", "item", "basis"]
    first = m.drop_duplicates(key, keep="first")
    top = m.merge(first[key + ["stmt_pri", "match_rank"]], on=key + ["stmt_pri", "match_rank"])
    conf = top[top.groupby(key)["value"].transform("nunique") > 1]
    conflicts = (
        conf.groupby(key + ["symbol"]).agg(n=("value", "size"), values=("value", lambda s: list(s)),
                              names=("항목명", lambda s: list(s))).reset_index()
        .assign(fiscal_year=year, fiscal_q=quarter).drop(columns="pkey")
    )

    out = pd.DataFrame({
        "symbol": first["symbol"].values,
        "corp_name": first["회사명"].str.strip().values,
        "fiscal_year": year,
        "fiscal_q": quarter,
        "fiscal_month": pd.to_numeric(first["결산월"], errors="coerce").astype("Int64").values,
        "period_end": pd.to_datetime(first["결산기준일"]).values,
        "statement_type": np.where(first["consolidated"], "CFS", "OFS"),
        "currency": first["통화"].str.strip().values,
        "item": first["item"].values,
        "basis": first["basis"].values,
        "value": first["value"].astype("int64").values,
        "source_file": first["source_file"].values,
    })
    return out, {"unparsable": unparsable, "conflicts": conflicts}


def _add_owner_items(long: pd.DataFrame) -> pd.DataFrame:
    """지배당기순이익, 지배자본총계 파생 행을 추가하고 원천 보조 항목(지배 NI, 비지배지분)은 남긴다."""
    k = ["symbol", "fiscal_year", "fiscal_q", "period_end", "basis"]
    ni = long[long["item"] == "당기순이익"]
    own = long[long["item"] == OWNER_NI][k + ["value"]].rename(columns={"value": "own"})
    a = ni.merge(own, on=k, how="left")
    a["value"] = a["own"].fillna(a["value"]).astype("int64")
    a["item"] = "지배당기순이익"
    eq = long[long["item"] == "자본총계"]
    nci = long[long["item"] == NCI_EQUITY][k + ["value"]].rename(columns={"value": "nci"})
    b = eq.merge(nci, on=k, how="left")
    b["value"] = (b["value"] - b["nci"].abs().fillna(0)).astype("int64")
    b["item"] = "지배자본총계"
    return pd.concat([long, a[LONG_COLUMNS], b[LONG_COLUMNS]], ignore_index=True)


def _add_q4_3m(long: pd.DataFrame) -> pd.DataFrame:
    """4Q 3개월 값 = FY − 같은 회계연도 3Q YTD. 3Q 결산기준일이 FY 결산기준일보다 3개월 앞서는 경우만 계산한다."""
    flow = long["item"].isin(PL_ITEMS | CF_ITEMS | {"지배당기순이익"})
    fy = long[flow & (long["fiscal_q"] == "4Q") & (long["basis"] == "FY")]
    q3 = long[flow & (long["fiscal_q"] == "3Q") & (long["basis"] == "YTD")]
    q3 = q3.assign(expected_fy_end=q3["period_end"] + pd.offsets.MonthEnd(3))
    j = fy.merge(
        q3[["symbol", "item", "expected_fy_end", "value"]].rename(columns={"value": "q3"}),
        left_on=["symbol", "item", "period_end"], right_on=["symbol", "item", "expected_fy_end"],
    )
    j["value"] = (j["value"] - j["q3"]).astype("int64")
    j["basis"] = "3M"
    return pd.concat([long, j[LONG_COLUMNS]], ignore_index=True)


def build_long(
    files: list[DartFile], item_map: pd.DataFrame | None = None,
    marcap_lookup=None,
) -> tuple[pd.DataFrame, BuildReport]:
    """``marcap_lookup``: (symbol, period_end) 키 표 → marcap 컬럼을 붙여 돌려주는 함수(끝 보고서 단위 확인용, 선택)."""
    item_map = load_item_map() if item_map is None else item_map
    periods = sorted({(f.year, f.quarter) for f in files})
    report = BuildReport(periods=periods)
    parts, conflicts = [], []
    for year, quarter in periods:
        out, diag = build_period(files, year, quarter, item_map)
        parts.append(out)
        conflicts.append(diag["conflicts"])
        report.unparsable += diag["unparsable"]
    long = pd.concat(parts, ignore_index=True)
    lag = long["fiscal_q"].map(DISCLOSURE_LAG_DAYS)
    long["available_date"] = long["period_end"] + pd.to_timedelta(lag, unit="D")
    long = long[LONG_COLUMNS]
    # 단위 오기입 보정은 파생 항목(지배 항목, 4Q 3M)을 만들기 전에 한다
    keys = long[["symbol", "period_end"]].drop_duplicates()
    marcap = marcap_lookup(keys) if marcap_lookup is not None else None
    report.scale_flags = detect_scale_errors(long, marcap)
    long = apply_scale_fix(long, report.scale_flags)
    long = _add_owner_items(long)
    long = _add_q4_3m(long)

    per = long.drop_duplicates(["symbol", "fiscal_year", "fiscal_q", "period_end"])
    dup = per[per.duplicated(["symbol", "fiscal_q", "period_end"], keep=False)]
    report.duplicate_periods = dup[["symbol", "corp_name", "fiscal_year", "fiscal_q", "period_end"]]
    report.conflicts = pd.concat(conflicts, ignore_index=True)
    report.rows = len(long)
    long = long.sort_values(["symbol", "period_end", "fiscal_q", "item", "basis"]).reset_index(drop=True)
    return long, report
