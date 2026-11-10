import numpy as np
import pandas as pd
import pytest

from kquant.backtest.benchmark import market_index
from kquant.backtest.engine import simulate_period
from kquant.backtest.schedule import periods

DAYS = pd.to_datetime(["2020-11-02", "2020-11-03", "2020-11-04", "2020-11-05"])


def test_simulate_hand_computed_and_identity():
    buy = pd.Series({"A": 1000.0, "B": 3000.0})
    adj = pd.DataFrame({"A": [10.0, 11.0, np.nan, 12.0], "B": [30.0, 30.0, 27.0, np.nan]}, index=DAYS)
    r = simulate_period(1_000_000, buy, adj)
    # 배분 500,000원: A 500주(500,000원), B 166주(498,000원), 현금 2,000원
    assert r.holdings["shares"].tolist() == [500, 166] and r.cash == 2_000
    expected = [1_000_000, 2_000 + 550_000 + 498_000, 2_000 + 550_000 + 448_200, 2_000 + 600_000 + 448_200]
    assert r.values.round(6).tolist() == expected  # 거래 없는 날은 직전 가격 유지(상폐 시 마지막 가격 고정)


def test_simulate_split_inside_period():
    # 2:1 분할: 원가격 1000 → 500이지만 수정종가는 연속이므로 평가액이 변하지 않는다
    buy = pd.Series({"A": 1000.0})
    adj = pd.DataFrame({"A": [500.0, 500.0, 550.0, 550.0]}, index=DAYS)  # 분할 반영된 수정종가
    r = simulate_period(100_000, buy, adj)
    assert r.values.tolist() == pytest.approx([100_000, 100_000, 110_000, 110_000])


def test_simulate_cost_and_empty():
    buy = pd.Series({"A": 1000.0})
    adj = pd.DataFrame({"A": [10.0, 10.0, 10.0, 10.0]}, index=DAYS)
    r = simulate_period(100_000, buy, adj, buy_rate=0.01, sell_rate=0.01)
    # floor(100000 / 1010) = 99주, 매수 99,000 + 비용 990 → 현금 10, 매도 비용 990
    assert r.values.iloc[0] == pytest.approx(100_000 - 990)
    assert r.values.iloc[-1] == pytest.approx(100_000 - 990 - 990)
    t = simulate_period(100_000, buy, adj, sell_rate=0.002)  # 매도세만: 100주, 매도 10만 원 × 0.2% = 200
    assert t.values.iloc[0] == 100_000 and t.values.iloc[-1] == pytest.approx(100_000 - 200)
    e = simulate_period(5_000, pd.Series(dtype=float), pd.DataFrame(index=DAYS))
    assert e.values.tolist() == [5_000] * 4
    with pytest.raises(ValueError):
        simulate_period(1, buy, pd.DataFrame({"A": [np.nan, 1, 1, 1]}, index=DAYS))


def test_schedules():
    days = pd.bdate_range("2018-01-02", "2019-06-28")
    an = periods("annual", days)
    # 2018-03-31(토) → 기한 후 첫 거래일 04-02(월) + 5거래일 = 판단일 04-09, 매수 04-10.
    # 2019-03-31(일) → 04-01 + 5거래일 = 판단일 04-08, 매수 04-09
    assert [(p.id, str(p.rebalance_date.date()), str(p.end_date.date()), p.closed) for p in an] == [
        ("2018-03", "2018-04-10", "2019-04-08", True), ("2019-03", "2019-04-09", "2019-06-28", False)]
    q = periods("quarterly", days)
    assert [(p.id, str(p.rebalance_date.date()), str(p.end_date.date())) for p in q] == [
        ("2018-03", "2018-04-10", "2018-05-22"), ("2018-05", "2018-05-23", "2018-08-21"),
        ("2018-08", "2018-08-22", "2018-11-21"), ("2018-11", "2018-11-22", "2019-04-08"),
        ("2019-03", "2019-04-09", "2019-05-22"), ("2019-05", "2019-05-23", "2019-06-28")]
    assert [p.closed for p in q] == [True] * 5 + [False]
    # 다음 판단일까지만 데이터가 있으면(다음 매수일 없음) 아직 진행 중
    short = periods("quarterly", days[days <= "2018-05-22"])
    assert short[-1].closed is False and short[-1].end_date == pd.Timestamp("2018-05-22")


def test_decision_date_sees_deadline_reports():
    # 판단일(리밸런싱 직전 거래일)은 항상 기한 이후 첫 거래일 + 5거래일 → 결산일 + 45/90일 보고서를 모두 쓸 수 있다
    days = pd.bdate_range("2018-01-02", "2026-11-05")
    for p in periods("quarterly", days):
        decision = days[days < p.rebalance_date][-1]
        assert decision >= p.begin, p
        assert ((days >= p.begin) & (days < decision)).sum() == 5, p


def test_market_index_cap_weighted():
    d = pd.to_datetime(["2020-01-02", "2020-01-03"])
    p = pd.DataFrame({"Date": [d[0], d[0], d[1], d[1]], "Code": ["A", "B", "A", "B"],
                      "Market": "KOSPI", "adj_close": [100.0, 10.0, 110.0, 9.0], "Marcap": [300.0, 100.0, 330, 90]})
    idx = market_index(p)
    # 전일 시총 가중: (300×10% + 100×−10%) / 400 = +5%
    assert idx["KOSPI"].round(6).tolist() == [100.0, 105.0]


def test_simulate_exit_rel_settles_last_day():
    buy = pd.Series({"A": 1000.0, "B": 1000.0})
    adj = pd.DataFrame({"A": [10.0, 11.0, 11.0, 11.0], "B": [10.0, 10.0, 12.0, 12.0]}, index=DAYS)
    # A는 기간 말 거래정지 → 재개 첫날 수정종가 5.5(매수 대비 0.55)로 정산
    r = simulate_period(100_000, buy, adj, exit_rel=pd.Series({"A": 0.55}))
    assert r.values.iloc[-2] == pytest.approx(55_000 + 60_000)  # 정산 전날까지는 직전 가격 평가
    assert r.values.iloc[-1] == pytest.approx(27_500 + 60_000)


def test_settlement_statuses():
    from types import SimpleNamespace

    from kquant.backtest.grid import settlement
    from kquant.backtest.schedule import Period

    end = pd.Timestamp("2020-11-05")
    p = Period(pd.Timestamp("2020-11-01"), end, DAYS[0], end, closed=True)
    win = pd.DataFrame({"Date": list(DAYS) * 4, "Code": ["A"] * 4 + ["B"] * 4 + ["C"] * 4 + ["D"] * 4,
                        "Volume": [1, 1, 0, 0] + [1, 1, 1, 1] + [1, 0, 0, 0] + [1, 1, 1, 0],
                        "adj_close": [10, 11, 11, 11] + [10] * 4 + [9, 9, 9, 9] + [10, 10, 8, 8]}, dtype=object)
    win["Volume"] = win["Volume"].astype(float)
    win["adj_close"] = win["adj_close"].astype(float)

    class Prices:
        def first_trade_after(self, codes, after):
            assert after == end and set(codes) == {"A", "C", "D"}
            return {"A": (pd.Timestamp("2020-12-01"), 5.5)}, {"A", "C"}  # C는 아직 정지, D는 이후 행 없음

    out = settlement(SimpleNamespace(prices=Prices()), p, {"A", "B", "C", "D"}, win)
    assert set(out) == {"A", "C", "D"}  # B는 기간 말 정상 거래
    assert out["A"]["status"] == "resumed" and out["A"]["exit_adj"] == 5.5 and out["A"]["last_adj"] == 11.0
    assert out["C"]["status"] == "pending" and out["C"]["exit_adj"] == 9.0
    assert out["D"]["status"] == "delisted" and out["D"]["exit_adj"] == 8.0
