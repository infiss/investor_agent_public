"""momentum: 최근 6개월 수정종가 수익률 순위 (spec §7).

- 수익률 = 기준일(리밸런싱일 직전 거래일) 수정종가 / 기준일로부터 달력 6개월 전(휴장이면 그 이전 마지막 거래일)
  수정종가 − 1. 유니버스 표의 ``mom_6m`` 컬럼(``add_momentum``이 계산)을 쓴다.
- 시작 시점 가격이 없는 종목(6개월 미만 상장 등)은 결과에서 뺀다.
"""

from __future__ import annotations

import pandas as pd

from kquant.strategies.base import final_rank, register

LOOKBACK_MONTHS = 6
START_TOLERANCE_DAYS = 31  # 시작일 근처에 거래가 없으면(장기 거래정지) 가격 없음으로 본다


def add_momentum(universe: pd.DataFrame, history: pd.DataFrame, decision_date) -> pd.DataFrame:
    """``history``(Date, Code, adj_close; 기준일 이전 구간)로 ``mom_6m``, ``mom_start_date``를 붙인다."""
    d = pd.Timestamp(decision_date)
    start = d - pd.DateOffset(months=LOOKBACK_MONTHS)
    h = history[(history["Date"] <= start) & (history["Date"] > start - pd.Timedelta(days=START_TOLERANCE_DAYS))]
    first = h.sort_values("Date").groupby("Code", observed=True).tail(1)
    first = first.set_index(first["Code"].astype(str))
    u = universe.copy()
    u["mom_start_date"] = u["symbol"].map(first["Date"])
    u["mom_start_price"] = u["symbol"].map(first["adj_close"])
    u["mom_6m"] = u["adj_close"] / u["mom_start_price"] - 1
    return u


@register("momentum", "가격 모멘텀", "최근 6개월(달력) 수정종가 수익률 순위, 기준일은 리밸런싱일 직전 거래일",
          ("symbol", "mom_6m"))
def rank_momentum(u: pd.DataFrame) -> pd.DataFrame:
    ok = u["mom_6m"].notna()
    out = pd.DataFrame({"symbol": u.loc[ok, "symbol"].values, "mom_6m": u.loc[ok, "mom_6m"].values})
    out["score"] = out["mom_6m"]
    out["rank"] = final_rank(out, ascending=False)
    return out.sort_values("rank").reset_index(drop=True)
