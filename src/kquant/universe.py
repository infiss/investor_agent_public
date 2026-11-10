"""공통 유니버스 (spec §7). 세 전략 모두 같은 유니버스에서 순위를 매긴다.

판단 기준일 ``d``(리밸런싱일 직전 거래일)의 가격 단면과 ``snapshot(d)``만 쓴다(look-ahead 없음).

제외 규칙(순서대로, 사유를 기록):
1. ``market``: KOSPI·KOSDAQ 외(KONEX 등)
1-1. ``halted``: 기준일 거래량 0(거래정지. 2021년 이후 marcap에는 정지일도 직전 가격·거래량 0 행이 있다)
2. ``name``: 회사명에 금융·지주·스팩·중국·홀딩스·리츠 포함
3. ``no_fin``: 기준일에 쓸 수 있는 재무 보고서가 없음
4. ``stale_fin``: 최신 보고서 결산기준일이 기준일보다 ``STALE_MONTHS``개월 넘게 지남(보고 중단)
5. ``currency``: 재무 보고 통화가 KRW가 아님
6. ``impaired``: 자본잠식(자본총계 ≤ 자본금, 또는 값 없음)
7. ``loss``: 당기순이익(지배주주, TTM) ≤ 0 또는 값 없음
8. ``min_marcap``: 시가총액 하한 미만(그리드 파라미터, 선택)
9. ``size``: 시총 백분위 컷(그리드 파라미터, 선택). small은 하위 cut 미만, large는 상위(백분위 ≥ cut).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

EXCLUDE_NAME_WORDS = ("금융", "지주", "스팩", "중국", "홀딩스", "리츠")
MARKETS = ("KOSPI", "KOSDAQ")
STALE_MONTHS = 15


@dataclass(frozen=True)
class UniverseParams:
    size: str = "all"  # all | small | large
    size_cut: float | None = None  # small: 하위 비율, large: 이 백분위 이상
    min_marcap: float | None = None  # 원

    def __post_init__(self):
        if self.size not in ("all", "small", "large"):
            raise ValueError("size는 all, small, large 중 하나")
        if self.size != "all" and not (self.size_cut and 0 < self.size_cut < 1):
            raise ValueError("small/large에는 0–1 사이 size_cut이 필요")


def build_universe(prices: pd.DataFrame, snap: pd.DataFrame, params: UniverseParams | None = None,
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(유니버스 표, 제외 기록)을 돌려준다.

    ``prices``: 기준일 가격 단면(PriceStore.cross_section), ``snap``: 같은 기준일의 재무 스냅샷.
    유니버스 표 컬럼: symbol, name, market, close, adj_close, marcap + 스냅샷 컬럼.
    제외 기록 컬럼: symbol, name, reason.
    """
    params = params or UniverseParams()
    d = pd.Timestamp(prices["Date"].iloc[0]) if len(prices) else None
    px = pd.DataFrame({
        "symbol": prices["Code"].astype(str), "name": prices["Name"].astype(str),
        "market": prices["Market"].astype(str), "close": prices["Close"].astype(float),
        "adj_close": prices["adj_close"].astype(float), "marcap": prices["Marcap"].astype(float),
        "volume": prices["Volume"].astype(float),
    })
    df = px.merge(snap, on="symbol", how="left")
    excluded: list[pd.DataFrame] = []

    def drop(mask: pd.Series, reason: str):
        nonlocal df
        mask = mask.fillna(False).astype(bool)
        if mask.any():
            excluded.append(df.loc[mask, ["symbol", "name"]].assign(reason=reason))
        df = df[~mask]

    drop(~df["market"].isin(MARKETS), "market")
    drop(df["volume"] <= 0, "halted")
    drop(df["name"].str.contains("|".join(EXCLUDE_NAME_WORDS), regex=True), "name")
    drop(df["period_end"].isna(), "no_fin")
    if d is not None:
        drop(df["period_end"] < d - pd.DateOffset(months=STALE_MONTHS), "stale_fin")
    drop(df["currency"] != "KRW", "currency")
    drop(~(df["자본총계"] > df["자본금"]), "impaired")
    drop(~(df["지배당기순이익_ttm"] > 0), "loss")
    if params.min_marcap:
        drop(df["marcap"] < params.min_marcap, "min_marcap")
    if params.size != "all":
        pct = df["marcap"].rank(pct=True, method="average")
        drop(pct >= params.size_cut if params.size == "small" else pct < params.size_cut, "size")

    log = pd.concat(excluded, ignore_index=True) if excluded else pd.DataFrame(columns=["symbol", "name", "reason"])
    return df.reset_index(drop=True), log
