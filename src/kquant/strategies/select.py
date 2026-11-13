"""리밸런싱일 종목 선정: 판단 기준일 결정 → 유니버스 → 전략 순위 → 조합 → 상위 N.

백테스트(P5)가 쓴다(종목 추천 기능은 2026-11-13 삭제).
- 판단 기준일 ``d`` = 리밸런싱일 직전 거래일. 가격 단면·재무 스냅샷·모멘텀 모두 ``d`` 기준이다.
- ``exclude``: 선정에서 뺄 종목(예: 그 기간 이상 수익률 종목). 빠진 자리는 다음 순위가 채운다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from kquant.strategies.base import combine, rank_strategies
from kquant.strategies.momentum import LOOKBACK_MONTHS, START_TOLERANCE_DAYS, add_momentum
from kquant.universe import UniverseParams, build_universe


@dataclass
class Selection:
    rebalance_date: pd.Timestamp
    decision_date: pd.Timestamp
    strategies: list[str]
    ranked: pd.DataFrame  # 조합 순위 전체 (+ 유니버스 정보)
    picks: pd.DataFrame  # 상위 N
    universe_size: int
    excluded_universe: pd.DataFrame  # 유니버스 제외 기록(사유별)
    excluded_manual: list[str] = field(default_factory=list)


def select(ctx, rebalance_date, strategies: list[str], n: int, params: UniverseParams | None = None,
           exclude: set[str] | None = None, decision_date=None) -> Selection:
    reb = pd.Timestamp(rebalance_date)
    d = pd.Timestamp(decision_date) if decision_date is not None else ctx.prices.last_trading_day(
        reb - pd.Timedelta(days=1))
    if d is None:
        raise ValueError(f"{reb.date()} 이전 거래일이 없습니다")
    prices = ctx.prices.cross_section(d)
    universe, log = build_universe(prices, ctx.snapshot(d), params)
    if "momentum" in strategies:
        start = d - pd.DateOffset(months=LOOKBACK_MONTHS) - pd.Timedelta(days=START_TOLERANCE_DAYS)
        hist = ctx.prices.range(start, d)[["Date", "Code", "adj_close"]]
        universe = add_momentum(universe, hist, d)
    ranked = combine(rank_strategies(universe, strategies))
    info = universe[["symbol", "name", "market", "marcap", "close"]]
    ranked = ranked.merge(info, on="symbol", how="left")
    exclude = exclude or set()
    manual = sorted(set(ranked["symbol"]) & exclude)
    picks = ranked[~ranked["symbol"].isin(exclude)].head(n).reset_index(drop=True)
    return Selection(reb, d, list(strategies), ranked, picks, len(universe), log, manual)
