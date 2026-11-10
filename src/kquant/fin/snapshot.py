"""as-of 재무 스냅샷: 기준일에 시장이 알 수 있었던 재무값만으로 종목별 wide 표를 만든다.

조건 검색(P3)과 전략(P4)이 공통으로 쓴다.

규칙:
- 기준 보고서: 종목마다 ``available_date <= as_of``인 행 중 가장 최근 ``period_end``.
  그 보고서에 없는 항목은 이전 보고서로 채우지 않고 NaN으로 둔다.
- 손익·현금흐름(flow) 항목, 기준 보고서의 회계 분기를 q(1–4)라 할 때:
    - ``{항목}_q``   분기 3개월 값. 3M 행이 없으면(2Q·3Q 현금흐름) ``YTD(t) − YTD(t−3개월)``.
    - ``{항목}_ttm`` 최근 12개월. q=4이면 FY, 아니면 ``YTD(t) + FY(t−3q개월) − YTD(t−12개월)``.
    - ``{항목}_fy``  직전 완결 회계연도 값. q=4이면 그 FY, 아니면 ``FY(t−3q개월)``.
- 재무상태표(instant) 항목: ``{항목}`` = 기준 보고서 시점값.
- 전년 동기 ``…_1y``: 위 값을 결산기준일 12개월 전 보고서 기준으로 똑같이 계산한다.
- 보고서는 결산기준일(월말)로만 맞춘다. 결산월이 바뀐 기업처럼 날짜가 맞지 않으면 NaN이다.
- ``value``는 ``currency`` 단위 그대로다(비KRW 기업은 유니버스에서 거른다).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from kquant.fin.items import NCI_EQUITY, OWNER_NI
from kquant.fin.long import BS_ITEMS, CF_ITEMS, PL_ITEMS

FLOW_ITEMS = sorted((PL_ITEMS | CF_ITEMS | {"지배당기순이익"}) - {OWNER_NI})
INSTANT_ITEMS = sorted((BS_ITEMS | {"지배자본총계"}) - {NCI_EQUITY})
META_COLUMNS = [
    "symbol", "corp_name", "currency", "statement_type", "fiscal_year", "fiscal_q", "period_end", "available_date",
]


def _months_before(pe: pd.Series, months: pd.Series | int) -> pd.Series:
    """월말 날짜에서 n개월 전 월말."""
    months = pd.Series(months, index=pe.index) if np.isscalar(months) else months
    out = pd.Series(pd.NaT, index=pe.index, dtype=pe.dtype)
    for m in months.unique():
        sel = months == m
        out[sel] = (pe[sel] - pd.DateOffset(months=int(m))) + pd.offsets.MonthEnd(0)
    return out


class _Lookup:
    def __init__(self, avail: pd.DataFrame, symbols: pd.Series):
        key = ["symbol", "period_end", "item", "basis"]
        self.val = avail.drop_duplicates(key).set_index(key)["value"].astype("float64")
        self.symbols = symbols.to_numpy()

    def get(self, pe: pd.Series, item: str, basis: str | np.ndarray) -> np.ndarray:
        n = len(self.symbols)
        basis = np.full(n, basis, dtype=object) if isinstance(basis, str) else basis
        idx = pd.MultiIndex.from_arrays([self.symbols, pe.to_numpy(), np.full(n, item, dtype=object), basis])
        return self.val.reindex(idx).to_numpy()


def _flow(lk: _Lookup, pe: pd.Series, q: pd.Series, item: str) -> dict[str, np.ndarray]:
    is4 = (q == 4).to_numpy()
    ytd = lk.get(pe, item, np.where(is4, "FY", "YTD").astype(object))
    m3 = lk.get(pe, item, "3M")
    prev_ytd = lk.get(_months_before(pe, 3), item, "YTD")
    m3 = np.where(np.isnan(m3) & (q == 1).to_numpy(), ytd, m3)
    m3 = np.where(np.isnan(m3) & q.isin([2, 3]).to_numpy(), ytd - prev_ytd, m3)
    fy_prev = lk.get(_months_before(pe, 3 * q.where(~is4, 0)), item, "FY")
    ytd_1y = lk.get(_months_before(pe, 12), item, "YTD")
    ttm = np.where(is4, ytd, ytd + fy_prev - ytd_1y)
    return {"q": m3, "ttm": ttm, "fy": np.where(is4, ytd, fy_prev)}


def snapshot(long: pd.DataFrame, as_of, items: list[str] | None = None) -> pd.DataFrame:
    """``as_of``(날짜) 기준 종목별 재무 스냅샷. 컬럼은 ``META_COLUMNS`` + 항목 컬럼(모듈 설명 참고)."""
    as_of = pd.Timestamp(as_of)
    avail = long[long["available_date"] <= as_of]
    if avail.empty:
        return pd.DataFrame(columns=META_COLUMNS)
    latest = avail.loc[avail.groupby("symbol")["period_end"].idxmax(), ["symbol", "period_end"]]
    meta = avail.merge(latest, on=["symbol", "period_end"]).drop_duplicates("symbol")[META_COLUMNS]
    meta = meta.sort_values("symbol").reset_index(drop=True)

    lk = _Lookup(avail, meta["symbol"])
    pe, q = meta["period_end"], meta["fiscal_q"].str[0].astype(int)
    pe_1y = _months_before(pe, 12)
    flow = [i for i in FLOW_ITEMS if items is None or i in items]
    inst = [i for i in INSTANT_ITEMS if items is None or i in items]

    cols: dict[str, np.ndarray] = {}
    for it in flow:
        for suffix, p in (("", pe), ("_1y", pe_1y)):
            for k, v in _flow(lk, p, q, it).items():
                cols[f"{it}_{k}{suffix}"] = v
    for it in inst:
        cols[it] = lk.get(pe, it, "instant")
        cols[f"{it}_1y"] = lk.get(pe_1y, it, "instant")
    return pd.concat([meta, pd.DataFrame(cols, index=meta.index)], axis=1)
