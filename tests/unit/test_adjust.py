import pandas as pd
import pytest

from kquant.prices.adjust import apply_adjustment, build_events, compute_events, update_events


def make(rows):
    df = pd.DataFrame(rows, columns=["Code", "Date", "Close", "Changes"])
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def test_no_event_means_no_drift():
    p = make([
        ("000001", "2024-01-02", 1000, 0),
        ("000001", "2024-01-03", 1013, 13),
        ("000001", "2024-01-04", 1007, -6),
    ])
    ev = compute_events(p)
    assert ev.empty
    adj = apply_adjustment(p, ev)
    assert (adj["adj_close"] == p["Close"]).all()


def test_split_50_to_1():
    # 삼성전자 2018-05-04 패턴: 전일 2,650,000 → 기준가 53,000
    p = make([
        ("005930", "2018-05-03", 2650000, 0),
        ("005930", "2018-05-04", 51900, -1100),
        ("005930", "2018-05-08", 52600, 700),
    ])
    ev = compute_events(p)
    assert len(ev) == 1
    assert ev.loc[0, "factor"] == pytest.approx(0.02)
    adj = apply_adjustment(p, ev).set_index("Date")["adj_close"]
    assert adj["2018-05-03"] == pytest.approx(53000)
    assert adj["2018-05-04"] == 51900
    # 분할일 수익률은 기준가 대비 등락률과 같다
    assert adj["2018-05-04"] / adj["2018-05-03"] - 1 == pytest.approx(-1100 / 53000)


def test_events_after_window_are_applied():
    full = make([
        ("000002", "2023-12-28", 10000, 0),
        ("000002", "2024-01-02", 2000, 0),   # 5:1 분할 (기준가 2000)
    ])
    ev = compute_events(full)
    window = full[full["Date"].dt.year == 2023]
    adj = apply_adjustment(window, ev)
    assert adj["adj_close"].iloc[0] == pytest.approx(2000)


def test_codes_do_not_leak_into_each_other():
    p = make([
        ("000001", "2024-01-02", 1000, 0),
        ("000002", "2024-01-02", 50000, 0),
        ("000001", "2024-01-03", 1010, 10),
        ("000002", "2024-01-03", 10000, 0),  # 000002만 5:1 분할
    ])
    ev = compute_events(p)
    assert list(ev["Code"]) == ["000002"]
    adj = apply_adjustment(p, ev)
    a1 = adj[adj.Code == "000001"]["adj_close"].tolist()
    assert a1 == [1000, 1010]


def test_year_boundary_event_detected(tmp_path):
    y1 = make([("000003", "2023-12-28", 9000, 0)])
    y2 = make([("000003", "2024-01-02", 3000, 0)])  # 3:1 분할이 연초 첫 거래일
    f1, f2 = tmp_path / "marcap-2023.parquet", tmp_path / "marcap-2024.parquet"
    y1.to_parquet(f1)
    y2.to_parquet(f2)
    ev = build_events([f1, f2])
    assert len(ev) == 1 and ev.loc[0, "factor"] == pytest.approx(1 / 3)
    # 증분 갱신도 같은 결과
    ev2 = update_events(ev.iloc[0:0], f2, [f1])
    pd.testing.assert_frame_equal(ev, ev2)


def test_first_row_and_relisting_gap_not_event():
    p = make([
        ("000004", "2024-01-02", 5000, 100),   # 첫 행: 직전 종가 없음 → 이벤트 아님
        ("000004", "2024-03-02", 5100, 100),   # 거래정지 후 재개, 기준가 = 직전 종가
    ])
    assert compute_events(p).empty
