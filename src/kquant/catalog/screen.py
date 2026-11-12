"""필터 DSL 검증·실행 (spec §6.2).

질의 예::

    {"as_of": "latest",
     "where": [{"field": "market_cap", "op": ">=", "value": "2000억"},
               {"field": "revenue", "period": "ttm", "op": ">=", "value": 30000000000}],
     "market": ["KOSPI", "KOSDAQ"],
     "sort": {"field": "market_cap", "order": "desc"},
     "fields": [{"field": "per"}],
     "limit": 50}

- 필드·기간·연산자·시장은 카탈로그 화이트리스트로 검증한다. SQL이나 문자열 수식을 만들지 않고
  pandas 비교 연산만 쓰므로 주입 경로가 없다.
- 잘못된 질의는 실행하지 않고 ``{"ok": False, "problem": {...}}``를 돌려준다(최상위 ``error`` 키 금지, service 참고). 에이전트는 이 오류로 되묻는다.
  오류 코드: invalid_query, unknown_key, unknown_field, period_required, unsupported_period,
  bad_operator, bad_value, bad_market, bad_sort, bad_limit, bad_as_of, too_many_conditions.
"""

from __future__ import annotations

import math
import operator
from dataclasses import dataclass

import numpy as np
import pandas as pd

from kquant.catalog.amount import AmountError, format_value, parse_number
from kquant.catalog.catalog import PERIOD_LABELS, Field, resolve, suggest
from kquant.context import DataContext, DateError
from kquant.disclaimer import DISCLAIMER

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
       "==": operator.eq, "!=": operator.ne}
MARKETS = ("KOSPI", "KOSDAQ", "KONEX")
DEFAULT_MARKETS = ["KOSPI", "KOSDAQ"]
TOP_KEYS = {"as_of", "where", "market", "sort", "fields", "limit"}
COND_KEYS = {"field", "period", "op", "value"}
MAX_WHERE = 20
MAX_FIELDS = 20
MAX_LIMIT = 100
DEFAULT_LIMIT = 20


class ScreenError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def to_dict(self) -> dict:
        return {"ok": False, "problem": {"code": self.code, "message": self.message, **self.details}}


@dataclass(frozen=True)
class Cond:
    field: Field
    period: str
    op: str
    value: float | tuple[float, float]

    @property
    def key(self) -> str:
        return self.field.key(self.period)


@dataclass(frozen=True)
class Plan:
    as_of: str | None
    conds: tuple[Cond, ...]
    markets: tuple[str, ...]
    sort: tuple[Field, str, bool] | None  # (field, period, ascending)
    extra: tuple[tuple[Field, str], ...]
    limit: int


def _short(x, n: int = 60) -> str:
    s = str(x)
    return s if len(s) <= n else s[:n] + "…"


def _field(name, where: str) -> Field:
    if not isinstance(name, str) or not name.strip() or len(name) > 60:
        raise ScreenError("unknown_field", f"{where}: 항목 이름이 올바르지 않습니다", field=_short(name))
    f = resolve(name)
    if f is None:
        raise ScreenError("unknown_field", f"카탈로그에 없는 항목입니다: {_short(name)}",
                          field=_short(name), suggestions=suggest(name))
    return f


def _period(f: Field, period, loc: str, inherited: dict[str, str] | None = None) -> str:
    """기간 확정. ``loc``(예: ``where[0]``, ``sort``, ``fields[1]``)은 오류 위치로 돌려준다 — 에이전트가 고칠 곳을 알게.

    ``inherited``: sort·fields에서 기간을 생략하면, 같은 항목이 where에 한 기간으로만 쓰였을 때 그 기간을 쓴다
    (사용자가 이미 정한 기간을 보여 주는 열에 다시 묻지 않는다). where 자체에는 적용하지 않는다.
    """
    if period is None:
        if len(f.periods) == 1:
            return f.periods[0]
        if inherited and f.id in inherited:
            return inherited[f.id]
        if f.default_period is None:
            raise ScreenError(
                "period_required", f"{loc}: '{f.label}'은(는) 기간을 지정해야 합니다",
                field=f.id, location=loc,
                options=[{"period": p, "label": PERIOD_LABELS[p]} for p in f.periods])
        return f.default_period
    if not isinstance(period, str) or period not in f.periods:
        raise ScreenError("unsupported_period", f"{loc}: '{f.label}'에서 지원하지 않는 기간입니다: {_short(period)}",
                          field=f.id, location=loc, options=list(f.periods))
    return period


def _cond(c, loc: str) -> Cond:
    if not isinstance(c, dict):
        raise ScreenError("invalid_query", "where의 각 조건은 객체여야 합니다")
    unknown = set(c) - COND_KEYS
    if unknown:
        raise ScreenError("unknown_key", f"조건에 알 수 없는 키: {sorted(map(_short, unknown))}")
    f = _field(c.get("field"), "where")
    p = _period(f, c.get("period"), loc)
    op = c.get("op")
    if op == "between":
        v = c.get("value")
        if not isinstance(v, list | tuple) or len(v) != 2:
            raise ScreenError("bad_value", "between의 value는 [하한, 상한] 두 값이어야 합니다", field=f.id)
        lo, hi = (_num(x, f) for x in v)
        if lo > hi:
            raise ScreenError("bad_value", "between의 하한이 상한보다 큽니다", field=f.id)
        return Cond(f, p, op, (lo, hi))
    if not isinstance(op, str) or op not in OPS:
        raise ScreenError("bad_operator", f"지원하지 않는 연산자: {_short(op)}",
                          options=[*OPS, "between"])
    return Cond(f, p, op, _num(c.get("value"), f))


def _num(v, f: Field) -> float:
    try:
        return parse_number(v, f.unit)
    except AmountError as e:
        raise ScreenError("bad_value", f"'{f.label}' 값 오류: {e}", field=f.id, unit=f.unit) from None


def validate(query) -> Plan:
    if not isinstance(query, dict):
        raise ScreenError("invalid_query", "질의는 JSON 객체여야 합니다")
    unknown = set(query) - TOP_KEYS
    if unknown:
        raise ScreenError("unknown_key", f"알 수 없는 키: {sorted(map(_short, unknown))}",
                          allowed=sorted(TOP_KEYS))
    where = query.get("where", [])
    if not isinstance(where, list):
        raise ScreenError("invalid_query", "where는 조건 목록이어야 합니다")
    if len(where) > MAX_WHERE:
        raise ScreenError("too_many_conditions", f"조건은 최대 {MAX_WHERE}개입니다")
    conds = tuple(_cond(c, f"where[{i}]") for i, c in enumerate(where))
    periods_by_field: dict[str, set[str]] = {}
    for c in conds:
        periods_by_field.setdefault(c.field.id, set()).add(c.period)
    inherited = {k: next(iter(v)) for k, v in periods_by_field.items() if len(v) == 1}

    markets = query.get("market", DEFAULT_MARKETS)
    if isinstance(markets, str):
        markets = [markets]
    if (not isinstance(markets, list) or not markets
            or not all(isinstance(m, str) and m.upper() in MARKETS for m in markets)):
        raise ScreenError("bad_market", "market은 KOSPI, KOSDAQ, KONEX 중에서 고릅니다", options=list(MARKETS))
    markets = tuple(dict.fromkeys(m.upper() for m in markets))

    sort = None
    s = query.get("sort")
    if s is not None:
        if not isinstance(s, dict) or set(s) - {"field", "period", "order"}:
            raise ScreenError("bad_sort", "sort는 {field, period?, order?} 객체여야 합니다")
        f = _field(s.get("field"), "sort")
        order = s.get("order", "desc")
        if order not in ("asc", "desc"):
            raise ScreenError("bad_sort", "order는 asc 또는 desc입니다")
        sort = (f, _period(f, s.get("period"), "sort", inherited), order == "asc")

    extra = []
    fields = query.get("fields", [])
    if not isinstance(fields, list) or len(fields) > MAX_FIELDS:
        raise ScreenError("invalid_query", f"fields는 최대 {MAX_FIELDS}개 항목 목록입니다")
    for i, x in enumerate(fields):
        x = {"field": x} if isinstance(x, str) else x
        if not isinstance(x, dict) or set(x) - {"field", "period"}:
            raise ScreenError("invalid_query", "fields의 각 항목은 이름 또는 {field, period?}입니다")
        f = _field(x.get("field"), "fields")
        extra.append((f, _period(f, x.get("period"), f"fields[{i}]", inherited)))

    limit = query.get("limit", DEFAULT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ScreenError("bad_limit", f"limit은 1–{MAX_LIMIT} 정수입니다")

    as_of = query.get("as_of", "latest")
    if not isinstance(as_of, str) or len(as_of) > 20:
        raise ScreenError("bad_as_of", "as_of는 'latest' 또는 YYYY-MM-DD입니다")
    return Plan(as_of, conds, markets, sort, tuple(extra), limit)


def _apply(df: pd.DataFrame, c: Cond) -> pd.Series:
    v = df[c.key]
    if c.op == "between":
        lo, hi = c.value
        return v.ge(lo) & v.le(hi)
    return OPS[c.op](v, c.value) & v.notna()


def _jsonable(x):
    if x is None or x is pd.NaT or (isinstance(x, float) and math.isnan(x)):  # 재무 없는 종목의 결산기준일 = NaT
        return None
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.floating | float):
        return None if math.isnan(x) else float(x)
    if isinstance(x, pd.Timestamp):
        return str(x.date())
    return x


def _annual_end(period_end, fiscal_q) -> str | None:
    """'직전 연간' 값의 회계연도 종료일. 스냅샷 규칙(q=4면 그 FY, 아니면 결산기준일 3q개월 전 FY)과 같다."""
    if period_end is None or pd.isna(period_end) or not isinstance(fiscal_q, str) or not fiscal_q[:1].isdigit():
        return None
    q = int(fiscal_q[0])
    end = pd.Timestamp(period_end) - pd.DateOffset(months=3 * (q % 4)) + pd.offsets.MonthEnd(0)
    return str(end.date())


def execute(plan: Plan, ctx: DataContext) -> dict:
    try:
        date = ctx.resolve_date(plan.as_of)
    except DateError as e:
        raise ScreenError("bad_as_of", str(e)) from None
    df = ctx.features(date)
    mask = df["market"].isin(plan.markets)
    for c in plan.conds:
        mask &= _apply(df, c)
    hit = df[mask]

    cols: list[tuple[Field, str]] = []
    for f, p in [*((c.field, c.period) for c in plan.conds), *plan.extra,
                 *([(plan.sort[0], plan.sort[1])] if plan.sort else [])]:
        if (f, p) not in cols:
            cols.append((f, p))
    if plan.sort:
        f, p, asc = plan.sort
        hit = hit.sort_values(f.key(p), ascending=asc, na_position="last", kind="stable")
    else:
        hit = hit.sort_values("market_cap.latest", ascending=False, na_position="last", kind="stable")
    top = hit.head(plan.limit)

    has_annual = any(p == "annual" for _, p in cols)
    rows = []
    for _, r in top.iterrows():
        row = {"symbol": r["symbol"], "name": r["name"], "market": r["market"],
               "fin_period_end": _jsonable(r["fin_period_end"]), "trading_halted": bool(r["trading_halted"])}
        if has_annual:
            row["fin_annual_period_end"] = _annual_end(r["fin_period_end"], r["fin_fiscal_q"])
        display = {}
        for f, p in cols:
            v = _jsonable(r[f.key(p)])
            if isinstance(v, float):
                v = round(v) if f.unit == "KRW" else round(v, 2)
            row[f.key(p)] = v
            display[f.key(p)] = format_value(v, f.unit)
        row["display"] = display
        rows.append(row)
    return {
        "ok": True,
        "as_of": str(date.date()),
        "total_matches": len(hit),
        "returned": len(rows),
        "columns": [{"key": f.key(p), "label": f.label, "period": p, "period_label": PERIOD_LABELS[p],
                     "unit": f.unit} for f, p in cols],
        "rows": rows,
        "notes": [
            ("값을 사용자에게 보일 때는 각 행 display의 문자열(예: '55.95조', '1,862억', '5.92배')을 그대로 쓰세요. "
             "원 단위 숫자를 직접 조·억으로 환산하지 마세요."),
            "재무값은 기준일까지 법정 제출 기한이 지난 최신 보고서 기준이며, fin_period_end가 그 결산기준일입니다.",
            *(["'직전 연간'(annual) 값은 fin_period_end가 아니라 fin_annual_period_end로 끝나는 직전 완결 회계연도 값입니다."]
              if has_annual else []),
            "재무 보고 통화가 원화가 아닌 기업은 재무 기반 조건을 만족하지 않는 것으로 처리합니다.",
            "trading_halted가 true인 종목은 기준일에 거래가 없었습니다(거래정지). 가격은 직전 거래 가격입니다.",
        ],
        "disclaimer": DISCLAIMER,
    }


def screen(query, ctx: DataContext) -> dict:
    """공개 진입점. 잘못된 질의는 예외 대신 구조화된 오류 dict를 돌려준다."""
    try:
        return execute(validate(query), ctx)
    except ScreenError as e:
        out = e.to_dict()
        out["disclaimer"] = DISCLAIMER
        return out
