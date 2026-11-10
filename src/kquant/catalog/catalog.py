"""카탈로그 로드와 항목 조회. ``fields.yaml``이 유일한 정의다."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

import yaml

KINDS = {"flow", "stock", "price", "ratio"}
PERIODS = {"quarter", "ttm", "annual", "latest"}
PERIOD_LABELS = {"quarter": "분기(3개월)", "ttm": "최근 12개월(TTM)", "annual": "직전 연간", "latest": "최신"}


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    kind: str
    unit: str
    periods: tuple[str, ...]
    default_period: str | None
    aliases: tuple[str, ...] = ()
    item: str | None = None  # 재무 항목명 (flow/stock)
    column: str | None = None  # 시세 컬럼 (price)
    description: str = ""
    formula: str = ""
    extra: dict = field(default_factory=dict)

    def key(self, period: str) -> str:
        return f"{self.id}.{period}"


def _parse(raw: list[dict]) -> dict[str, Field]:
    out: dict[str, Field] = {}
    for r in raw:
        f = Field(
            id=r["id"], label=r["label"], kind=r["kind"], unit=r["unit"], periods=tuple(r["periods"]),
            default_period=r.get("default_period"), aliases=tuple(r.get("aliases", [])), item=r.get("item"),
            column=r.get("column"), description=r.get("description", ""), formula=r.get("formula", ""),
        )
        if f.kind not in KINDS or not set(f.periods) <= PERIODS or f.id in out:
            raise ValueError(f"카탈로그 정의 오류: {f.id}")
        if f.default_period is not None and f.default_period not in f.periods:
            raise ValueError(f"default_period가 periods에 없음: {f.id}")
        out[f.id] = f
    return out


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Field]:
    text = resources.files("kquant.catalog").joinpath("fields.yaml").read_text(encoding="utf-8")
    return _parse(yaml.safe_load(text))


def _norm(s: str) -> str:
    return s.replace(" ", "").lower()


def resolve(name: str) -> Field | None:
    """id, 표시명, 별칭으로 항목을 찾는다(공백·대소문자 무시). 없으면 None."""
    cat = load_catalog()
    if name in cat:
        return cat[name]
    n = _norm(name)
    for f in cat.values():
        if n in {_norm(f.id), _norm(f.label), *(_norm(a) for a in f.aliases)}:
            return f
    return None


def suggest(name: str, n: int = 5) -> list[str]:
    cat = load_catalog()
    names = {}
    for f in cat.values():
        for s in (f.id, f.label, *f.aliases):
            names[_norm(s)] = f.id
    hits = difflib.get_close_matches(_norm(name), list(names), n=n * 2, cutoff=0.4)
    out: list[str] = []
    for h in hits:
        if names[h] not in out:
            out.append(names[h])
    return out[:n]


def list_fields() -> list[dict]:
    """사용자에게 보여줄 카탈로그 표(항목, 가능 기간, 단위, 설명)."""
    rows = []
    for f in load_catalog().values():
        rows.append({
            "id": f.id, "label": f.label, "kind": f.kind, "unit": f.unit,
            "periods": list(f.periods), "period_labels": [PERIOD_LABELS[p] for p in f.periods],
            "default_period": f.default_period, "aliases": list(f.aliases),
            "description": f.formula or f.description,
        })
    return rows
