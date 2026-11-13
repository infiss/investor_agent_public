"""공개 도구의 Python 진입점. MCP 서버(P7)는 이 함수들을 그대로 감싼다.

모든 함수는 JSON 직렬화 가능한 dict를 돌려주고, 잘못된 입력에는 예외 대신 ``{"ok": False, "problem": …}``를 준다.
최상위 키를 ``error``로 쓰지 않는다: Hermes는 응답 JSON에 ``error`` 키가 있으면 실패로 세고 3회 연속이면 MCP 서버를
60초(프로세스 전역) 멈춘다. 되묻기·입력 오류가 다른 사용자의 사용을 막지 않게 ``problem``을 쓴다(2026-11-10 결정 D2).
모든 응답에 ``disclaimer``가 붙는다. 쓰기 작업은 리포트 HTML 캐시 파일 생성뿐이다(데이터는 읽기 전용).
종목 추천·매수 수량·매매 신호는 어떤 함수도 돌려주지 않는다(2026-11-13: 추천 기능 삭제).
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import os
from pathlib import Path

import pandas as pd

from kquant.backtest.schedule import FIRST_YEAR
from kquant.catalog.catalog import list_fields
from kquant.catalog.screen import screen as _screen
from kquant.context import DataContext
from kquant.disclaimer import DISCLAIMER

STRATEGIES = ("value", "salesmt", "momentum")
SIZES = {"all": None, "small": 0.3, "large": 0.7}  # small = 시총 하위 30%, large = 시총 상위 30%(백분위 ≥ 0.7)
SIZE_LABELS = {"all": "전체", "small": "소형주(시총 하위 30%)", "large": "대형주(시총 상위 30%)"}
SCHEDULES = {"annual": "연 1회(사업보고서 제출 기한 3/31 이후 5거래일 여유를 두고 4월 초·중순)",
             "quarterly": "분기 1회(정기 보고서 제출 기한 3/31·5/15·8/14·11/14 이후 5거래일 여유)"}
DEFAULTS = {"n": 10, "size": "all", "schedule": "annual"}


class ParamError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.payload = {"ok": False, "problem": {"code": code, "message": message, **details},
                        "disclaimer": DISCLAIMER}


def _ok(**kw) -> dict:
    return {"ok": True, **kw, "disclaimer": DISCLAIMER}


def _f(x, digits=4):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(x) else round(x, digits)


# ---- 카탈로그 / 조건 검색 ----

def catalog(ctx: DataContext) -> dict:
    return _ok(fields=list_fields(), data=ctx.data_status(),
               usage="조건은 field(id)·period·op·value로 지정합니다. 기간이 여러 개인 항목은 period를 꼭 지정하세요.")


def screen(ctx: DataContext, query) -> dict:
    return _screen(query, ctx)


# ---- 전략 파라미터 ----

def strategy_options() -> dict:
    from kquant.strategies import REGISTRY

    return _ok(
        strategies=[{"id": s.name, "label": s.label, "description": s.description} for s in REGISTRY.values()],
        n=[5, 10, 20], size=[{"id": k, "label": v} for k, v in SIZE_LABELS.items()],
        schedule=[{"id": k, "label": v} for k, v in SCHEDULES.items()],
        years={"first": FIRST_YEAR, "note": "start_year·end_year로 달력 연도 구간을 고릅니다(마지막 연도는 데이터 기준일까지)."},
        defaults=DEFAULTS,
        notes=["전략 리포트(strategy_report)는 전략 하나(strategy)를 고릅니다.",
               "리포트는 과거 백테스트 성과이며 종목 추천이 아닙니다."],
    )


def _strategy(v) -> tuple[str]:
    if not isinstance(v, str) or v not in STRATEGIES:
        raise ParamError("bad_strategy", "strategy는 value, salesmt, momentum 중 하나입니다", options=list(STRATEGIES))
    return (v,)


def _year(v, name: str, last: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not FIRST_YEAR <= v <= last:
        raise ParamError("bad_years", f"{name}는 {FIRST_YEAR}–{last} 사이 연도(정수)입니다",
                         options={"first": FIRST_YEAR, "last": last})
    return v


def _size(params) -> tuple[str, float | None]:
    size = params.get("size", DEFAULTS["size"])
    if not isinstance(size, str) or size not in SIZES:
        raise ParamError("bad_size", "size는 all(전체), small(시총 하위 30%), large(시총 상위 30%) 중 하나입니다",
                         options=list(SIZES))
    return size, SIZES[size]


def _check_keys(params, allowed: set):
    if not isinstance(params, dict):
        raise ParamError("invalid_params", "파라미터는 JSON 객체여야 합니다")
    unknown = set(params) - allowed
    if unknown:
        raise ParamError("unknown_key", f"알 수 없는 키: {sorted(str(k)[:40] for k in unknown)}", allowed=sorted(allowed))


def resolve_grid_key(params, last_year: int) -> tuple[str, dict, tuple[int, int]]:
    """리포트 파라미터 → (그리드 키, 키 설명, (시작 연도, 끝 연도)). ``last_year``는 데이터 기준일의 연도."""
    from kquant.backtest.grid import GridKey

    _check_keys(params, {"strategy", "n", "size", "schedule", "start_year", "end_year"})
    strategies = _strategy(params.get("strategy"))
    n = params.get("n", DEFAULTS["n"])
    if isinstance(n, bool) or n not in (5, 10, 20):
        raise ParamError("bad_n", "n은 5, 10, 20 중 하나입니다", options=[5, 10, 20])
    size, cut = _size(params)
    schedule = params.get("schedule", DEFAULTS["schedule"])
    if not isinstance(schedule, str) or schedule not in SCHEDULES:
        raise ParamError("bad_schedule", "schedule은 annual(연 1회) 또는 quarterly(분기 1회)입니다",
                         options=list(SCHEDULES))
    sy = _year(params.get("start_year", FIRST_YEAR), "start_year", last_year)
    ey = _year(params.get("end_year", last_year), "end_year", last_year)
    if sy > ey:
        raise ParamError("bad_years", "start_year는 end_year보다 클 수 없습니다", options={"first": FIRST_YEAR, "last": last_year})
    key = GridKey(strategies, int(n), size, cut, 0.0, schedule)
    return key.id, key.to_dict(), (sy, ey)


# ---- 전략 리포트 ----

def _names_for(ctx: DataContext, periods: pd.DataFrame) -> dict[str, str]:
    names: dict[str, str] = {}
    for p in periods.itertuples():
        syms = {e["symbol"] for e in json.loads(p.excluded)}
        syms |= {e["symbol"] for e in json.loads(getattr(p, "abnormal_held", "[]") or "[]")}
        syms |= {e["symbol"] for e in json.loads(getattr(p, "settled", "[]") or "[]")}
        if syms:
            cs = ctx.prices.cross_section(p.rebalance_date)
            names.update(dict(zip(cs["Code"], cs["Name"], strict=True)))
            missing = syms - set(names)
            if missing:
                cs = ctx.prices.cross_section(p.decision_date)
                names.update(dict(zip(cs["Code"], cs["Name"], strict=True)))
    return names


def strategy_report(ctx: DataContext, params) -> dict:
    from kquant.backtest.grid import load_result
    from kquant.reports.html import build_report
    from kquant.reports.metrics import clean

    gdir = ctx.paths.grid_dir
    if not (gdir / "meta.json").exists():
        return ParamError("grid_not_ready", "백테스트 결과가 아직 준비되지 않았습니다").payload
    meta = json.loads((gdir / "meta.json").read_text(encoding="utf-8"))
    try:
        key, kparams, (sy, ey) = resolve_grid_key(params, int(meta["latest_date"][:4]))
    except ParamError as e:
        return e.payload
    name = hashlib.sha1(f"{key}|{sy}-{ey}|{meta['fingerprint']}|{meta['latest_date']}|{_report_code_digest()}"
                        .encode()).hexdigest()[:16]
    rdir = ctx.paths.report_dir
    rdir.mkdir(parents=True, exist_ok=True)
    out = rdir / f"report-{name}.html"
    summary_f = rdir / f"report-{name}.json"
    summary = None
    if out.exists() and summary_f.exists():
        try:
            summary = json.loads(summary_f.read_text(encoding="utf-8"))
            if not isinstance(summary.get("strategy"), dict) or not isinstance(summary.get("benchmarks"), dict):
                summary = None
        except (OSError, ValueError, AttributeError):
            summary = None
    if summary is None:
        try:
            values, periods, bench = load_result(gdir, key)
        except KeyError:
            return ParamError("not_in_grid", "이 설정의 백테스트 결과가 없습니다", key=key).payload
        doc, summary = build_report(key, kparams, values, periods, bench, _names_for(ctx, periods),
                                    meta["latest_date"], sy, ey, buy_rate=float(meta.get("buy_cost_rate", 0.0)),
                                    sell_rate=float(meta.get("sell_cost_rate", 0.0)))
        for target, text in ((summary_f, json.dumps(summary, ensure_ascii=False)), (out, doc)):
            tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(target)
    base = os.environ.get("KQUANT_REPORT_BASE_URL")
    link = f"{base.rstrip('/')}/{out.name}" if base else out.name  # 서버 경로는 공개 응답에 넣지 않는다
    return _ok(report=link, key=key, params=kparams, start_year=sy, end_year=ey, data_date=meta["latest_date"],
               summary={"strategy": _round(clean(summary["strategy"])),
                        "benchmarks": {k: _round(clean(v)) for k, v in summary["benchmarks"].items()}},
               display={"strategy": _summary_display(clean(summary["strategy"])),
                        "benchmarks": {k: _summary_display(clean(v)) for k, v in summary["benchmarks"].items()}},
               notes=["사용자에게는 display의 문자열(예: CAGR '10.91%')을 그대로 보이세요. summary의 수익률은 비율(0.1091 = 10.91%)입니다.",(f"summary의 start는 수익률 계산의 기준일입니다. 시작 연도가 {FIRST_YEAR}년보다 뒤면 직전 연도 마지막 "
                       f"거래일이고, {FIRST_YEAR}년이면 첫 매수일입니다.")])


@functools.cache
def _report_code_digest() -> str:
    """리포트 내용을 정하는 코드의 지문. 그리드를 다시 돌리지 않고 이 코드만 바뀌어도 캐시가 새로 만들어진다."""
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for rel in ("service.py", "reports/html.py", "reports/metrics.py"):
        h.update((root / rel).read_bytes())
    return h.hexdigest()[:12]


PCT_KEYS = ("total_return", "cagr", "volatility", "mdd", "positive_years", "worst_year")
X_KEYS = ("sharpe", "sortino", "calmar")


def _summary_display(d: dict) -> dict:
    """요약 지표의 표시 문자열(비율 → %). LLM이 0.109를 0.11%로 옮기는 식의 환산 실수를 막는다(2026-11-13)."""
    out = {k: d[k] for k in ("start", "end", "worst_year_label") if k in d}
    for k in PCT_KEYS:
        if _f(d.get(k)) is not None:
            out[k] = f"{d[k] * 100:,.2f}%"
    for k in X_KEYS:
        if _f(d.get(k)) is not None:
            out[k] = f"{d[k]:,.2f}"
    return out


def _round(d: dict) -> dict:
    return {k: _f(v) if isinstance(v, float) else v for k, v in d.items()}


