"""OpenDART 재무제표 일괄 다운로드 txt 파일의 탐색과 읽기.

파일명 형식: ``{연도}_{1분기|반기|3분기|사업}보고서_{01..05}_{표이름}[_{금융업종}][_연결]_{YYYYMMDD}.txt``

- 2025년 3분기 이후 zip에는 금융업(금융기타·보험·은행·증권) 파일이 따로 들어 있다.
  기존 보관 txt에는 금융사가 없으므로 기본적으로 제외한다(``include_financial=False``).
- 같은 (연도, 분기, 표, 연결 여부, 금융업종) 파일이 두 곳 이상에 있으면 오류를 낸다.
  어느 쪽을 쓸지 조용히 고르지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# 파일명 속 분기 표기 → 내부 분기 키
REPORT_TO_Q = {"1분기": "1Q", "반기": "2Q", "3분기": "3Q", "사업": "4Q"}
Q_TO_REPORT = {v: k for k, v in REPORT_TO_Q.items()}

STATEMENTS = {
    "01": "재무상태표",
    "02": "손익계산서",
    "03": "포괄손익계산서",
    "04": "현금흐름표",
    "05": "자본변동표",
}
FINANCIAL_SECTORS = ("금융기타", "보험", "은행", "증권")

_NAME_RE = re.compile(
    r"^(?P<year>\d{4})_(?P<report>1분기|반기|3분기|사업)보고서_(?P<no>0[1-5])_(?P<stmt>[^_]+)"
    r"(?:_(?P<sector>" + "|".join(FINANCIAL_SECTORS) + r"))?"
    r"(?P<cons>_연결)?_(?P<date>\d{8})\.txt$"
)


@dataclass(frozen=True)
class DartFile:
    path: Path
    year: int
    quarter: str  # "1Q" | "2Q" | "3Q" | "4Q"
    statement_no: str  # "01".."05"
    consolidated: bool
    sector: str | None  # 금융업 변형이면 업종명, 일반이면 None
    file_date: str  # 파일명 끝 YYYYMMDD (DART 생성일)

    @property
    def key(self) -> tuple:
        return (self.year, self.quarter, self.statement_no, self.consolidated, self.sector)


def parse_name(path: Path) -> DartFile | None:
    """파일명이 DART txt 형식이 아니면 None."""
    m = _NAME_RE.match(path.name)
    if not m:
        return None
    if STATEMENTS[m["no"]] != m["stmt"]:
        raise ValueError(f"표 번호와 이름이 맞지 않음: {path.name}")
    return DartFile(
        path=path,
        year=int(m["year"]),
        quarter=REPORT_TO_Q[m["report"]],
        statement_no=m["no"],
        consolidated=m["cons"] is not None,
        sector=m["sector"],
        file_date=m["date"],
    )


def discover(root: Path, include_financial: bool = False) -> list[DartFile]:
    """``root`` 아래(하위 폴더 포함)의 DART txt를 찾는다. 형식이 다른 txt는 무시한다."""
    found: dict[tuple, DartFile] = {}
    for p in sorted(root.rglob("*.txt")):
        f = parse_name(p)
        if f is None or (f.sector is not None and not include_financial):
            continue
        if f.key in found:
            raise ValueError(f"같은 표가 두 파일에 있음: {found[f.key].path} / {p}")
        found[f.key] = f
    return sorted(found.values(), key=lambda f: f.key[:3] + (f.consolidated, f.sector or ""))


def read_txt(path: Path) -> pd.DataFrame:
    """탭 구분 cp949 txt를 문자열로 읽는다. 컬럼명의 공백을 모두 지우고, 이름 없는 빈 컬럼은 버린다."""
    df = pd.read_csv(path, sep="\t", encoding="cp949", dtype=str)
    df.columns = df.columns.str.replace(" ", "")
    return df.loc[:, ~df.columns.str.startswith("Unnamed")]


def coverage(files: list[DartFile]) -> pd.DataFrame:
    """연도×분기별로 일반 파일(01–05 × 연결/별도 = 10개)이 갖춰졌는지 표로 만든다."""
    rows = [(f.year, f.quarter) for f in files if f.sector is None]
    s = pd.Series(1, index=pd.MultiIndex.from_tuples(rows, names=["year", "quarter"]))
    return s.groupby(level=[0, 1]).sum().unstack("quarter").fillna(0).astype(int)
