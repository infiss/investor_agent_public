"""공개 도구의 Python 진입점. MCP 서버(P7)는 이 함수들을 그대로 감싼다.

모든 함수는 JSON 직렬화 가능한 dict를 돌려주고, 잘못된 입력에는 예외 대신 ``{"ok": False, "problem": …}``를 준다.
최상위 키를 ``error``로 쓰지 않는다: Hermes는 응답 JSON에 ``error`` 키가 있으면 실패로 세고 3회 연속이면 MCP 서버를
60초(프로세스 전역) 멈춘다. 되묻기·입력 오류가 다른 사용자의 사용을 막지 않게 ``problem``을 쓴다(2026-11-10 결정 D2).
(추천의 ``disclosures.error``는 중첩 키라 해당 없음.)
모든 응답에 ``disclaimer``가 붙는다. 쓰기 작업은 리포트 HTML 캐시 파일 생성뿐이다(데이터는 읽기 전용).
매수 수량·매매 신호는 어떤 함수도 돌려주지 않는다.
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
from kquant.catalog.amount import AmountError, parse_number
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
RECOMMEND_MAX_N = 30


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
               "종목 추천(recommend)은 전략 1–3개를 고를 수 있고, 여러 개면 각 순위의 평균으로 합칩니다(교집합)."],
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


def _strategies(v) -> tuple[str, ...]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not v or len(v) > 3 or not all(isinstance(s, str) and s in STRATEGIES for s in v):
        raise ParamError("bad_strategies", "strategies는 value, salesmt, momentum 중 1–3개입니다", options=list(STRATEGIES))
    return tuple(sorted(set(v)))


def _size(params) -> tuple[str, float | None]:
    size = params.get("size", DEFAULTS["size"])
    if not isinstance(size, str) or size not in SIZES:
        raise ParamError("bad_size", "size는 all(전체), small(시총 하위 30%), large(시총 상위 30%) 중 하나입니다",
                         options=list(SIZES))
    return size, SIZES[size]


def _min_marcap(v) -> float:
    try:
        x = parse_number(v if v is not None else 0, "KRW")
    except AmountError as e:
        raise ParamError("bad_min_marcap", f"min_marcap 오류: {e}") from None
    if x < 0:
        raise ParamError("bad_min_marcap", "min_marcap은 0 이상입니다")
    return x


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
               notes=[(f"summary의 start는 수익률 계산의 기준일입니다. 시작 연도가 {FIRST_YEAR}년보다 뒤면 직전 연도 마지막 "
                       f"거래일이고, {FIRST_YEAR}년이면 첫 매수일입니다.")])


@functools.cache
def _report_code_digest() -> str:
    """리포트 내용을 정하는 코드의 지문. 그리드를 다시 돌리지 않고 이 코드만 바뀌어도 캐시가 새로 만들어진다."""
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for rel in ("service.py", "reports/html.py", "reports/metrics.py"):
        h.update((root / rel).read_bytes())
    return h.hexdigest()[:12]


def _round(d: dict) -> dict:
    return {k: _f(v) if isinstance(v, float) else v for k, v in d.items()}


# ---- 종목 추천 ----

def recommend(ctx: DataContext, params) -> dict:
    """최신 기준일에 전략을 적용한 상위 N종목(+ 최근 공시). 매수 수량·매매 신호는 없다."""
    from kquant.ingest.dart_api import load_corp_codes, recent_disclosures
    from kquant.strategies.select import select
    from kquant.universe import UniverseParams

    try:
        _check_keys(params, {"strategies", "n", "size", "min_marcap", "include_disclosures"})
        strategies = _strategies(params.get("strategies"))
        n = params.get("n", DEFAULTS["n"])
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= RECOMMEND_MAX_N:
            raise ParamError("bad_n", f"n은 1–{RECOMMEND_MAX_N} 정수입니다")
        size, cut = _size(params)
        mc = _min_marcap(params.get("min_marcap", 0))
        disc = params.get("include_disclosures", True)
        if not isinstance(disc, bool):
            raise ParamError("invalid_params", "include_disclosures는 true/false입니다")
    except ParamError as e:
        return e.payload

    d = ctx.prices.latest_date()
    sel = select(ctx, d + pd.Timedelta(days=1), list(strategies), n, UniverseParams(size, cut, mc or None),
                 decision_date=d)
    feats = ctx.features(d).set_index("symbol")
    corp = load_corp_codes(ctx.paths) if disc else {}
    rows = []
    for p in sel.picks.itertuples():
        f = feats.loc[p.symbol] if p.symbol in feats.index else None
        row = {
            "rank": int(p.rank), "symbol": p.symbol, "name": p.name, "market": p.market,
            "industry": None,
            "market_cap": None if _f(p.marcap) is None else int(p.marcap),
            "fin_period_end": None if f is None or pd.isna(f["fin_period_end"]) else str(f["fin_period_end"].date()),
            "metrics": {} if f is None else {
                "per_ttm": _f(f["per.ttm"], 2), "pbr": _f(f["pbr.latest"], 2), "psr_ttm": _f(f["psr.ttm"], 2),
                "pcr_ttm": _f(f["pcr.ttm"], 2), "roe_ttm_pct": _f(f["roe.ttm"], 2),
                "operating_income_growth_q_pct": _f(f["operating_income_growth.quarter"], 2),
            },
            "strategy_ranks": {s: int(getattr(p, f"rank_{s}")) for s in strategies},
        }
        if disc:
            cc = corp.get(p.symbol)
            row["disclosures"] = recent_disclosures(cc) if cc else {"items": [], "error": "공시 고유번호 정보 없음"}
        rows.append(row)
    return _ok(as_of=str(d.date()), strategies=list(strategies), universe_size=sel.universe_size, picks=rows,
               notes=["순위는 기준일까지 공개된 재무·가격만으로 계산했습니다.",
                      "산업 분류는 아직 제공하지 않습니다(데이터 소스 결정 대기).",
                      "이 목록은 매수 수량이나 매매 시점을 제시하지 않습니다."])
