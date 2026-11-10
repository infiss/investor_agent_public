import pandas as pd
import pytest

from kquant.catalog.features import build_features


class FakeContext:
    """DataContext 대역: 합성 가격 단면 + 합성 스냅샷으로 단면 지표를 만든다."""

    def __init__(self, prices: pd.DataFrame, snap: pd.DataFrame):
        self.date = prices["Date"].iloc[0]
        self._features = build_features(prices, snap)

    def resolve_date(self, as_of):
        from kquant.context import DateError

        if as_of not in (None, "latest", str(self.date.date())):
            raise DateError("범위 밖 날짜")
        return self.date

    def features(self, date):
        return self._features


def make_ctx() -> FakeContext:
    d = pd.Timestamp("2026-11-03")
    prices = pd.DataFrame({
        "Date": d, "Code": ["000001", "000002", "000003", "000004", "000005"],
        "Name": ["가나", "다라", "마바", "외국", "코넥"], "Market": ["KOSPI", "KOSDAQ", "KOSPI", "KOSDAQ", "KONEX"],
        "Close": [1000.0, 2000, 3000, 4000, 500], "Volume": 1.0, "Amount": 1e9,
        "Marcap": [500e9, 100e9, 2000e9, 50e9, 10e9], "Stocks": 1,
    })
    snap = pd.DataFrame({
        "symbol": ["000001", "000002", "000003", "000004", "000005"],
        "corp_name": ["가나", "다라", "마바", "외국", "코넥"],
        "currency": ["KRW", "KRW", "KRW", "USD", "KRW"], "statement_type": "CFS",
        "fiscal_year": 2026, "fiscal_q": "2Q", "period_end": pd.Timestamp("2026-06-30"),
        "available_date": pd.Timestamp("2026-08-14"),
        "매출액_ttm": [1000e9, 50e9, 3000e9, 10e6, 5e9], "매출액_ttm_1y": [800e9, 60e9, 3000e9, 5e6, -1e9],
        "매출액_q": [250e9, 10e9, 700e9, 3e6, 1e9],
        "지배당기순이익_ttm": [100e9, -5e9, 200e9, 2e6, 1e9],
        "영업이익_ttm": [120e9, -3e9, 250e9, 2e6, 1e9],
        "지배자본총계": [400e9, 80e9, 1000e9, 1e6, 5e9], "자본총계": [420e9, 80e9, 1000e9, 1e6, 5e9],
        "부채총계": [210e9, 40e9, 500e9, 1e6, 1e9], "자산총계": [630e9, 120e9, 1500e9, 2e6, 6e9],
    })
    return FakeContext(prices, snap)


@pytest.fixture
def ctx():
    return make_ctx()


@pytest.fixture(scope="session")
def real_ctx():
    """실제 dataset/ 기반 컨텍스트. 데이터가 없으면 skip."""
    from kquant.config import load_paths
    from kquant.context import DataContext

    paths = load_paths()
    if not paths.fin_long.exists() or not list(paths.marcap_raw.glob("marcap-*.parquet")):
        pytest.skip("실제 dataset/ 파일이 없음")
    return DataContext(paths)
