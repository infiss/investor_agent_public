"""금액·수치 문자열 파싱. LLM이 아니라 kquant가 단위를 변환한다 (spec §6.2).

예: "2000억" → 2e11, "1조 5000억원" → 1.5e12, "1.5조" → 1.5e12, "300만" → 3e6, "-50억" → -5e9,
    "15%" → 15.0, "1.2배" → 1.2, "1,000" → 1000.0
"""

from __future__ import annotations

import math
import re

UNITS = {"조": 10**12, "억": 10**8, "만": 10**4, "천": 10**3}
_NUM = r"\d+(?:,\d{3})*(?:\.\d+)?|\d*\.\d+"
_PART = re.compile(rf"({_NUM})\s*(조|억|만|천)?")
MAX_LEN = 40


class AmountError(ValueError):
    pass


def parse_number(value, unit: str) -> float:
    """조건 값 하나를 float으로. ``unit``은 항목 단위(KRW, %, 배). bool·NaN·inf·과도한 길이는 거부한다."""
    if isinstance(value, bool):
        raise AmountError("숫자가 아닙니다")
    if isinstance(value, int | float):
        try:
            v = float(value)
        except OverflowError:
            raise AmountError("값이 너무 큽니다") from None
    elif isinstance(value, str):
        v = _parse_str(value, unit)
    else:
        raise AmountError("숫자 또는 금액 문자열이어야 합니다")
    if not math.isfinite(v):
        raise AmountError("유한한 숫자가 아닙니다")
    return v


def _parse_str(text: str, unit: str) -> float:
    if len(text) > MAX_LEN:
        raise AmountError("값이 너무 깁니다")
    s = text.strip().replace(" ", "")
    neg = s.startswith("-")
    if neg or s.startswith("+"):
        s = s[1:]
    if unit == "KRW":
        s = s.removesuffix("원")
    elif unit == "%":
        s = s.removesuffix("%")
    elif unit == "배":
        s = s.removesuffix("배")
    if not s:
        raise AmountError(f"해석할 수 없는 값: {text!r}")
    total, pos = 0.0, 0
    last_unit = math.inf
    for m in _PART.finditer(s):
        if m.start() != pos:
            break
        num = float(m.group(1).replace(",", ""))
        u = m.group(2)
        if u is not None and unit != "KRW":
            raise AmountError(f"{unit} 단위 항목에는 금액 단위를 쓸 수 없습니다: {text!r}")
        mult = UNITS[u] if u else 1
        if mult >= last_unit:  # "억 조"처럼 큰 단위가 뒤에 오면 거부
            raise AmountError(f"해석할 수 없는 값: {text!r}")
        last_unit = mult
        total += num * mult
        if not math.isfinite(total):
            raise AmountError("값이 너무 큽니다")
        pos = m.end()
        if u is None:
            break
    if pos != len(s):
        raise AmountError(f"해석할 수 없는 값: {text!r}")
    return -total if neg else total
