"""시장 벤치마크: marcap으로 계산한 시가총액 가중 지수(KOSPI, KOSDAQ, 가격 수익률).

외부 지수 소스(FinanceDataReader 등) 없이 같은 가격 데이터로 만든다. 일간 수익률 =
Σ(전일 시가총액 × 종목 수정종가 수익률) / Σ 전일 시가총액. 당일 신규 상장·거래 없는 종목은 그날 빠진다.
"""

from __future__ import annotations

import pandas as pd


def market_index(prices: pd.DataFrame, base: float = 100.0) -> pd.DataFrame:
    """``prices``: Date, Code, Market, adj_close, Marcap. 반환: 날짜 × 시장 지수(첫날 = base)."""
    p = prices[prices["Market"].isin(["KOSPI", "KOSDAQ"])][["Date", "Code", "Market", "adj_close", "Marcap"]]
    p = p.sort_values(["Code", "Date"], kind="stable")
    g = p.groupby("Code", observed=True)
    p = p.assign(ret=g["adj_close"].pct_change(fill_method=None), w=g["Marcap"].shift())
    p = p[p["ret"].notna() & p["w"].notna()]
    p = p.assign(wr=p["ret"] * p["w"])
    agg = p.groupby(["Date", "Market"], observed=True)[["wr", "w"]].sum()
    r = (agg["wr"] / agg["w"]).unstack("Market").sort_index()
    idx = (1 + r.fillna(0)).cumprod() * base
    first = pd.DataFrame([[base] * idx.shape[1]], columns=idx.columns,
                         index=[prices["Date"].min()])
    return pd.concat([first, idx[idx.index > prices["Date"].min()]])
