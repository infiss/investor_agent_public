"""기간 시뮬레이션: 동일 비중, 정수 주식 수, 잔액 현금, 매수·매도 비용 비율은 인자(기본 0).

- 매수: 리밸런싱일 종가(원가격 Close)로 ``floor(배분액 / Close)``주. 배분액 = 기간 시작 자금 / 종목 수.
- 평가: 보유가치_t = 주식 수 × 매수 종가 × (수정종가_t / 수정종가_매수일). 기간 중 분할·병합이 있어도 정확하다.
- 거래가 없는 날(거래정지)은 직전 수정종가를 유지한다. 기간 중 상장폐지되면 마지막 거래 가격으로 평가가
  고정된다(= 그 가격에 현금화). legacy ``adj_mode``의 두 방식(이탈일 제거 / 0으로 채움)은 쓰지 않는다.
- 기간 말 정산(2026-11-07, 방식 A): 기간 마지막 날 거래가 없는 종목은 ``exit_rel``(기간 종료 후 첫 거래일
  수정종가 / 매수일 수정종가)로 마지막 날 가치를 계산한다. 돈은 기간 말에 들어온 것으로 본다.
- 항등식: 매 거래일 총액 = 현금 + Σ 보유가치. 기간 종료 총액이 다음 기간 시작 자금이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PeriodResult:
    values: pd.Series  # 거래일별 총액
    holdings: pd.DataFrame  # symbol, shares, buy_close, cost
    cash: float


def simulate_period(capital: float, buy_close: pd.Series, adj: pd.DataFrame, buy_rate: float = 0.0,
                    sell_rate: float = 0.0, exit_rel: pd.Series | None = None) -> PeriodResult:
    """``buy_close``: 종목별 매수일 종가. ``adj``: 기간 거래일 × 종목 수정종가(첫 행 = 매수일, 결측 없음).

    ``buy_rate``·``sell_rate``: 매수·매도 금액에 부과하는 비용 비율(예: 매도 0.002 = 증권거래세). 기본 0.
    매도 비용은 기간 마지막 날 총액에서 뺀다(기간마다 전량 매도로 본다).
    """
    days = adj.index
    syms = list(buy_close.index)
    if not syms:
        return PeriodResult(pd.Series(capital, index=days, dtype=float),
                            pd.DataFrame(columns=["symbol", "shares", "buy_close", "cost"]), capital)
    if adj[syms].iloc[0].isna().any():
        raise ValueError("매수일 가격이 없는 종목이 있습니다")
    alloc = capital / len(syms)
    px = buy_close.to_numpy(dtype=float)
    shares = np.floor(alloc / (px * (1 + buy_rate)))
    cost = shares * px
    cash = capital - float((cost * (1 + buy_rate)).sum())
    rel = adj[syms].ffill() / adj[syms].iloc[0]
    if exit_rel is not None and len(exit_rel):
        rel.iloc[-1] = exit_rel.reindex(syms).fillna(rel.iloc[-1]).to_numpy()
    held = rel.to_numpy() * cost
    values = cash + held.sum(axis=1)
    if sell_rate:
        values[-1] -= float(held[-1].sum()) * sell_rate  # 마지막 날 매도 비용
    holdings = pd.DataFrame({"symbol": syms, "shares": shares, "buy_close": px, "cost": cost})
    return PeriodResult(pd.Series(values, index=days, dtype=float), holdings, cash)
