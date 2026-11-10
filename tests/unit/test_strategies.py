import numpy as np
import pandas as pd
import pytest

from kquant.prices.anomaly import abnormal_days
from kquant.strategies.base import combine, rank_strategies
from kquant.strategies.momentum import add_momentum
from kquant.universe import UniverseParams, build_universe

D = pd.Timestamp("2025-10-31")


def _prices(rows):
    df = pd.DataFrame(rows, columns=["Code", "Name", "Market", "Marcap"])
    return df.assign(Date=D, Close=1000.0, adj_close=1000.0, Volume=100.0)


def _snap(symbols, **cols):
    base = {"symbol": symbols, "currency": "KRW", "period_end": pd.Timestamp("2025-06-30"),
            "자본총계": 100.0, "자본금": 10.0, "지배당기순이익_ttm": 10.0}
    base.update(cols)
    return pd.DataFrame(base)


def test_universe_rules_and_reasons():
    p = _prices([("A", "정상", "KOSPI", 100), ("B", "가나금융", "KOSPI", 100), ("C", "코넥스", "KONEX", 100),
                 ("D", "재무없음", "KOSDAQ", 100), ("E", "외화", "KOSDAQ", 100), ("F", "잠식", "KOSPI", 100),
                 ("G", "적자", "KOSPI", 100), ("H", "오래됨", "KOSPI", 100), ("I", "에이홀딩스", "KOSDAQ", 100)])
    s = _snap(["A", "B", "C", "E", "F", "G", "H", "I"],
              currency=["KRW", "KRW", "KRW", "USD", "KRW", "KRW", "KRW", "KRW"],
              자본총계=[100, 100, 100, 100, 5, 100, 100, 100],
              지배당기순이익_ttm=[10, 10, 10, 10, 10, -1, 10, 10],
              period_end=pd.to_datetime(["2025-06-30"] * 6 + ["2024-03-31", "2025-06-30"]))
    u, log = build_universe(p, s)
    assert list(u["symbol"]) == ["A"]
    assert dict(zip(log["symbol"], log["reason"], strict=True)) == {
        "B": "name", "C": "market", "D": "no_fin", "E": "currency", "F": "impaired", "G": "loss",
        "H": "stale_fin", "I": "name"}


def test_universe_excludes_halted():
    p = _prices([("A", "정상", "KOSPI", 100), ("B", "정지", "KOSPI", 100)])
    p.loc[1, "Volume"] = 0.0
    u, log = build_universe(p, _snap(["A", "B"]))
    assert list(u["symbol"]) == ["A"] and log.set_index("symbol").loc["B", "reason"] == "halted"


def test_grid_pick_uses_only_rebalance_day_trading():
    from kquant.backtest.grid import GridKey, PeriodCandidates, abnormal_held, pick

    key = GridKey(("value",), 2, "all", None, 0.0, "annual")
    ranked = pd.DataFrame({"symbol": ["A", "B", "C", "D"], "rank": [1, 2, 3, 4]})
    ab = pd.DataFrame({"Code": ["A"], "Date": [pd.Timestamp("2025-12-01")], "ret": [-0.8]})
    c = PeriodCandidates(D, {(key.universe, key.strategies): ranked}, {key.universe: 4}, ab, {"A", "C", "D"},
                         pd.Series(dtype=float))
    picks, excl = pick(c, key)
    assert picks == ["A", "C"]  # A는 이후 이상 수익률이 있어도 선정(미래 정보 미사용), B는 리밸런싱일 거래정지
    assert excl == [{"symbol": "B", "reason": "no_trade_on_rebalance"}]
    assert abnormal_held(c, picks) == [{"symbol": "A", "date": "2025-12-01", "ret": -0.8}]


def test_universe_size_cuts():
    p = _prices([(c, c, "KOSPI", m) for c, m in zip("ABCDEFGHIJ", range(10, 110, 10), strict=True)])
    s = _snap(list("ABCDEFGHIJ"))
    small, _ = build_universe(p, s, UniverseParams("small", 0.3))
    large, _ = build_universe(p, s, UniverseParams("large", 0.7))
    floor, _ = build_universe(p, s, UniverseParams(min_marcap=55))
    assert list(small["symbol"]) == ["A", "B"]  # 백분위 0.1, 0.2 < 0.3
    assert list(large["symbol"]) == ["G", "H", "I", "J"]  # 백분위 ≥ 0.7
    assert list(floor["symbol"]) == ["F", "G", "H", "I", "J"]
    with pytest.raises(ValueError):
        UniverseParams("small")


def _u():
    return pd.DataFrame({
        "symbol": ["A", "B", "C", "D"], "marcap": [100.0, 100, 100, 100],
        "지배당기순이익_ttm": [10.0, 5, 20, np.nan], "매출액_ttm": [100.0, 200, 50, 10],
        "지배자본총계": [50.0, 80, 20, 10], "영업활동현금흐름_ttm": [10.0, -5, 30, 1],
        "영업이익_q": [10.0, 30, 5, -1], "영업이익_q_1y": [5.0, 10, 10, 1],
        "adj_close": [120.0, 90, 200, 100], "mom_6m": [0.2, -0.1, 1.0, np.nan],
    })


def test_value_hand_computed():
    r = rank_strategies(_u(), ["value"])["value"].set_index("symbol")
    # 역수 순위(클수록 1위, 결측 최하위): E/P A2 B3 C1 D4 · S/P A2 B1 C3 D4 · B/P A2 B1 C3 D4 · CF/P A2 B4 C1 D3
    assert r.loc["A", "score"] == 2.0 and r.loc["B", "score"] == 2.25
    assert r.loc["C", "score"] == 2.0 and r.loc["D", "score"] == 3.75
    assert list(r.sort_values("rank").index) == ["A", "C", "B", "D"]  # 동점(A,C)은 symbol 순


def test_salesmt_only_positive_both_years():
    r = rank_strategies(_u(), ["salesmt"])["salesmt"]
    assert list(r["symbol"]) == ["B", "A", "C"]  # 성장률 2.0, 1.0, -0.5. D(올해 적자)는 제외
    assert r["op_income_growth"].round(3).tolist() == [2.0, 1.0, -0.5]


def test_momentum_and_combine():
    res = rank_strategies(_u(), ["momentum", "salesmt"])
    assert list(res["momentum"]["symbol"]) == ["C", "A", "B"]  # D는 시작 가격 없음
    c = combine(res).set_index("symbol")
    # 교집합 A,B,C. 평균 순위 A (2+2)/2=2, B (3+1)/2=2, C (1+3)/2=2 → 모두 동점이라 symbol 순
    assert list(c.sort_values("rank").index) == ["A", "B", "C"]


def test_add_momentum_uses_calendar_6m_start():
    hist = pd.DataFrame({"Code": ["A", "A", "A", "B"],
                         "Date": pd.to_datetime(["2025-04-29", "2025-04-30", "2025-05-02", "2025-05-02"]),
                         "adj_close": [50.0, 60.0, 70.0, 10.0]})
    u = add_momentum(pd.DataFrame({"symbol": ["A", "B"], "adj_close": [120.0, 20.0]}), hist, D)
    # 시작일 = 2025-04-30(10/31의 6개월 전), 그 이하 마지막 거래 = 4/30 → 120/60 − 1
    assert u.loc[0, "mom_6m"] == 1.0 and u.loc[0, "mom_start_date"] == pd.Timestamp("2025-04-30")
    assert np.isnan(u.loc[1, "mom_6m"])  # B는 시작일 이전 가격 없음


def test_abnormal_days_limits():
    p = pd.DataFrame({"Code": ["A"] * 4 + ["B"] * 2,
                      "Date": pd.to_datetime(["2015-01-02", "2015-01-05", "2016-01-04", "2016-01-05",
                                              "2016-01-04", "2016-01-05"]),
                      "adj_close": [100.0, 120.0, 100.0, 129.0, 100.0, 140.0]})
    a = abnormal_days(p)
    # 2015-01-05 +20%(당시 제한 15%) 이상, 2016 +29% 정상, B +40% 이상
    assert list(zip(a["Code"], a["Date"].dt.strftime("%Y-%m-%d"), strict=True)) == [
        ("A", "2015-01-05"), ("B", "2016-01-05")]
