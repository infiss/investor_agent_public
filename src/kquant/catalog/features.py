"""기준일 단면 지표 표: 가격 단면 + as-of 재무 스냅샷 → 카탈로그 ``{id}.{period}`` 컬럼.

- 대상: 기준일에 거래 행이 있는 종목(KOSPI·KOSDAQ·KONEX). 재무 스냅샷은 같은 날짜 기준이다.
- 재무 보고 통화가 KRW가 아닌 종목은 재무 기반 값(flow·stock·ratio)을 모두 비운다(원화 시총과 비교 불가).
- 비율 계산은 이 파일의 ``RATIOS``가 유일한 구현이다(문자열 수식을 평가하지 않는다).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from kquant.catalog.catalog import Field, load_catalog

FIN_SUFFIX = {"quarter": "_q", "ttm": "_ttm", "annual": "_fy"}
BASE_COLUMNS = ["symbol", "name", "market", "price_date", "fin_period_end", "fin_fiscal_q", "currency",
                "trading_halted"]


def _fin(df: pd.DataFrame, item: str, period: str, lag: str = "") -> pd.Series:
    col = item if period == "latest" else f"{item}{FIN_SUFFIX[period]}"
    col += lag
    return df[col] if col in df else pd.Series(np.nan, index=df.index)


def _pos(s: pd.Series) -> pd.Series:
    return s.where(s > 0)


def _growth(item: str) -> Callable[[pd.DataFrame, str], pd.Series]:
    def f(df: pd.DataFrame, p: str) -> pd.Series:
        cur, prev = _fin(df, item, p), _pos(_fin(df, item, p, "_1y"))
        return (cur - prev) / prev * 100

    return f


RATIOS: dict[str, Callable[[pd.DataFrame, str], pd.Series]] = {
    "per": lambda d, p: d["Marcap"] / _pos(_fin(d, "지배당기순이익", p)),
    "pbr": lambda d, p: d["Marcap"] / _pos(_fin(d, "지배자본총계", "latest")),
    "psr": lambda d, p: d["Marcap"] / _pos(_fin(d, "매출액", p)),
    "pcr": lambda d, p: d["Marcap"] / _pos(_fin(d, "영업활동현금흐름", p)),
    "roe": lambda d, p: _fin(d, "지배당기순이익", p) / _pos(_fin(d, "지배자본총계", "latest")) * 100,
    "roa": lambda d, p: _fin(d, "지배당기순이익", p) / _pos(_fin(d, "자산총계", "latest")) * 100,
    "debt_ratio": lambda d, p: _fin(d, "부채총계", "latest") / _pos(_fin(d, "자본총계", "latest")) * 100,
    "operating_margin": lambda d, p: _fin(d, "영업이익", p) / _pos(_fin(d, "매출액", p)) * 100,
    "net_margin": lambda d, p: _fin(d, "지배당기순이익", p) / _pos(_fin(d, "매출액", p)) * 100,
    "revenue_growth": _growth("매출액"),
    "operating_income_growth": _growth("영업이익"),
    "net_income_growth": _growth("지배당기순이익"),
}


def field_values(df: pd.DataFrame, f: Field, period: str) -> pd.Series:
    if f.kind == "price":
        return df[f.column].astype(float)
    if f.kind in ("flow", "stock"):
        return _fin(df, f.item, period).astype(float)
    return RATIOS[f.id](df, period).astype(float)


def build_features(prices: pd.DataFrame, snap: pd.DataFrame) -> pd.DataFrame:
    """``prices``: PriceStore.cross_section 결과, ``snap``: kquant.fin.snapshot 결과."""
    px = prices[prices["Market"].isin(["KOSPI", "KOSDAQ", "KONEX"])]
    df = px.rename(columns={"Code": "symbol"}).merge(snap, on="symbol", how="left", suffixes=("", "_fin"))
    fin_cols = [c for c in snap.columns if c not in
                {"symbol", "corp_name", "currency", "statement_type", "fiscal_year", "fiscal_q",
                 "period_end", "available_date"}]
    non_krw = df["currency"].notna() & (df["currency"] != "KRW")
    df.loc[non_krw, fin_cols] = np.nan

    out = pd.DataFrame({
        "symbol": df["symbol"], "name": df["Name"], "market": df["Market"], "price_date": df["Date"],
        "fin_period_end": df["period_end"], "fin_fiscal_q": df["fiscal_q"], "currency": df["currency"],
        "trading_halted": df["Volume"].astype(float) <= 0,
    })
    cols = {}
    for f in load_catalog().values():
        for p in f.periods:
            cols[f.key(p)] = field_values(df, f, p).to_numpy()
    out = pd.concat([out, pd.DataFrame(cols, index=out.index)], axis=1)
    return out.replace([np.inf, -np.inf], np.nan).reset_index(drop=True)
