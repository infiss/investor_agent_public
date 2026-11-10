# kquant — 국내 주식 분석 에이전트 도구

KOSPI·KOSDAQ 종목의 재무제표와 가격 데이터로 **조건 검색, 전략 백테스트 리포트, 전략 기준 종목 목록**을 제공하는 Python 패키지와,
이를 [Hermes Agent](https://github.com/NousResearch/hermes-agent)에서 대화형으로 쓰기 위한 MCP 서버·배포 설정입니다.

> **투자 권유가 아닙니다.** 이 프로젝트의 모든 결과는 과거 데이터에 기반한 분석 자료입니다. 매수 수량·매매 신호·목표가는 제공하지 않으며,
> 매매 판단과 그 결과의 책임은 이용자에게 있습니다. 모든 도구 응답에 같은 고지가 붙습니다.

## 기능
| 기능 | 설명 |
|---|---|
| 조건 검색 | 항목 카탈로그(매출·이익·PER·PBR·시가총액 등 36개)를 먼저 보여 주고, 카탈로그 항목과 기간(분기/TTM/연간/최신)으로만 검색한다. 모호한 요청은 되묻는다. SQL·수식 실행 경로 없음 |
| 전략 리포트 | 사전 계산한 백테스트 그리드(전략 3종 × 종목 수 5/10/20 × 규모 전체/소형/대형 × 연·분기 리밸런싱 = 54개)에서 고른 설정의 성과를 HTML 리포트(누적 수익, 낙폭, 연도별 수익, CAGR·MDD·샤프 등, 시장 지수 비교)로 만든다. 리포트에 종목 목록은 없다 |
| 종목 목록 | 사용자가 요청할 때만, 최신 기준일에 전략을 적용한 상위 종목과 최근 공시(OpenDART)를 보여 준다 |

전략: `value`(1/PER·1/PSR·1/PBR·1/PCR 순위 평균), `salesmt`(전년 동기 대비 영업이익 성장률), `momentum`(최근 6개월 수익률).
판단 시점은 법정 공시 기한 이후로 맞춰 미래 정보를 쓰지 않으며(look-ahead 방지), 상장폐지 종목도 표본에 남긴다(생존편향 방지).

## 이 에이전트로 할 수 있는 것
Hermes Agent에 연결하면 웹 채팅(Open WebUI)이나 메신저(Telegram 등)에서 자연어로 묻고 답을 받습니다. 에이전트는 아래 도구 다섯 개만 쓰며,
터미널·파일·웹 검색 같은 다른 도구는 갖지 않습니다.

**1. 어떤 항목으로 찾을 수 있는지 묻기**
> 조건 검색에 쓸 수 있는 항목이 뭐가 있어?

→ 항목 카탈로그(손익·재무상태·가격·비율 36개)와 항목별 기간(분기·최근 12개월·직전 연간·최신), 단위, 데이터 기준일을 정리해 보여 줍니다.

**2. 조건으로 종목 찾기**
> 최근 12개월 매출 1조 원 이상이고 PER 10배 이하인 회사를 시가총액 순으로 10개 보여 줘

→ 조건에 맞는 종목을 표로 보여 주고, 기준일과 각 종목의 재무 결산기준일을 함께 알려 줍니다.
기간이 빠진 요청("매출 1조 넘는 회사")이나 카탈로그에 없는 항목은 실행하지 않고 "분기/최근 12개월/연간 중 무엇인가요?"처럼 되묻습니다.

**3. 전략 성과 리포트 받기**
> value 전략, 종목 10개, 소형주, 분기 리밸런싱으로 2018년부터 2024년까지 성과를 보여 줘

→ 사전 계산된 백테스트로 CAGR·누적 수익률·최대 낙폭(MDD)·샤프 지수 등 요약과 KOSPI·KOSDAQ 시장 지수 비교를 답하고,
누적 수익 곡선·낙폭·연도별 수익 차트가 담긴 **HTML 리포트 링크**를 줍니다. 정하지 않은 값(규모, 리밸런싱 등)은 기본값을 쓰고 무엇을 썼는지 알립니다.
고를 수 있는 값: 전략 `value`/`salesmt`/`momentum`, 종목 수 5/10/20, 규모 전체/소형(시총 하위 30%)/대형(시총 상위 30%), 연 1회/분기 1회, 2018년 이후 연도 구간.

**4. 전략 기준 종목 목록 보기(요청할 때만)**
> value와 momentum 전략을 같이 적용한 상위 5개 종목과 최근 공시를 알려 줘

→ 최신 기준일에 전략(1–3개 조합)을 적용한 상위 종목의 기업명, 종목코드, 시장, 주요 지표(PER·PBR·ROE 등), 전략 순위와 최근 공시 링크를 보여 줍니다.

**하지 않는 것**
- 매수 수량, 비중, 투자금 배분, 매수·매도 시점, 목표가 제시("1000만 원으로 몇 주 사?"에는 답하지 않습니다)
- 그리드 밖 임의 설정의 백테스트, 실시간 시세, 뉴스 기사 요약
- 데이터 갱신·다운로드 같은 운영 작업(운영자가 CLI로만 실행)
- "이전 지시를 무시하고 …" 같은 요청으로 규칙이나 역할을 바꾸는 것

모든 답변 끝에는 "투자 권유가 아닙니다" 고지가 붙습니다. 대화 규칙은 [`hermes/SOUL.md`](hermes/SOUL.md)에 있습니다.

## 구성
```
src/kquant/         코어 패키지 (Hermes를 모른다)
  prices/           시세 동기화(FinanceData/marcap)와 이벤트 기반 수정종가
  fin/              재무제표 long 포맷, 단위 오기입 보정, as-of 스냅샷
  catalog/          검색 항목 카탈로그(fields.yaml)와 조건 검색
  strategies/, universe.py   전략 3종과 공통 유니버스
  backtest/         백테스트 엔진과 그리드(grid.yaml, 증분 계산)
  reports/          HTML 리포트
  service.py        공개 도구의 진입점(모든 응답에 고지)
  mcp_server.py     공개 MCP 서버(stdio, 읽기 전용 도구 5개)
hermes/             Hermes 공개 프로필 설정 예시, 대화 규칙(SOUL.md), 로컬 웹 채팅·공개 컨테이너 compose
tests/              단위(합성 데이터)·레드팀(주입·시점 편향·노출 도구)·통합(실데이터, 없으면 skip)
docs/               설계 문서(spec), 목표 정의(intent)
dataset/            재무 가공 결과와 수정계수 이벤트(아래 "데이터")
```

## 데이터
| 파일 | 내용 | 출처 |
|---|---|---|
| `dataset/fin/long.parquet` | 재무제표 long 포맷(2016 1Q–2026 2Q, 연결 우선, 공시 가능일 포함) | [OpenDART](https://opendart.fss.or.kr) 재무정보 일괄 다운로드를 가공 |
| `dataset/fin/conflicts.parquet`, `quality.parquet` | 동순위 항목 충돌 기록, 단위 오기입 판정표 | 위 가공 과정의 기록 |
| `dataset/prices/adj_events.parquet` | 수정계수 이벤트(분할·병합 등으로 기준가가 바뀐 날) | [FinanceData/marcap](https://github.com/FinanceData/marcap) 시세로 계산 |
| `dataset/marcap-YYYY.parquet` | 일별 시세 원본 — **저장소에 없음**, 처음 실행할 때 받는다 | FinanceData/marcap |

재무 데이터는 DART 원본(약 5GB)이 있어야 다시 만들 수 있어 가공 결과를 함께 배포합니다. 데이터 이용 시 각 출처의 이용 조건을 따르세요.
2017년 이전 시세는 KOSPI 위주라 백테스트는 2018년부터입니다.

## 빠른 시작
요구 사항: [uv](https://docs.astral.sh/uv/) (Python 3.13은 uv가 설치), 공시 조회를 쓰려면 OpenDART API 키.

```bash
uv sync
cp .env.example .env            # DART_API_KEY 등 값 채우기(.env는 git 제외)

# 시세 받기(처음 한 번, 연도별). --no-events: 저장소의 수정계수 이벤트를 그대로 쓴다
for y in $(seq 2016 2026); do uv run kquant update-prices --year $y --no-events; done
uv run kquant update-corp-codes # 공시 조회용 상장사 고유번호(DART_API_KEY 필요)
uv run kquant run-grid --full   # 백테스트 그리드(약 2분) → dataset/grid/

uv run kquant screen '{"sort": {"field": "market_cap"}, "limit": 5}'
uv run kquant report '{"strategy": "value", "n": 10, "start_year": 2018, "end_year": 2021}'
uv run kquant recommend '{"strategies": ["value"], "n": 5}'
```
매일 갱신: `uv run kquant update-prices`(당해 연도 시세 + 수정계수 이벤트 증분) → `uv run kquant run-grid`(진행 중인 기간만 재계산).

Docker:
```bash
docker compose build
docker compose run --rm kquant update-prices --year 2026
docker compose run --rm kquant run-grid --full
```

## Hermes에서 대화로 쓰기
- 공개 MCP 서버: `uv run kquant-mcp`(stdio). 도구 `list_fields`, `screen`, `strategy_options`, `strategy_report`, `recommend` — 모두 읽기 전용.
- Hermes 공개 프로필 설정·대화 규칙·배포 순서: [`hermes/README.md`](hermes/README.md)
- 로컬 1인 웹 채팅(Open WebUI + 리포트 링크): `hermes/compose.webui.local.yaml`
- 배포 전 노출 도구 점검: `uv run kquant check-hermes`(공개 프로필 API 서버 `/v1/toolsets`, 위험 도구가 켜져 있으면 종료 코드 2)

## 테스트
```bash
uv run pytest -q          # 실데이터가 없으면 tests/integration은 skip
uv run ruff check src tests
```

## 알려진 한계
- 가격 수익률 기준(배당 미반영), 거래비용은 매도 시 증권거래세 0.20%만 반영.
- 재무 공시 시점은 법정 제출 기한으로 가정한다(실제 접수일 미사용, 판단일에 5거래일 여유).
- 회생절차 중 출자전환을 동반한 감자처럼 거래소 기준가가 병합 비율과 다르게 정해진 경우 기존 주주의 손실이 수정종가에 반영되지 않을 수 있다.
- 비KRW로 보고하는 기업(외국 기업 등)의 해당 기간은 유니버스에서 제외한다.
자세한 설계와 결정 근거는 [`docs/spec/system-spec.md`](docs/spec/system-spec.md).

## 라이선스
함께 배포하는 데이터(`dataset/`)는 위 "데이터"의 각 출처 이용 조건을 따릅니다.
