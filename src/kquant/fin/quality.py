"""재무 long 데이터 품질 검사: 보고서 단위 단위(10^k) 오기입 탐지·보정.

DART 원본에는 보고서 하나 전체가 10³·10⁶배(드물게 1/10⁶) 부풀려진 값이 있다
(예: 동성코퍼레이션 2017 2Q 자산총계 8.4e17원). 오기입은 보고서 안의 모든 값 컬럼에 같은 배율로 나타난다.

판정 (보고서 = 종목 × 결산기준일):
- 재무상태표(instant) 항목별로 ``log10(|값| / |이웃 보고서 값|)``의 중앙값을 직전·직후 보고서에 대해 구한다.
  재무상태표는 분기 간 변동이 작아서 배율 판정 기준으로 쓴다(손익·현금흐름은 실적 변동이 커서 기준으로 쓰지 않음).
- **양쪽 확인**: 두 중앙값이 모두 같은 3k(k≠0)에서 ±``SCALE_TOL`` 이내이고, 비교 항목이 ``MIN_ITEMS``개 이상이면 보정한다.
- **끝 보고서**(이웃이 한쪽뿐): 한쪽 중앙값이 3k에 맞고, 자산총계/시가총액 배율이 그 종목의 중앙값보다
  3k ± ``MCAP_TOL`` 만큼 벗어나 있을 때만 보정한다. 시가총액 정보가 없으면 ``review``로 남긴다.
- 위에 해당하지 않지만 시가총액 대비 자산총계가 비정상인 보고서는 ``review``로만 기록한다(보정하지 않음).
- 통화(``currency``)가 있으면 이웃 비교는 같은 통화 구간 안에서만 하고, 시가총액 비교는 KRW 보고서에만 한다.

보정: 그 보고서의 모든 행(재무상태·손익·현금흐름, 모든 basis) 값을 ``10^k``로 나눈다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SCALE_TOL = 0.5  # 배율 중앙값이 3k에서 벗어나도 되는 폭 (log10)
MIN_ITEMS = 5  # 이웃 비교에 필요한 최소 재무상태표 항목 수
MCAP_TOL = 1.0  # 끝 보고서의 시가총액 확인 허용폭 (log10)
REVIEW_MCAP_REL = 2.5  # 종목 중앙값 대비 자산총계/시총 배율이 이만큼 벗어나면 review
REVIEW_MCAP_ABS = 4.0  # 자산총계/시총이 10^4배 이상이면 review (보고서가 하나뿐인 종목 대비)

FLAG_COLUMNS = [
    "symbol", "corp_name", "fiscal_year", "fiscal_q", "period_end", "status", "method", "k",
    "med_prev", "n_prev", "med_next", "n_next", "mcap_log_ratio", "mcap_rel", "rows",
]


def marcap_at(marcap_files: list[Path], keys: pd.DataFrame, tolerance_days: int = 30) -> pd.DataFrame:
    """``keys``(symbol, period_end)마다 결산기준일 이전 가장 가까운 거래일의 시가총액을 붙인다."""
    years = sorted({int(y) for y in keys["period_end"].dt.year.unique()})
    files = [f for f in marcap_files if int(f.stem.split("-")[1]) in years]
    if not files:
        return keys.assign(marcap=np.nan)
    mc = pd.concat([pd.read_parquet(f, columns=["Date", "Code", "Marcap"]) for f in files], ignore_index=True)
    mc = pd.DataFrame({
        "symbol": mc["Code"].astype(object).astype(str),
        "Date": pd.to_datetime(mc["Date"]).astype("datetime64[us]"),
        "marcap": mc["Marcap"].astype(float),
    }).sort_values("Date")
    k = keys.assign(symbol=keys["symbol"].astype(object).astype(str),
                    period_end=keys["period_end"].astype("datetime64[us]")).sort_values("period_end")
    out = pd.merge_asof(k, mc, left_on="period_end", right_on="Date", by="symbol",
                        direction="backward", tolerance=pd.Timedelta(days=tolerance_days))
    return out.drop(columns="Date")


def _currency(raw: pd.DataFrame) -> pd.Series:
    return raw["currency"] if "currency" in raw else pd.Series("KRW", index=raw.index)


def _bs_neighbor_medians(raw: pd.DataFrame) -> pd.DataFrame:
    bs = raw[(raw["basis"] == "instant") & (raw["value"] != 0)]
    bs = bs.assign(cur=_currency(bs)).drop_duplicates(["symbol", "period_end", "item"])
    bs = bs.assign(lg=np.log10(bs["value"].abs().astype(float)))
    per = bs[["symbol", "cur", "period_end"]].drop_duplicates(["symbol", "period_end"])
    per = per.sort_values(["symbol", "period_end"])
    per["o"] = per.groupby(["symbol", "cur"]).cumcount()  # 같은 통화 구간 안에서의 순번
    bs = bs.drop(columns="cur").merge(per, on=["symbol", "period_end"])
    w = bs[["symbol", "cur", "item", "o", "period_end", "lg"]]
    out = per.set_index(["symbol", "period_end"])
    for name, d in (("prev", -1), ("next", 1)):
        nb = w[["symbol", "cur", "item", "o", "lg"]].assign(o=w["o"] - d).rename(columns={"lg": "lg_nb"})
        m = w.merge(nb, on=["symbol", "cur", "item", "o"])
        g = (m["lg"] - m["lg_nb"]).groupby([m["symbol"], m["period_end"]])
        out[f"med_{name}"] = g.median()
        out[f"n_{name}"] = g.size()
    out[["n_prev", "n_next"]] = out[["n_prev", "n_next"]].fillna(0).astype(int)
    return out.drop(columns=["o", "cur"]).reset_index()


def _near(x: pd.Series, k: pd.Series, tol: float) -> pd.Series:
    return (x - 3 * k).abs() <= tol


def detect_scale_errors(raw: pd.DataFrame, marcap: pd.DataFrame | None = None) -> pd.DataFrame:
    """보고서별 단위 오기입 판정표(``FLAG_COLUMNS``). ``status``는 ``corrected`` 또는 ``review``.

    ``raw``: 파생 항목을 만들기 전 long 행. ``marcap``: (symbol, period_end, marcap) — 없으면 끝 보고서는 review.
    ``k``는 10의 지수(3, 6, -6 …)이고, 보정값 = 원래 값 / 10^k.
    """
    r = _bs_neighbor_medians(raw)
    has_p, has_n = r["n_prev"] >= MIN_ITEMS, r["n_next"] >= MIN_ITEMS
    k_both = ((r["med_prev"] + r["med_next"]) / 2 / 3).round()
    both = has_p & has_n & (k_both != 0) & _near(r["med_prev"], k_both, SCALE_TOL) & _near(r["med_next"], k_both, SCALE_TOL)
    one_med = r["med_prev"].where(has_p & ~has_n, r["med_next"].where(has_n & ~has_p))
    k_one = (one_med / 3).round()
    edge = one_med.notna() & (k_one != 0) & _near(one_med, k_one, SCALE_TOL)
    # 유일한 이웃이 양쪽 확인으로 보정될 보고서면, 어긋남의 원인은 그 이웃이다
    g = both.groupby(r["symbol"])  # r은 (symbol, period_end) 순으로 정렬돼 있다
    nb_both = g.shift(-1, fill_value=False).where(has_n, False) | g.shift(1, fill_value=False).where(has_p, False)
    edge &= ~nb_both

    assets = raw[(raw["item"] == "자산총계") & (raw["basis"] == "instant") & (_currency(raw) == "KRW")]
    assets = assets.drop_duplicates(["symbol", "period_end"])[["symbol", "period_end", "value"]]
    if marcap is not None:
        a = assets.merge(marcap[["symbol", "period_end", "marcap"]], on=["symbol", "period_end"], how="left")
        a["mcap_log_ratio"] = np.log10(a["value"].abs().astype(float).replace(0, np.nan) / a["marcap"])
        a["mcap_rel"] = a["mcap_log_ratio"] - a.groupby("symbol")["mcap_log_ratio"].transform("median")
        r = r.merge(a[["symbol", "period_end", "mcap_log_ratio", "mcap_rel"]], on=["symbol", "period_end"], how="left")
    else:
        r["mcap_log_ratio"] = np.nan
        r["mcap_rel"] = np.nan

    edge_ok = edge & _near(r["mcap_rel"], k_one, MCAP_TOL)
    suspicious = (r["mcap_rel"].abs() >= REVIEW_MCAP_REL) | (r["mcap_log_ratio"].abs() >= REVIEW_MCAP_ABS)

    r["k"] = np.select([both, edge], [k_both * 3, k_one * 3], 0).astype(int)
    r["method"] = np.select([both, edge_ok, edge], ["both_neighbors", "edge_marcap", "edge_unconfirmed"], "marcap_only")
    r["status"] = np.where(both | edge_ok, "corrected", "review")
    flags = r[both | edge | suspicious].copy()

    meta = raw.drop_duplicates(["symbol", "period_end"])[["symbol", "period_end", "corp_name", "fiscal_year", "fiscal_q"]]
    counts = raw.groupby(["symbol", "period_end"]).size().rename("rows").reset_index()
    flags = flags.merge(meta, on=["symbol", "period_end"], how="left").merge(counts, on=["symbol", "period_end"], how="left")
    return flags[FLAG_COLUMNS].sort_values(["status", "symbol", "period_end"]).reset_index(drop=True)


def _rescale(values: pd.Series, k: pd.Series) -> pd.Series:
    """정수 그대로 10^k로 나눈다(반올림). k<0이면 곱한다. float을 거치지 않아 큰 값도 정확하다."""
    v = values.to_numpy(dtype=np.int64)
    kk = k.to_numpy(dtype=np.int64)
    out = v.copy()
    pos, neg = kk > 0, kk < 0
    d = 10 ** kk[pos]
    q, rem = np.divmod(v[pos], d)
    out[pos] = q + (2 * rem >= d)
    out[neg] = v[neg] * 10 ** (-kk[neg])
    return pd.Series(out, index=values.index)


def apply_scale_fix(raw: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    """``status == corrected``인 보고서의 모든 행 값을 10^k로 나눈다."""
    fix = flags.loc[flags["status"] == "corrected", ["symbol", "period_end", "k"]]
    if fix.empty:
        return raw
    m = raw[["symbol", "period_end"]].merge(fix, on=["symbol", "period_end"], how="left")
    hit = m["k"].notna().to_numpy()
    out = raw.copy()
    out.loc[hit, "value"] = _rescale(out.loc[hit, "value"], m.loc[hit, "k"].astype(int).set_axis(out.index[hit]))
    return out
