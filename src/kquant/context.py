"""데이터 접근 컨텍스트: 재무 long·가격 저장소를 지연 로드하고 기준일별 스냅샷·단면 지표를 캐시한다.

공개 도구(조건 검색)와 백테스트가 같은 컨텍스트를 쓴다. 읽기 전용이다.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from functools import cached_property

import pandas as pd

from kquant.config import Paths, load_paths
from kquant.prices.store import PriceStore

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MIN_DATE = pd.Timestamp("2016-01-01")


class DateError(ValueError):
    pass


class DataContext:
    def __init__(self, paths: Paths | None = None, cache_size: int = 8):
        self.paths = paths or load_paths()
        self._cache: OrderedDict = OrderedDict()
        self._cache_size = cache_size

    @cached_property
    def long(self) -> pd.DataFrame:
        return pd.read_parquet(self.paths.fin_long)

    @cached_property
    def prices(self) -> PriceStore:
        return PriceStore.from_paths(self.paths)

    def resolve_date(self, as_of) -> pd.Timestamp:
        """``"latest"`` 또는 ``YYYY-MM-DD`` → 그 날짜 이하 마지막 거래일."""
        latest = self.prices.latest_date()
        if as_of in (None, "latest"):
            return latest
        if not isinstance(as_of, str) or not DATE_RE.match(as_of):
            raise DateError("as_of는 'latest' 또는 YYYY-MM-DD 형식이어야 합니다")
        try:
            t = pd.Timestamp(as_of)
        except ValueError as e:
            raise DateError(f"날짜를 해석할 수 없습니다: {as_of}") from e
        if t < MIN_DATE or t > latest + pd.Timedelta(days=7):
            raise DateError(f"as_of는 {MIN_DATE.date()}–{latest.date()} 범위여야 합니다")
        d = self.prices.last_trading_day(min(t, latest))
        if d is None:
            raise DateError(f"{as_of} 이전 거래일이 없습니다")
        return d

    def _cached(self, key, fn):
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        val = fn()
        self._cache[key] = val
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return val

    def snapshot(self, date: pd.Timestamp) -> pd.DataFrame:
        from kquant.fin.snapshot import snapshot

        return self._cached(("snap", date), lambda: snapshot(self.long, date))

    def features(self, date: pd.Timestamp) -> pd.DataFrame:
        from kquant.catalog.features import build_features

        return self._cached(("feat", date), lambda: build_features(self.prices.cross_section(date),
                                                                    self.snapshot(date)))

    def data_status(self) -> dict:
        d = self.prices.latest_date()
        avail = self.long[self.long["available_date"] <= d]
        return {
            "price_latest_date": str(d.date()),
            "fin_latest_period_end": str(avail["period_end"].max().date()),
            "fin_note": "재무값은 기준일까지 법정 제출 기한(분기 +45일, 연간 +90일)이 지난 최신 보고서 기준",
        }
