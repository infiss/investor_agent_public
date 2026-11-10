"""일별 총액 시계열 → 성과 지표.

- 수익률은 거래일 총액 기준(현금 구간 포함). 무위험 수익률 0, 연환산 거래일 252.
- CAGR은 달력 기간 기준: (최종/최초)^(365.25/일수) − 1.
- 연도별 수익률: 각 연도 마지막 값 / 직전 연도 마지막 값(첫 해는 시작값) − 1.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def drawdown(values: pd.Series) -> pd.Series:
    return values / values.cummax() - 1


def yearly_returns(values: pd.Series) -> pd.Series:
    last = values.groupby(values.index.year).last()
    prev = last.shift(1)
    prev.iloc[0] = values.iloc[0]
    return last / prev - 1


def full_years(values: pd.Series) -> list[int]:
    """시작·끝이 연초·연말 근처(±10일)인 연도만. 부분 연도는 연도 통계(최악 연도, 수익 연도 비율)에서 뺀다."""
    out = []
    for y, g in values.groupby(values.index.year):
        if (g.index[0] <= pd.Timestamp(year=y, month=1, day=10)
                and g.index[-1] >= pd.Timestamp(year=y, month=12, day=20)):
            out.append(int(y))
    return out


def summarize(values: pd.Series) -> dict:
    v = values.dropna()
    if len(v) < 2:
        return {}
    r = v.pct_change().dropna()
    days = (v.index[-1] - v.index[0]).days
    total = v.iloc[-1] / v.iloc[0] - 1
    cagr = (v.iloc[-1] / v.iloc[0]) ** (365.25 / days) - 1 if days > 0 else float("nan")
    vol = r.std(ddof=1) * math.sqrt(TRADING_DAYS)
    sharpe = r.mean() / r.std(ddof=1) * math.sqrt(TRADING_DAYS) if r.std(ddof=1) > 0 else float("nan")
    down = r[r < 0]
    dd_std = math.sqrt((down**2).sum() / len(r)) * math.sqrt(TRADING_DAYS) if len(r) else float("nan")
    sortino = r.mean() * TRADING_DAYS / dd_std if dd_std > 0 else float("nan")
    mdd = drawdown(v).min()
    yr = yearly_returns(v)
    yr = yr[yr.index.isin(full_years(v))]
    return {
        "start": str(v.index[0].date()), "end": str(v.index[-1].date()),
        "total_return": float(total), "cagr": float(cagr), "volatility": float(vol), "sharpe": float(sharpe),
        "sortino": float(sortino), "mdd": float(mdd),
        "calmar": float(cagr / abs(mdd)) if mdd < 0 else float("nan"),
        "positive_years": float((yr > 0).mean()) if len(yr) else float("nan"),
        "worst_year": float(yr.min()) if len(yr) else float("nan"),
        "worst_year_label": int(yr.idxmin()) if len(yr) else None,
    }


def year_window(values: pd.Series, start_year: int, end_year: int) -> pd.Series:
    """달력 연도 구간 [start_year, end_year]. 직전 연도의 마지막 값이 있으면 기준점으로 앞에 붙인다
    (첫 거래일 수익률도 구간에 포함되도록). 시작 연도에 그 전 값이 없으면 첫 값부터."""
    yrs = values.index.year
    sel = values[(yrs >= start_year) & (yrs <= end_year)]
    prev = values[yrs < start_year]
    return pd.concat([prev.iloc[-1:], sel]) if len(prev) and len(sel) else sel


def clean(d: dict) -> dict:
    return {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in d.items()}
