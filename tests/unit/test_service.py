"""공개 서비스 함수: 파라미터 검증, 리포트 생성·캐시."""


import pytest
from test_grid_incremental import _write  # 같은 디렉터리(pytest가 sys.path에 추가)

from kquant import service
from kquant.backtest import grid as grid_mod
from kquant.config import Paths
from kquant.context import DataContext

GRID = {"strategies": ["value", "momentum"], "n": [5], "size": [{"size": "all"}], "min_marcap": [0],
        "schedule": ["annual"], "initial_capital": 10_000_000, "sell_cost_rate": 0.002}
FORBIDDEN_KEYS = {"shares", "quantity", "buy_quantity", "qty", "signal", "buy_signal", "sell_signal", "target_price",
                  "weight", "amount_to_buy"}


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


@pytest.fixture
def svc_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(grid_mod, "load_grid_config", lambda: GRID)
    monkeypatch.setenv("KQUANT_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.delenv("KQUANT_REPORT_BASE_URL", raising=False)
    data = tmp_path / "data"
    data.mkdir()
    _write(data, "2019-06-28")
    ctx = DataContext(Paths(data))
    grid_mod.run_grid(ctx, ctx.paths.grid_dir, full=True, log=lambda *a: None)
    return ctx


def test_strategy_report_and_cache(svc_ctx, monkeypatch):
    r = service.strategy_report(svc_ctx, {"strategy": "momentum", "n": 5})
    assert r["ok"], r
    assert r["key"] == "momentum|n5|all|mc0|annual" and (r["start_year"], r["end_year"]) == (2018, 2019)
    assert "/" not in r["report"] and "\\" not in r["report"]  # 공개 응답에 서버 경로 없음
    html = (svc_ctx.paths.report_dir / r["report"]).read_text(encoding="utf-8")
    assert "전략 성과 리포트" in html and "투자 권유가 아닙니다" in html and "plotly" in html.lower()
    assert "매도 0.20%(증권거래세)" in html  # 그리드에 쓴 비용이 한계 문구에 반영
    assert "출자전환을 동반한 감자" in html  # 2026-11-10: 회생 감자 손실 미반영 가능성 고지(STX 사례)
    assert r["summary"]["strategy"]["start"] == "2018-04-10"
    # 2026-11-13: 표시 문자열(비율 → %)을 함께 준다. LLM이 직접 환산하지 않게
    st, disp = r["summary"]["strategy"], r["display"]["strategy"]
    assert disp["cagr"] == f"{st['cagr'] * 100:,.2f}%" and disp["mdd"].endswith("%") and "%" not in disp["sharpe"]
    assert set(r["display"]["benchmarks"]) == set(r["summary"]["benchmarks"])
    monkeypatch.setenv("KQUANT_REPORT_BASE_URL", "https://example.test/r/")
    r2 = service.strategy_report(svc_ctx, {"strategy": "momentum", "n": 5})
    assert r2["report"].startswith("https://example.test/r/report-") and r2["report"].endswith(".html")
    # 연도 구간: 2019만 → 직전 연도 마지막 거래일 값을 기준점으로 2019년 수익률 전체를 포함
    r3 = service.strategy_report(svc_ctx, {"strategy": "momentum", "n": 5, "start_year": 2019, "end_year": 2019})
    assert r3["ok"] and r3["summary"]["strategy"]["start"] == "2018-12-31" and r3["report"] != r2["report"]


def test_year_ranges_match_series_and_have_distinct_cache(svc_ctx):
    from kquant.backtest.grid import load_result

    s, _, _ = load_result(svc_ctx.paths.grid_dir, "momentum|n5|all|mc0|annual")
    last = s.groupby(s.index.year).last()
    names = set()
    for sy, ey in [(2018, 2018), (2018, 2019), (2019, 2019)]:
        r = service.strategy_report(svc_ctx, {"strategy": "momentum", "n": 5, "start_year": sy, "end_year": ey})
        assert r["ok"] and "disclaimer" in r
        base = s.iloc[0] if sy == 2018 else last[sy - 1]
        assert r["summary"]["strategy"]["total_return"] == pytest.approx(last[ey] / base - 1, abs=1e-4)
        names.add(r["report"])
    assert len(names) == 3


def test_year_window_and_yearly_returns():
    import pandas as pd

    from kquant.reports.metrics import year_window, yearly_returns

    v = pd.Series([100.0, 110.0, 121.0, 133.1], index=pd.to_datetime(["2019-12-30", "2019-12-31", "2020-01-02",
                                                                       "2020-12-30"]))
    w = year_window(v, 2020, 2020)
    assert list(w.index.strftime("%Y-%m-%d")) == ["2019-12-31", "2020-01-02", "2020-12-30"]
    assert yearly_returns(w).loc[2020] == pytest.approx(133.1 / 110 - 1)  # 1월 2일 수익률도 포함
    assert list(year_window(v, 2019, 2019).index.year) == [2019, 2019]


def test_report_param_errors(svc_ctx):
    cases = [({}, "bad_strategy"), ({"strategy": "lynch"}, "bad_strategy"),
             ({"strategy": ["value", "momentum"]}, "bad_strategy"),
             ({"strategy": "value", "n": 7}, "bad_n"), ({"strategy": "value", "size": "mid"}, "bad_size"),
             ({"strategy": "value", "size_cut": 0.5}, "unknown_key"),
             ({"strategy": "value", "min_marcap": "100억"}, "unknown_key"),
             ({"strategy": "value", "schedule": "nov_apr"}, "bad_schedule"),
             ({"strategy": "value", "start_year": 2017}, "bad_years"),
             ({"strategy": "value", "end_year": 2020}, "bad_years"),  # 데이터 기준일(2019) 이후
             ({"strategy": "value", "start_year": 2019, "end_year": 2018}, "bad_years"),
             ({"strategy": "value", "start_year": "2018"}, "bad_years"),
             ({"strategy": "value", "path": "../../etc"}, "unknown_key"), ("value", "invalid_params")]
    for params, code in cases:
        r = service.strategy_report(svc_ctx, params)
        assert r["ok"] is False and r["problem"]["code"] == code, (params, r)
        assert "disclaimer" in r
    # 그리드에 없는 키(합성 그리드는 n=5만 계산)
    r = service.strategy_report(svc_ctx, {"strategy": "value", "n": 10})
    assert r["ok"] is False and r["problem"]["code"] == "not_in_grid"


def test_no_recommendation_entrypoint():
    """2026-11-13 결정: 종목 추천 기능 없음(공개 서비스·CLI 모두)."""
    from kquant import cli

    assert not hasattr(service, "recommend")
    with pytest.raises(SystemExit):
        cli.main(["recommend", "{}"])


def test_strategy_options_and_catalog(svc_ctx):
    o = service.strategy_options()
    assert {s["id"] for s in o["strategies"]} == {"value", "salesmt", "momentum"}
    c = service.catalog(svc_ctx)
    assert c["ok"] and c["data"]["price_latest_date"] == "2019-06-28"
