"""전략 레지스트리와 조합.

계약: 전략은 순수 함수 ``rank(universe: DataFrame) -> DataFrame[symbol, score, rank, 진단 컬럼...]``.
- ``rank``는 1이 가장 좋다. 순위를 매길 수 없는 종목은 결과에서 뺀다.
- 여러 전략 조합: 각 전략 결과의 교집합(symbol inner join)에서 ``rank``들의 평균으로 최종 순위를 매긴다.
- 동점은 symbol 오름차순으로 정해 결과가 항상 같다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Strategy:
    name: str
    label: str
    description: str
    requires: tuple[str, ...]  # 유니버스 표에 필요한 컬럼
    fn: Callable[[pd.DataFrame], pd.DataFrame]


REGISTRY: dict[str, Strategy] = {}


def register(name: str, label: str, description: str, requires: tuple[str, ...]):
    def deco(fn):
        REGISTRY[name] = Strategy(name, label, description, requires, fn)
        return fn

    return deco


def final_rank(df: pd.DataFrame, score_col: str = "score", ascending: bool = True) -> pd.Series:
    """score 정렬(동점은 symbol 순)로 1부터 순위를 매긴다."""
    order = df.sort_values([score_col, "symbol"], ascending=[ascending, True], kind="stable").index
    return pd.Series(range(1, len(order) + 1), index=order).reindex(df.index)


def rank_strategies(universe: pd.DataFrame, names: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for n in names:
        if n not in REGISTRY:
            raise KeyError(f"알 수 없는 전략: {n}")
        s = REGISTRY[n]
        missing = [c for c in s.requires if c not in universe.columns]
        if missing:
            raise KeyError(f"{n} 전략에 필요한 컬럼이 없음: {missing}")
        out[n] = s.fn(universe)
    return out


def combine(results: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """교집합에서 순위 평균 → 최종 순위. 컬럼: symbol, rank, score, rank_<전략>…"""
    merged = None
    for n, r in results.items():
        part = r[["symbol", "rank"]].rename(columns={"rank": f"rank_{n}"})
        merged = part if merged is None else merged.merge(part, on="symbol", how="inner")
    if merged is None:
        return pd.DataFrame(columns=["symbol", "rank", "score"])
    merged["score"] = merged[[f"rank_{n}" for n in results]].mean(axis=1)
    merged["rank"] = final_rank(merged)
    return merged.sort_values("rank").reset_index(drop=True)


# 전략 모듈을 불러와 레지스트리에 등록한다
from kquant.strategies import momentum, salesmt, value  # noqa: F401
