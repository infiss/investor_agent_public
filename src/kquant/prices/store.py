"""가격 저장소: marcap 연도 파일을 필요할 때만 읽고 수정종가(adj_close)를 붙여 메모리에 캐시한다.

조건 검색(최근 거래일 단면), 전략(모멘텀), 백테스트(기간 가격)가 공통으로 쓴다.
- 거래정지일: 2020년까지 파일에는 행이 없고, 2021년 이후 파일에는 직전 가격·거래량 0 행이 있다.
  단면 조회는 그날 행이 있는 종목을 모두 돌려주므로, 실제 거래 여부는 ``Volume > 0``으로 판단한다.
- ``Market``의 ``KOSDAQ GLOBAL``은 ``KOSDAQ``으로 합친다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from kquant.prices.adjust import apply_adjustment, normalize_marcap

COLUMNS = ["Date", "Code", "Name", "Market", "Close", "Volume", "Amount", "Marcap", "Stocks"]
MARKET_ALIASES = {"KOSDAQ GLOBAL": "KOSDAQ"}


class PriceStore:
    def __init__(self, marcap_dir: Path, events: pd.DataFrame):
        self.marcap_dir = Path(marcap_dir)
        self.events = events
        self._years: dict[int, pd.DataFrame] = {}

    @classmethod
    def from_paths(cls, paths) -> PriceStore:
        events = pd.read_parquet(paths.adj_events) if paths.adj_events.exists() else pd.DataFrame(
            columns=["Code", "Date", "factor"])
        if not events.empty:
            events["Date"] = pd.to_datetime(events["Date"]).astype("datetime64[ns]")
        return cls(paths.marcap_raw, events)

    def available_years(self) -> list[int]:
        return sorted(int(f.stem.split("-")[1]) for f in self.marcap_dir.glob("marcap-*.parquet"))

    def year(self, y: int) -> pd.DataFrame:
        if y not in self._years:
            f = self.marcap_dir / f"marcap-{y}.parquet"
            if not f.exists():
                self._years[y] = pd.DataFrame(columns=[*COLUMNS, "adj_close"])
            else:
                df = normalize_marcap(pd.read_parquet(f))
                df = df[[c for c in COLUMNS if c in df.columns]]
                df["Market"] = df["Market"].replace(MARKET_ALIASES)
                df = apply_adjustment(df, self.events).drop(columns="adj_mult")
                for c in ("Code", "Name", "Market"):
                    df[c] = df[c].astype("category")
                self._years[y] = df.sort_values(["Date", "Code"]).reset_index(drop=True)
        return self._years[y]

    def range(self, start, end) -> pd.DataFrame:
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        parts = [self.year(y) for y in range(start.year, end.year + 1)]
        parts = [p for p in parts if not p.empty]
        if not parts:
            return pd.DataFrame(columns=[*COLUMNS, "adj_close"])
        parts = [p[(p["Date"] >= start) & (p["Date"] <= end)] for p in parts]
        df = pd.concat(parts, ignore_index=True)
        for c in ("Code", "Name", "Market"):
            df[c] = df[c].astype(str)
        return df

    def latest_date(self) -> pd.Timestamp:
        years = self.available_years()
        for y in reversed(years):
            d = self.year(y)
            if not d.empty:
                return d["Date"].max()
        raise FileNotFoundError(f"marcap 파일이 없습니다: {self.marcap_dir}")

    def trading_days(self, start, end) -> pd.DatetimeIndex:
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        days: set = set()
        for y in range(start.year, end.year + 1):
            d = self.year(y)
            if not d.empty:
                u = d["Date"].unique()
                days.update(u[(u >= start) & (u <= end)])
        return pd.DatetimeIndex(sorted(days))

    def last_trading_day(self, on_or_before) -> pd.Timestamp | None:
        """``on_or_before`` 이하 마지막 거래일(최대 1년 전까지 찾는다)."""
        t = pd.Timestamp(on_or_before)
        days = self.trading_days(t - pd.Timedelta(days=366), t)
        return days[-1] if len(days) else None

    def first_trading_day(self, on_or_after) -> pd.Timestamp | None:
        t = pd.Timestamp(on_or_after)
        days = self.trading_days(t, t + pd.Timedelta(days=366))
        return days[0] if len(days) else None

    def first_trade_after(self, codes, after) -> tuple[dict[str, tuple[pd.Timestamp, float]], set[str]]:
        """``after`` 다음 날부터 처음 실제 거래된 날(거래량 > 0)과 수정종가. (찾은 것, 이후 행이 있는 종목)."""
        after = pd.Timestamp(after)
        left, found, seen = set(codes), {}, set()
        for y in [y for y in self.available_years() if y >= after.year]:
            if not left:
                break
            d = self.year(y)
            sub = d[d["Code"].isin(left) & (d["Date"] > after)]
            seen |= set(sub["Code"].astype(str))
            tr = sub[sub["Volume"] > 0].sort_values("Date").drop_duplicates("Code")
            for r in tr.itertuples():
                found[str(r.Code)] = (r.Date, float(r.adj_close))
            left -= set(found)
        return found, seen

    def cross_section(self, date) -> pd.DataFrame:
        """그 날짜(정확히) 거래 행. 컬럼: COLUMNS + adj_close."""
        d = pd.Timestamp(date)
        y = self.year(d.year)
        out = y[y["Date"] == d].reset_index(drop=True)
        for c in ("Code", "Name", "Market"):
            out[c] = out[c].astype(str)
        return out
