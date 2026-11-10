"""재무 항목 정의: DART 항목코드/항목명 → 표준 항목명 매핑표와 특수 항목 이름."""

from __future__ import annotations

import io
from importlib import resources

import pandas as pd

OWNER_NI = "지배기업의 소유주에게 귀속되는 당기순이익(손실)"
NCI_EQUITY = "자본_비지배지분"


def load_item_map() -> pd.DataFrame:
    """연도별 IFRS 항목코드(``{YYYY}년`` 컬럼)와 추가 항목명 → 표준 ``항목명`` 매핑표."""
    text = resources.files("kquant.fin").joinpath("dart_item_map.csv").read_text(encoding="utf-8")
    return pd.read_csv(io.StringIO(text), dtype=str)
