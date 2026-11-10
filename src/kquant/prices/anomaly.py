"""이상 수익률 탐지 (2026-11-06 결정: 해당 종목은 그 리밸런싱 기간 백테스트에서 제외, 리포트에 표시).

수정종가 일간 수익률이 가격제한폭을 넘는 날. 신규상장·재상장·감자 후 거래재개처럼 거래소 기준가가
따로 정해지는 날이 대부분이다. 가격제한폭: 2015-06-15부터 ±30%, 그 전 ±15%. 반올림 여유 ``TOL``을 둔다.
"""

from __future__ import annotations

import pandas as pd

LIMIT_CHANGE_DATE = pd.Timestamp("2015-06-15")
TOL = 0.005


def price_limit(dates: pd.Series) -> pd.Series:
    return pd.Series(0.30, index=dates.index).where(dates >= LIMIT_CHANGE_DATE, 0.15)


def abnormal_days(prices: pd.DataFrame) -> pd.DataFrame:
    """``prices``(Date, Code, adj_close; 연속 구간) → 이상일 표(Code, Date, ret, limit)."""
    p = prices[["Code", "Date", "adj_close"]].sort_values(["Code", "Date"], kind="stable")
    ret = p.groupby("Code", observed=True)["adj_close"].pct_change(fill_method=None)
    lim = price_limit(p["Date"])
    bad = ret.abs() > lim + TOL
    out = pd.DataFrame({"Code": p["Code"].astype(str), "Date": p["Date"], "ret": ret, "limit": lim})[bad]
    return out.reset_index(drop=True)
