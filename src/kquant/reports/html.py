"""전략 성과 HTML 리포트 (spec §9): Plotly를 내장한 자체 완결형 파일 1개.

구성: 설정 요약 → 핵심 지표 타일 → 누적 수익(시장 지수 비교) → 낙폭 → 연도별 수익률(막대 + 표) →
지표 표 → 리밸런싱 기간 표 → 이상 수익률로 제외한 종목 → 데이터 기준일·한계·고지.
보유 종목(추천 종목)은 넣지 않는다.
"""

from __future__ import annotations

import html
import json

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from kquant.disclaimer import DISCLAIMER
from kquant.reports.metrics import clean, drawdown, full_years, summarize, year_window, yearly_returns

# dataviz 기본 팔레트 slot 1–3 (validate_palette.js 통과, aqua는 대비 경고 → 직접 라벨 + 표로 보완)
COLORS = {"light": ["#2a78d6", "#eb6834", "#1baf7a"], "dark": ["#3987e5", "#d95926", "#199e70"]}
SCHEDULE_LABELS = {"annual": "연 1회(사업보고서 제출 기한 3/31 이후 5거래일 여유를 두고 4월 초·중순)",
                   "quarterly": "분기 1회(정기 보고서 제출 기한 3/31·5/15·8/14·11/14 이후 5거래일 여유)"}
STRATEGY_LABELS = {"value": "가치(value)", "salesmt": "이익 모멘텀(salesmt)", "momentum": "가격 모멘텀(momentum)"}
REASONS = {"no_trade_on_rebalance": "리밸런싱일 거래 없음(거래정지)"}


def _pct(x, digits=1):
    return "–" if x is None else f"{x * 100:.{digits}f}%"


def _num(x, digits=2):
    return "–" if x is None else f"{x:.{digits}f}"


def describe_key(params: dict) -> list[tuple[str, str]]:
    size = {"all": "전체"}.get(params["size"]) or (
        f"소형주(시총 하위 {params['size_cut'] * 100:g}%)" if params["size"] == "small"
        else f"대형주(시총 상위 {(1 - params['size_cut']) * 100:g}%)")
    rows = [
        ("전략", " + ".join(STRATEGY_LABELS[s] for s in params["strategies"])),
        ("종목 수", f"{params['n']}개 동일 비중"),
        ("규모", size), ("리밸런싱", SCHEDULE_LABELS[params["schedule"]]),
    ]
    if params.get("min_marcap"):
        rows.insert(3, ("시가총액 하한", f"{params['min_marcap'] / 1e8:,.0f}억 원"))
    return rows


def _layout(title: str, height: int, yfmt: str) -> dict:
    return {
        "title": {"text": title, "x": 0, "font": {"size": 15}}, "height": height,
        "margin": {"l": 56, "r": 90, "t": 48, "b": 40}, "hovermode": "x unified",
        "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"family": "Pretendard, 'Noto Sans KR', system-ui, sans-serif", "size": 12, "color": "#52514e"},
        "xaxis": {"showgrid": False, "linecolor": "#d6d5d0"},
        "yaxis": {"gridcolor": "#ebeae6", "zeroline": False, "tickformat": yfmt},
        "legend": {"orientation": "h", "y": 1.02, "yanchor": "bottom", "x": 1, "xanchor": "right"},
    }


def build_report(key: str, params: dict, values: pd.Series, periods: pd.DataFrame, bench: pd.DataFrame,
                 names: dict[str, str], data_date: str, start_year: int, end_year: int,
                 buy_rate: float = 0.0, sell_rate: float = 0.0) -> tuple[str, dict]:
    """(HTML 문자열, 요약 지표 dict). 달력 연도 구간 [start_year, end_year]로 자른다.
    ``buy_rate``·``sell_rate``는 그리드 계산에 쓴 비용 비율(한계 문구용)."""
    v = year_window(values, start_year, end_year)
    b = bench.loc[bench.index >= v.index[0]].reindex(v.index).ffill()
    series = {"전략": v / v.iloc[0] * 100}
    for m in ("KOSPI", "KOSDAQ"):
        if m in b:
            series[m] = b[m] / b[m].iloc[0] * 100
    stats = {name: clean(summarize(s)) for name, s in series.items()}

    figs = []
    fig = go.Figure()
    for i, (name, s) in enumerate(series.items()):
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=name, mode="lines",
                                 line={"width": 2, "color": COLORS["light"][i]},
                                 hovertemplate="%{y:,.1f}<extra>" + name + "</extra>"))
        fig.add_annotation(x=s.index[-1], y=s.iloc[-1], text=f"{name} {s.iloc[-1]:,.0f}", showarrow=False,
                           xanchor="left", xshift=6, font={"size": 11, "color": "#52514e"})
    fig.update_layout(**_layout("누적 수익 (시작 = 100, 시장 지수와 비교)", 380, ",.0f"))
    figs.append(fig)

    dd = drawdown(v)
    fig = go.Figure(go.Scatter(x=dd.index, y=dd.values, mode="lines", fill="tozeroy", name="낙폭",
                               line={"width": 2, "color": COLORS["light"][0]}, fillcolor="rgba(42,120,214,0.15)",
                               hovertemplate="%{y:.1%}<extra>낙폭</extra>"))
    fig.update_layout(**_layout(f"낙폭 (최대 {_pct(stats['전략'].get('mdd'))})", 240, ".0%"), showlegend=False)
    figs.append(fig)

    yr = pd.DataFrame({name: yearly_returns(s) for name, s in series.items()})
    yr = yr[yr.index >= start_year]  # 기준점(직전 연도 마지막 값)의 연도는 표시하지 않는다
    fig = go.Figure()
    for i, name in enumerate(yr.columns):
        fig.add_trace(go.Bar(x=yr.index.astype(str), y=yr[name].values, name=name,
                             marker={"color": COLORS["light"][i], "line": {"width": 0}},
                             hovertemplate="%{y:.1%}<extra>" + name + "</extra>"))
    lay = _layout("연도별 수익률", 300, ".0%")
    lay["hovermode"] = "x"
    fig.update_layout(**lay, barmode="group", bargap=0.25, bargroupgap=0.08)
    figs.append(fig)

    plots = [pio.to_html(f, full_html=False, include_plotlyjs=(i == 0), config={"displaylogo": False,
                         "responsive": True}) for i, f in enumerate(figs)]

    s0 = stats["전략"]
    tiles = [("CAGR", _pct(s0.get("cagr"))), ("누적 수익률", _pct(s0.get("total_return"), 0)),
             ("최대 낙폭(MDD)", _pct(s0.get("mdd"))), ("샤프 지수", _num(s0.get("sharpe")))]
    rows = [("CAGR", "cagr", _pct), ("누적 수익률", "total_return", _pct), ("변동성(연)", "volatility", _pct),
            ("샤프", "sharpe", _num), ("소르티노", "sortino", _num), ("MDD", "mdd", _pct), ("칼마", "calmar", _num),
            ("수익 연도 비율(완전한 연도)", "positive_years", lambda x: _pct(x, 0)),
            ("최악 연도(완전한 연도)", "worst_year", _pct)]
    metric_table = "".join(
        f"<tr><th>{lab}</th>" + "".join(f"<td>{fmt(stats[n].get(k))}</td>" for n in series) + "</tr>"
        for lab, k, fmt in rows)
    full = set(full_years(v))
    year_table = "".join(
        f"<tr><th>{y}{'' if y in full else ' (부분)'}</th>" + "".join(f"<td>{_pct(None if pd.isna(x) else x)}</td>" for x in r) + "</tr>"
        for y, r in yr.iterrows())
    per = periods[(periods["end_date"] > v.index[0]) & (periods["rebalance_date"] <= v.index[-1])]
    per_table = "".join(
        f"<tr><th>{p.rebalance_date:%Y-%m-%d}</th><td>{p.end_date:%Y-%m-%d}{'' if p.closed else ' (진행 중)'}</td>"
        f"<td>{p.n_picks}</td><td>{_pct(p.ret)}</td></tr>" for p in per.itertuples())
    excl_rows, ab_rows, st_rows = [], [], []
    for p in per.itertuples():
        for e in json.loads(p.excluded):
            nm = html.escape(names.get(e["symbol"], ""))
            excl_rows.append(f"<tr><th>{p.rebalance_date:%Y-%m-%d}</th><td>{nm} ({e['symbol']})</td>"
                             f"<td>{REASONS.get(e['reason'], e['reason'])}</td></tr>")
        for e in json.loads(getattr(p, "abnormal_held", "[]") or "[]"):
            nm = html.escape(names.get(e["symbol"], ""))
            ab_rows.append(f"<tr><th>{p.rebalance_date:%Y-%m-%d}</th><td>{nm} ({e['symbol']})</td>"
                           f"<td>{e['date']}</td><td>{_pct(e['ret'])}</td></tr>")
        for e in json.loads(getattr(p, "settled", "[]") or "[]"):
            nm = html.escape(names.get(e["symbol"], ""))
            how = {"resumed": f"재개일 {e['exit_date']} 가격", "pending": "아직 거래정지(잠정: 정지 직전 가격)",
                   "delisted": "상장폐지(마지막 거래 가격)"}[e["status"]]
            st_rows.append(f"<tr><th>{p.rebalance_date:%Y-%m-%d}</th><td>{nm} ({e['symbol']})</td>"
                           f"<td>{e['last_trade'] or '–'}</td><td>{how}</td><td>{_pct(e['ret_vs_last'])}</td></tr>")
    excl = ("".join(excl_rows) if excl_rows else "<tr><td colspan='3'>제외된 종목이 없습니다.</td></tr>")
    sett = ("".join(st_rows) if st_rows else "<tr><td colspan='5'>해당 종목이 없습니다.</td></tr>")
    abn = ("".join(ab_rows) if ab_rows else "<tr><td colspan='4'>해당 종목이 없습니다.</td></tr>")
    settings = "".join(f"<tr><th>{html.escape(a)}</th><td>{html.escape(b)}</td></tr>" for a, b in describe_key(params))
    span = f"{start_year}년" if start_year == end_year else f"{start_year}–{end_year}년"
    if v.index[0].year < start_year:
        period_note = (f"기간은 달력 연도 기준이며, {v.index[0]:%Y-%m-%d} 종가를 기준점으로 {start_year}년 첫 거래일부터의 "
                       "수익률을 계산합니다. 시작 시점에는 그 전에 구성한 포트폴리오를 보유한 상태입니다.")
    else:
        period_note = (f"백테스트는 2017 사업보고서로 처음 구성한 {v.index[0]:%Y-%m-%d} 매수부터이므로 "
                       f"{start_year}년은 그날부터만 포함됩니다.")
    heads = "".join(f"<th>{n}</th>" for n in series)
    if buy_rate or sell_rate:
        cost_note = (f"거래비용은 매수 {buy_rate:.2%}, 매도 {sell_rate:.2%}(증권거래세)만 반영했습니다. 수수료와 체결 가격 차이는 "
                     "빠져 있습니다. 리밸런싱 때마다 모든 종목을 팔고 다시 사는 것으로 계산하므로, 계속 보유하는 종목에도 "
                     "세금이 붙습니다(보수적 가정).")
    else:
        cost_note = "거래비용·세금은 반영하지 않았습니다."

    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>전략 성과 리포트 — {html.escape(key)}</title>
<style>
.viz-root{{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f4f3f0;--text-primary:#0b0b0b;--text-secondary:#52514e;
--border:#e3e2dd;}}
@media (prefers-color-scheme:dark){{.viz-root{{color-scheme:dark;--surface-1:#1a1a19;--surface-2:#242422;
--text-primary:#f0efec;--text-secondary:#c3c2b7;--border:#383835;}}}}
body{{margin:0;background:var(--surface-1);}}
.viz-root{{background:var(--surface-1);color:var(--text-primary);font-family:Pretendard,'Noto Sans KR',system-ui,sans-serif;
max-width:1040px;margin:0 auto;padding:28px 20px 48px;line-height:1.5}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:32px 0 8px}}
.sub{{color:var(--text-secondary);font-size:13px}}
.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0}}
.tile{{background:var(--surface-2);border-radius:8px;padding:14px 16px}}
.tile .v{{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums}} .tile .k{{color:var(--text-secondary);font-size:12px}}
table{{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}}
th,td{{padding:6px 10px;border-bottom:1px solid var(--border);text-align:right}} th{{text-align:left;font-weight:500}}
thead th{{color:var(--text-secondary);font-weight:500;text-align:right}} thead th:first-child{{text-align:left}}
.settings td{{text-align:left}} .note{{color:var(--text-secondary);font-size:12px}} .note li{{margin:2px 0}}
.disclaimer{{margin-top:28px;padding:12px 14px;border:1px solid var(--border);border-radius:8px;font-size:13px}}
@media (max-width:700px){{.tiles{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><div class="viz-root">
<h1>전략 성과 리포트</h1>
<div class="sub">{span} · {v.index[0]:%Y-%m-%d} ~ {v.index[-1]:%Y-%m-%d} · 데이터 기준일 {data_date} · 그리드 키 <code>{html.escape(key)}</code></div>
<h2>설정</h2><table class="settings">{settings}</table>
<div class="tiles">{"".join(f'<div class="tile"><div class="k">{k}</div><div class="v">{val}</div></div>' for k, val in tiles)}</div>
{plots[0]}{plots[1]}{plots[2]}
<h2>연도별 수익률 표</h2><table><thead><tr><th>연도</th>{heads}</tr></thead>{year_table}</table>
<h2>성과 지표</h2><table><thead><tr><th>지표</th>{heads}</tr></thead>{metric_table}</table>
<h2>리밸런싱 기간</h2><table><thead><tr><th>매수일</th><th>평가 종료일</th><th>종목 수</th><th>기간 수익률</th></tr></thead>{per_table}</table>
<h2>리밸런싱일 거래정지로 제외한 종목</h2>
<p class="note">순위에는 들었지만 리밸런싱일에 거래가 없어(거래정지) 살 수 없었던 종목입니다. 다음 순위로 채웠습니다.</p>
<table><thead><tr><th>매수일</th><th>종목</th><th>사유</th></tr></thead>{excl}</table>
<h2>기간 말 거래정지 종목의 정산</h2>
<p class="note">기간 마지막 날 거래정지였던 보유 종목은 정지가 풀린 첫 거래일 가격으로 판 것으로 계산했습니다(돈은 기간 말에 들어온 것으로 봅니다).</p>
<table><thead><tr><th>매수일</th><th>종목</th><th>마지막 거래일</th><th>정산 방식</th><th>정지 전 대비</th></tr></thead>{sett}</table>
<h2>보유 중 이상 수익률이 있었던 종목 (참고)</h2>
<p class="note">보유 기간에 가격제한폭을 넘는 하루 수익률(정리매매, 거래 재개 등)이 있었던 종목입니다. 성과에는 실제 가격 그대로 반영했습니다. 개별 확인용으로 보여 드립니다.</p>
<table><thead><tr><th>매수일</th><th>종목</th><th>발생일</th><th>하루 수익률</th></tr></thead>{abn}</table>
<h2>계산 방식과 한계</h2><ul class="note">
<li>판단 기준일은 리밸런싱일 직전 거래일이며, 재무값은 그날까지 법정 제출 기한(분기 +45일, 연간 +90일)이 지난 보고서만 씁니다.</li>
<li>리밸런싱일 종가로 동일 금액씩 정수 주식을 사고 남는 돈은 현금으로 둡니다. 리밸런싱일에 거래정지인 종목은 사지 않습니다.</li>
<li>{period_note}</li>
<li>보유 중 거래정지된 날은 직전 가격으로 평가하고, 상장폐지 종목은 마지막 거래 가격(정리매매 포함)으로 고정됩니다. 기간 말에 거래정지인 종목은 정지가 풀린 첫 거래일 가격으로 정산하되, 그 돈이 기간 말에 들어온 것으로 계산합니다(장기 정지에서는 실제보다 자금이 일찍 쓰이는 것으로 계산됨).</li>
<li>{cost_note} 배당은 반영하지 않았습니다(가격 수익률). 상장폐지 종목도 표본에 포함됩니다.</li>
<li>수정주가는 거래소 기준가로 계산합니다. 회생절차 중 출자전환을 동반한 감자(주식 병합)처럼 거래소 기준가가 병합 비율과 다르게 정해진 경우에는 기존 주주의 실제 손실이 수익률에 반영되지 않을 수 있습니다.</li>
<li>시장 지수는 같은 가격 데이터로 계산한 시가총액 가중 지수(가격 수익률)입니다.</li>
<li>과거 성과는 미래 수익을 보장하지 않습니다.</li></ul>
<div class="disclaimer">{html.escape(DISCLAIMER)}</div>
</div>
<script>
(function(){{if(!window.matchMedia||!matchMedia('(prefers-color-scheme: dark)').matches)return;
var dark={json.dumps(COLORS["dark"])};
document.querySelectorAll('.plotly-graph-div').forEach(function(g){{
 var upd={{'font.color':'#c3c2b7','yaxis.gridcolor':'#2e2e2c','xaxis.linecolor':'#383835'}};
 (g.layout.annotations||[]).forEach(function(a,i){{upd['annotations['+i+'].font.color']='#c3c2b7';}});
 Plotly.relayout(g,upd);
 g.data.forEach(function(t,i){{var c=dark[i%3]; if(t.type==='bar')Plotly.restyle(g,{{'marker.color':c}},[i]);
  else Plotly.restyle(g,{{'line.color':c}},[i]);}});}});}})();
</script></body></html>"""
    summary = {"window": span, "strategy": s0, "benchmarks": {n: stats[n] for n in series if n != "전략"}}
    return doc, summary
