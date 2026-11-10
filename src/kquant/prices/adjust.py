"""수정종가 계산.

marcap의 `Changes`(전일 대비 변동액)는 거래소 기준가 대비 값이다. 따라서
    기준가_t = Close_t - Changes_t
이고, 기업 이벤트(분할·병합·증자 등)가 없으면 기준가_t == 직전 거래일 Close다.
이벤트가 있는 날만 조정계수 f_t = 기준가_t / Close_{t-1} 가 1이 아니다.

수정종가는 최신 가격을 원가격으로 두고 과거로 갈수록 이후 이벤트 계수를 곱한다.
    adj_close_t = Close_t * prod_{s > t} f_s

기존 방식(반올림된 ChangesRatio의 역누적곱 + 정수 반올림)과 달리 이벤트가 없는 날에는
오차가 생기지 않는다. 이벤트 테이블만 저장하고 수정종가는 읽을 때 계산하므로, 새 이벤트가
생겨도 과거 가격 파일을 다시 쓸 필요가 없다.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

EVENT_TOL = 1e-9
EVENT_COLUMNS = ["Code", "Date", "factor", "prev_close", "base_price"]


def normalize_marcap(df: pd.DataFrame) -> pd.DataFrame:
    """marcap 원본의 연도별 차이를 정리한다 (오타 컬럼, 종목코드 형식, 날짜 타입)."""
    df = df.rename(columns={"ChagesRatio": "ChangesRatio"})
    df["Code"] = df["Code"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(6)
    df["Date"] = pd.to_datetime(df["Date"]).astype("datetime64[ns]")
    return df


def read_marcap(files: Iterable[Path], columns: list[str] | None = None) -> pd.DataFrame:
    frames = []
    for f in files:
        df = pd.read_parquet(f)
        df = normalize_marcap(df)
        frames.append(df[columns] if columns else df)
    return pd.concat(frames, ignore_index=True)


def compute_events(prices: pd.DataFrame, prev_close: pd.Series | None = None) -> pd.DataFrame:
    """조정계수가 1이 아닌 (종목, 날짜) 목록을 만든다.

    prices: Code, Date, Close, Changes 컬럼을 가진 연속 구간 시세.
    prev_close: 구간 시작 직전의 종목별 마지막 종가 (Code 인덱스). 증분 계산 시
        이전 연도 마지막 종가를 넘겨야 구간 첫날의 이벤트를 놓치지 않는다.
    """
    p = prices[["Code", "Date", "Close", "Changes"]].sort_values(["Code", "Date"], kind="stable")
    p = p.drop_duplicates(["Code", "Date"], keep="last")
    prev = p.groupby("Code", sort=False)["Close"].shift()
    if prev_close is not None:
        first = prev.isna()
        prev = prev.where(~first, p["Code"].map(prev_close))
    base = p["Close"] - p["Changes"]
    factor = base / prev
    valid = prev.notna() & (prev > 0) & base.notna() & (base > 0)
    is_event = valid & ((factor - 1).abs() > EVENT_TOL)
    events = pd.DataFrame({
        "Code": p["Code"], "Date": p["Date"], "factor": factor,
        "prev_close": prev, "base_price": base,
    })[is_event]
    return events.reset_index(drop=True)[EVENT_COLUMNS]


def apply_adjustment(prices: pd.DataFrame, events: pd.DataFrame, price_cols=("Close",)) -> pd.DataFrame:
    """events 테이블로 수정가격 컬럼(adj_<col>)을 붙인다.

    prices의 기간과 무관하게 events 전체(이후 연도 포함)를 반영하므로, 어느 구간을
    읽어도 최신 가격 기준의 같은 수정가격이 나온다.
    """
    out = prices.copy()
    if events.empty:
        mult = pd.Series(1.0, index=out.index)
    else:
        ev = events[["Code", "Date", "factor"]].sort_values(["Code", "Date"], kind="stable")
        # 각 이벤트 시점 이후(포함) 모든 계수의 곱
        ev["cum_from"] = ev["factor"][::-1].groupby(ev["Code"][::-1]).cumprod()[::-1]
        # 가격 행 t에는 t보다 뒤에 있는 첫 이벤트의 cum_from이 곱해진다
        left = out[["Code", "Date"]].reset_index().sort_values("Date", kind="stable")
        merged = pd.merge_asof(
            left, ev[["Code", "Date", "cum_from"]].sort_values("Date", kind="stable"),
            on="Date", by="Code", direction="forward", allow_exact_matches=False,
        )
        mult = merged.set_index("index")["cum_from"].reindex(out.index).fillna(1.0)
    for col in price_cols:
        out[f"adj_{col.lower()}"] = out[col] * mult.to_numpy()
    out["adj_mult"] = mult.to_numpy()
    return out


def last_close_before(files_before: Iterable[Path]) -> pd.Series:
    """증분 계산용: 주어진 (이전 연도) 파일들에서 종목별 마지막 종가."""
    df = read_marcap(files_before, columns=["Code", "Date", "Close"])
    df = df.sort_values(["Code", "Date"], kind="stable")
    return df.groupby("Code")["Close"].last()


def build_events(marcap_files: list[Path]) -> pd.DataFrame:
    """전체 연도 파일로 이벤트 테이블을 처음부터 만든다. 연도 경계도 이어서 계산한다."""
    events = []
    prev_close: pd.Series | None = None
    for f in sorted(marcap_files):
        year = read_marcap([f], columns=["Code", "Date", "Close", "Changes"])
        events.append(compute_events(year, prev_close))
        last = year.sort_values(["Code", "Date"], kind="stable").groupby("Code")["Close"].last()
        prev_close = last if prev_close is None else last.combine_first(prev_close)
    out = pd.concat(events, ignore_index=True) if events else pd.DataFrame(columns=EVENT_COLUMNS)
    return out.sort_values(["Code", "Date"], kind="stable").reset_index(drop=True)


def update_events(existing: pd.DataFrame, year_file: Path, earlier_files: list[Path]) -> pd.DataFrame:
    """한 연도 파일이 갱신됐을 때 그 연도 이벤트만 다시 계산해 교체한다."""
    year = read_marcap([year_file], columns=["Code", "Date", "Close", "Changes"])
    y = int(year["Date"].dt.year.iloc[0])
    prev_close = last_close_before(earlier_files) if earlier_files else None
    fresh = compute_events(year, prev_close)
    keep = existing[existing["Date"].dt.year != y]
    out = pd.concat([keep, fresh], ignore_index=True)
    return out.sort_values(["Code", "Date"], kind="stable").reset_index(drop=True)


def daily_returns(adj: pd.DataFrame, col: str = "adj_close") -> pd.Series:
    """종목별 일간 수익률 (같은 종목의 직전 거래일 대비)."""
    s = adj.sort_values(["Code", "Date"], kind="stable")
    return s.groupby("Code", sort=False)[col].pct_change(fill_method=None)


__all__ = [
    "apply_adjustment",
    "build_events",
    "compute_events",
    "daily_returns",
    "last_close_before",
    "normalize_marcap",
    "read_marcap",
    "update_events",
]
