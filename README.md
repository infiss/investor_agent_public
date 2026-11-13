# kquant — 국내 주식 분석 에이전트 도구

KOSPI·KOSDAQ 종목의 재무제표와 가격 데이터로 **조건 검색과 전략 백테스트 리포트**를 제공하는 Python 패키지와,
이를 [Hermes Agent](https://github.com/NousResearch/hermes-agent)에서 대화형으로 쓰기 위한 MCP 서버·배포 설정입니다.

> **투자 권유가 아닙니다.** 이 프로젝트의 모든 결과는 과거 데이터에 기반한 분석 자료입니다. 종목 추천·매수 수량·매매 신호·목표가는 제공하지 않으며,
> 매매 판단과 그 결과의 책임은 이용자에게 있습니다. 모든 도구 응답에 같은 고지가 붙습니다. 자세한 내용은 [면책 고지](#면책-고지).

## 기능
| 기능 | 설명 |
|---|---|
| 조건 검색 | 항목 카탈로그(매출·이익·PER·PBR·시가총액 등 36개)를 먼저 보여 주고, 카탈로그 항목과 기간(분기/TTM/연간/최신)으로만 검색한다. 모호한 요청은 되묻는다. SQL·수식 실행 경로 없음 |
| 전략 리포트 | 사전 계산한 백테스트 그리드(전략 3종 × 종목 수 5/10/20 × 규모 전체/소형/대형 × 연·분기 리밸런싱 = 54개)에서 고른 설정의 성과를 HTML 리포트(누적 수익, 낙폭, 연도별 수익, CAGR·MDD·샤프 등, 시장 지수 비교)로 만든다. 리포트에 종목 목록은 없다 |

전략: `value`(1/PER·1/PSR·1/PBR·1/PCR 순위 평균), `salesmt`(전년 동기 대비 영업이익 성장률), `momentum`(최근 6개월 수익률).
판단 시점은 법정 공시 기한 이후로 맞춰 미래 정보를 쓰지 않으며(look-ahead 방지), 상장폐지 종목도 표본에 남긴다(생존편향 방지).

## 이 에이전트로 할 수 있는 것
Hermes Agent에 연결하면 웹 채팅(Open WebUI)이나 메신저(Telegram 등)에서 자연어로 묻고 답을 받습니다. 에이전트는 도구 네 개(항목 카탈로그, 조건 검색, 전략 선택지, 전략 리포트)만 쓰며,
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

**하지 않는 것**
- 종목 추천("뭘 사면 좋아?", "유망한 종목 추천해 줘"에는 추천하지 않는다고 답하고, 직접 정한 조건의 검색이나 전략 리포트를 안내합니다).
  조건 검색 결과도 사용자가 정한 조건에 맞는 종목일 뿐 추천이 아닙니다
- 매수 수량, 비중, 투자금 배분, 매수·매도 시점, 목표가 제시("1000만 원으로 몇 주 사?"에는 답하지 않습니다)
- 그리드 밖 임의 설정의 백테스트, 실시간 시세, 공시·뉴스 조회
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

## 로컬에서 실행하기
GitHub에서 받은 그대로 내 PC에서 실행하는 순서입니다. Windows 11, Python 3.13(uv 설치), Hermes Agent v0.22.2에서 처음부터 끝까지 확인했습니다.
명령은 **저장소 폴더에서** 실행합니다. 셸마다 문법이 다른 곳은 bash(macOS·Linux·Git Bash)와 PowerShell(Windows)을 따로 적었습니다.

### 0. 준비물
| 항목 | 필요한 곳 | 비고 |
|---|---|---|
| [git](https://git-scm.com/), [uv](https://docs.astral.sh/uv/getting-started/installation/) | 전부 | Python 3.13은 uv가 알아서 설치합니다 |
| 디스크 약 1GB, 메모리 4GB 이상 | 전부 | 시세 원본 약 220MB. 그리드 계산 중 메모리 최대 약 3GB, 대화용 MCP 서버 약 1–1.3GB |
| 인터넷 연결 | 시세 받기(GitHub) | 회사·기관 보안 프록시가 TLS를 가로채면 `uv sync`·시세 받기가 인증서 오류로 실패할 수 있습니다(uv는 `--native-tls`) |
| LLM 제공자 계정 (선택) | 대화형 에이전트(4–6단계) | Hermes Agent가 지원하는 제공자. 사용량만큼 비용이 들 수 있습니다 |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent), Docker Desktop (선택) | 대화형 에이전트·웹 채팅(5–6단계) | 설치는 각 공식 문서를 따릅니다 |

### 1. 받기와 설치
```bash
git clone https://github.com/nsson/investor_agent_public.git
cd investor_agent_public
uv sync
uv run pytest -q          # 시세를 받기 전이라 실데이터 테스트는 skip, 나머지는 통과해야 합니다
```
Windows에서 한글이 깨지면 PowerShell은 `$env:PYTHONUTF8 = "1"`, bash는 `export PYTHONUTF8=1`을 먼저 실행합니다.

### 2. 데이터 받기와 백테스트 계산 (처음 한 번, 약 7분)
과거 연도 시세는 저장소의 수정계수 이벤트를 그대로 쓰고(`--no-events`), **올해 시세는 `--no-events` 없이** 받아 저장소 이후에 생긴 분할·병합까지 계산합니다.
순서를 지켜야 합니다(올해 계산에 작년 파일이 필요).

bash:
```bash
for y in $(seq 2016 $(( $(date +%Y) - 1 ))); do uv run kquant update-prices --year $y --no-events; done
uv run kquant update-prices        # 올해 시세 + 올해 수정계수 이벤트
uv run kquant run-grid --full      # 백테스트 그리드(54개 설정, 약 3–6분) → dataset/grid/
```
PowerShell:
```powershell
foreach ($y in 2016..((Get-Date).Year - 1)) { uv run kquant update-prices --year $y --no-events }
uv run kquant update-prices
uv run kquant run-grid --full
```

### 3. 명령줄로 써 보기 (LLM 없이)
bash:
```bash
uv run kquant screen '{"where": [{"field": "revenue", "period": "annual", "op": ">=", "value": "1조"}], "sort": {"field": "market_cap"}, "limit": 5}'
uv run kquant report '{"strategy": "value", "n": 10, "start_year": 2018, "end_year": 2024}'
```
PowerShell(Windows PowerShell 5.1은 JSON 안의 큰따옴표를 `\"`로 써야 합니다):
```powershell
uv run kquant screen '{\"where\": [{\"field\": \"revenue\", \"period\": \"annual\", \"op\": \">=\", \"value\": \"1조\"}], \"sort\": {\"field\": \"market_cap\"}, \"limit\": 5}'
uv run kquant report '{\"strategy\": \"value\", \"n\": 10, \"start_year\": 2018, \"end_year\": 2024}'
```
리포트는 `dataset/reports/report-….html`로 저장되며 브라우저로 엽니다. 결과의 `display`가 사람이 읽는 값(예: `55.95조`)입니다.

### 4. 대화형으로 쓰기 ① — MCP를 지원하는 AI 클라이언트에 연결 (가장 간단)
이 저장소는 표준 MCP 서버(`uv run kquant-mcp`, stdio, 읽기 전용 도구 5개)를 제공합니다. 저장소 루트의 `.mcp.json`에 등록되어 있어
Claude Code처럼 프로젝트 `.mcp.json`을 읽는 클라이언트는 저장소 폴더에서 열기만 하면 연결됩니다.
다른 MCP 클라이언트는 명령 `uv`, 인자 `["--directory", "<저장소 절대경로>", "run", "kquant-mcp"]`로 등록합니다.
이 방식에는 대화 규칙(`hermes/SOUL.md`)이 자동으로 적용되지 않으니, 필요하면 그 내용을 클라이언트의 지시문에 넣습니다.

### 5. 대화형으로 쓰기 ② — Hermes Agent
1. Hermes Agent를 설치하고 LLM 제공자에 로그인합니다(Hermes 공식 문서).
2. 이 에이전트 전용 프로필을 만듭니다. 내장 도구(터미널·파일·웹)와 스킬이 없는 프로필입니다.
   ```bash
   hermes profile create kquantpub --no-skills --no-alias
   ```
3. 프로필 폴더(Windows `%LOCALAPPDATA%\hermes\profiles\kquantpub`, macOS·Linux `~/.hermes/profiles/kquantpub`)에서
   - `config.yaml`을 [`hermes/config.local.example.yaml`](hermes/config.local.example.yaml) 내용으로 바꾸고 `<…>`(모델, 제공자, 저장소 절대경로)를 채웁니다.
   - 저장소의 `hermes/SOUL.md`를 프로필 폴더의 `SOUL.md`로 복사합니다(대화 규칙: 카탈로그 먼저, 모호하면 되묻기, 수량·신호 금지, 고지).
   - 프로필 폴더의 `.env`에 아래 4줄을 넣습니다. `API_SERVER_KEY`는 직접 만든 긴 임의 문자열입니다.
     ```
     API_SERVER_ENABLED=true
     API_SERVER_HOST=127.0.0.1
     API_SERVER_PORT=8643
     API_SERVER_KEY=<임의의 긴 문자열>
     ```
4. 터미널에서 바로 대화: `hermes -p kquantpub chat`
5. 웹 채팅을 쓰려면 게이트웨이를 켜 둡니다(별도 창): `hermes -p kquantpub gateway run`.
   로그에 `MCP: 5 tool(s)`와 `API server listening on http://127.0.0.1:8643`이 보이면 준비된 것입니다.
6. 점검(선택): 다른 창에서 `HERMES_API_KEY`에 위 키를 넣고 `uv run kquant check-hermes` → `"ok": true`, `"names": []`이면 내장 도구가 하나도 켜져 있지 않습니다.

### 6. 웹 채팅 화면 (Open WebUI, Docker 필요)
Docker Desktop을 켜고 5단계 게이트웨이가 떠 있는 상태에서 실행합니다. 처음에는 이미지(수 GB)를 받느라 몇 분 걸립니다.

bash:
```bash
export HERMES_API_KEY=<5단계의 API_SERVER_KEY>
docker compose -f hermes/compose.webui.local.yaml up -d
```
PowerShell:
```powershell
$env:HERMES_API_KEY = "<5단계의 API_SERVER_KEY>"
docker compose -f hermes/compose.webui.local.yaml up -d
```
브라우저에서 `http://127.0.0.1:3000` → 모델 `kquantpub`을 고르고 대화합니다. 리포트 링크는 `http://127.0.0.1:8650/…`로 열립니다.
화면 상단에는 "투자 권유가 아닙니다" 고지 배너가 항상 표시됩니다(×로 닫아도 새로고침하면 다시 나타납니다).
끄기: `docker compose -f hermes/compose.webui.local.yaml down`, 게이트웨이 창에서 Ctrl+C.
이 구성은 **로그인 없이 내 PC(127.0.0.1)에서만** 열립니다. 외부에 공개하려면 인증·요청 제한·TLS를 따로 갖춰야 합니다([`hermes/README.md`](hermes/README.md)).

### 7. 데이터 갱신 (선택)
`uv run kquant update-prices`(올해 시세 + 수정계수 이벤트) → `uv run kquant run-grid`(진행 중인 기간만 다시 계산).
Docker를 쓰려면 `docker compose build` 후 `docker compose run --rm kquant update-prices`, `docker compose run --rm kquant run-grid --full`
(데이터는 `./dataset`을 컨테이너에 붙입니다. 시세 받기는 2단계와 같은 순서).
재무 데이터(`dataset/fin/`)는 이 저장소가 갱신할 때 `git pull`로 받습니다(DART 원본 약 5GB가 있어야 다시 만들 수 있어 `kquant build-fin`은 일반 사용자 환경에서 쓰지 않습니다).

### 자주 막히는 곳
| 증상 | 원인·해결 |
|---|---|
| `JSONDecodeError: Expecting property name` (PowerShell) | JSON 안의 큰따옴표를 `\"`로 씁니다(3단계 예시) |
| 한글이 `???`로 나옴 | `PYTHONUTF8=1` 설정(1단계) |
| 전략 리포트가 "백테스트 결과가 아직 준비되지 않았습니다" | 2단계 `run-grid --full`을 실행하지 않았습니다 |
| Hermes가 도구 없이 답하거나 `MCP: 0 tool(s)` | `config.yaml`의 저장소 경로 확인. `uv`를 못 찾으면 `command`에 uv 전체 경로(`where uv` / `which uv`) |
| 웹 채팅에 모델 목록이 비어 있음 | 게이트웨이(5단계)를 먼저 켰는지, `HERMES_API_KEY`가 프로필 `.env`의 `API_SERVER_KEY`와 같은지 확인 |
| 표의 금액이 이상함 | 모델이 원 단위 값을 직접 환산한 경우입니다. 도구 응답의 `display` 값이 기준이며, 성능이 낮은 모델일수록 자주 틀립니다 |

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

## 면책 고지
- 이 프로젝트와 그 결과(조건 검색, 백테스트 리포트, 에이전트의 답변)는 **정보 제공용 분석 자료이며 투자 권유나 투자 자문이 아닙니다.**
  종목을 추천하거나 특정 종목의 매수·매도를 권하지 않고, 매수 수량·비중·매매 시점·목표가를 제공하지 않습니다.
- 과거 데이터에 기반한 백테스트 결과는 미래 수익을 보장하지 않습니다.
- 데이터(시세·재무제표)와 그 가공 결과는 정확성·완전성을 보장하지 않습니다. 출처 데이터의 오류, 가공 과정의 한계(아래 "알려진 한계"),
  대화형 에이전트(LLM)가 값을 잘못 옮기는 오류가 있을 수 있으니 중요한 판단 전에는 공시 원문 등으로 직접 확인하세요.
- **투자 판단과 그 결과에 대한 책임은 전적으로 이용자 본인에게 있으며**, 작성자는 이 프로젝트의 이용으로 생긴 손실에 책임지지 않습니다.
- 이 코드로 다른 사람에게 서비스를 제공하려는 경우, 관련 법령(투자자문·유사투자자문 관련 규정 등)과 데이터 출처의 이용 조건을 확인하는 것은 운영자의 책임입니다.
- 모든 도구 응답과 HTML 리포트에는 같은 취지의 고지("투자 권유가 아닙니다 … 책임은 이용자에게 있습니다")가 자동으로 붙습니다.

## 라이선스
함께 배포하는 데이터(`dataset/`)는 위 "데이터"의 각 출처 이용 조건을 따릅니다.
