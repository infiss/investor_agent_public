# 시스템 스펙 v0.1 — kquant (국내 주식 분석 코어) + Hermes 연동

근거: `docs/intent/investor-agent.md` (확정 목표). 이 문서는 설계 결정과 그 이유, 그리고 미결 질문을 기록한다.
상태 표기: **[결정]** 확정, **[제안]** 운영자 확인 전 기본안, **[미결]** 운영자 답변 필요.

---

## 1. 전체 구조

```
                ┌──────────── 외부 사용자 ────────────┐
                │  Telegram            Open WebUI     │
                └────┬─────────────────────┬──────────┘
                     │ Messaging Gateway   │ API Server (OpenAI 호환, 공개 :8643)
              ┌──────▼─────────────────────▼──────┐
              │  Hermes "public" 프로필             │  ← terminal/file/browser 도구 비활성
              │  toolsets: kquant-public 만         │
              └──────────────┬─────────────────────┘
                             │ MCP (stdio)
              ┌──────────────▼─────────────────────┐
              │  kquant MCP 서버 (공개 도구만)        │  catalog / screen / strategy_report / recommend
              └──────────────┬─────────────────────┘
                             │ Python import (읽기 전용)
   ┌─────────────────────────▼─────────────────────────────────┐
   │  kquant 코어 패키지                                         │
   │  ingest → store(parquet + DuckDB) → features → strategies  │
   │  → backtest(grid, 증분) → reports(HTML)                     │
   └─────────────────────────▲─────────────────────────────────┘
                             │ CLI (운영자 전용)
              ┌──────────────┴─────────────────────┐
              │  kquant CLI: update-prices, dart-download,     │
              │  dart-ingest, build-panel, run-grid, validate  │
              │  ← Hermes cron(--no-agent --script) 또는 운영자 │
              └────────────────────────────────────┘
```

**[결정] 코어는 Hermes를 모른다.** 모든 기능은 순수 Python API + CLI로 먼저 만들고 테스트한다. Hermes 연동은 얇은 어댑터(MCP 서버)다. 그래야 Windows에서 Hermes 없이 개발·검증이 가능하고, 나중에 FastAPI 등 다른 진입점도 같은 코어를 쓴다.

**[결정] Hermes 연동 = MCP stdio 서버.** 조사 결과 Hermes는 plugin(`~/.hermes/plugins`), MCP, skills를 지원한다. MCP를 택한 이유:
- 별도 프로세스·별도 venv로 실행 → kquant 의존성(pandas 등)이 Hermes 환경과 충돌하지 않는다.
- `mcp_servers.<name>.tools.include`로 노출 도구를 서버 단위로 화이트리스트할 수 있다.
- Claude Code에서도 같은 MCP 서버로 테스트할 수 있다 (Hermes 없이 E2E 검증).
- ~~Hermes skill(`SKILL.md`)에 대화 절차를 담는다~~ → 2026-11-10 결정: 공개 프로필에는 스킬을 두지 않고(스킬 쓰기 도구 `skill_manage`가 묶여 있어 공개 대화가 지시문을 바꿀 수 있음) 대화 절차는 프로필 `SOUL.md`(`hermes/SOUL.md`)에 둔다. Hermes v0.22는 MCP `instructions`를 모델에 전달하지 않는다.

**[결정] 공개/운영 분리.** 데이터 갱신·다운로드 같은 쓰기 작업은 공개 MCP 서버에 절대 넣지 않는다. 운영 작업은 CLI로만 실행한다 (Hermes cron의 `--no-agent --script` 모드는 LLM 없이 스크립트만 돌리므로 적합).

**[결정] 공개 Hermes 프로필 보안 요건** (조사: API Server는 기본적으로 terminal 포함 전체 도구 노출)
- 공개 프로필은 `platform_toolsets.api_server`(·telegram)에 MCP 서버 이름 `kquant-public`만 허용(skills 제외, curator·메모리 끔), terminal/file/browser/code 비활성(v0.22.2 키, 2026-11-10 확인).
- 배포 전 `GET /v1/toolsets`로 실제 노출 도구를 확인하는 것을 레드팀 체크리스트 1번으로 둔다.
- Telegram은 기본이 "미등록 사용자 거부" — 공개 시 `TELEGRAM_ALLOW_ALL_USERS` 설정은 공개 프로필에서만.
- 관리자 Web Dashboard(:9119)는 loopback 바인딩 유지.

---

## 2. 저장소 구조 [제안]

```
Investor_agent/
├─ pyproject.toml            # uv 관리, Python >=3.11 (Hermes와 동일 하한)
├─ src/kquant/
│  ├─ config.py              # 경로/키는 환경변수 + config.toml, pathlib
│  ├─ ingest/                # 외부 수집 (네트워크 경계)
│  │  ├─ marcap.py           # GitHub raw parquet 동기화
│  │  ├─ dart_bulk.py        # OpenDART 일괄 zip 목록/다운로드 + inbox 처리
│  │  ├─ dart_api.py         # 유상증자, 공시목록, 기업개황 (API 키)
│  │  └─ kind.py             # 배당 정보
│  ├─ prices/adjust.py       # 수정종가 (§4)
│  ├─ fin/                   # 재무: 파싱, 항목 매핑, 분기 정리, 지배주주 조정 (§5)
│  ├─ store/                 # parquet 레이아웃 + DuckDB 뷰
│  ├─ catalog/               # 검색 항목 카탈로그 + 필터 DSL (§6)
│  ├─ universe.py            # 스크리닝(제외 규칙)
│  ├─ strategies/            # value, salesmt, momentum + registry (§7)
│  ├─ backtest/              # 엔진 + 그리드 + 증분 저장 (§8)
│  ├─ reports/               # HTML 리포트 (§9)
│  ├─ recommend.py           # 종목 추천 + 공시 첨부 (§10)
│  ├─ cli.py                 # 운영자 CLI
│  └─ mcp_server.py          # 공개 도구 어댑터
├─ hermes/                   # Hermes 배포 자산 (SOUL.md, config 예시) — 실제 키 없음
├─ tests/
│  ├─ unit/  redteam/
│  └─ fixtures/              # 소형 표본 데이터 (git 포함 가능 크기)
├─ docs/
├─ 국내퀀트코드/               # (git 제외) legacy 코드 — 로컬 검토·회귀 기준용, 수정하지 않음
└─ local/                    # (git 제외) legacy 재현 모듈, 회귀 스크립트·테스트·기록, legacy-issues
```

- 데이터 디렉터리는 `KQUANT_DATA_DIR`(기본: `dataset/`)로 지정. git에는 서비스 환경에서 다시 만들 수 없는 새 산출물만 넣는다: `fin/long.parquet`, `fin/conflicts.parquet`, `fin/quality.parquet`, `prices/adj_events.parquet` (2026-11-07 결정). legacy 코드·legacy 비교 작업은 로컬 전용이다.
- 비밀값: `DART_API_KEY`, `KRX_ID`, `KRX_PW` 등은 `.env`(gitignore) 또는 서버 secret.
- 기존 코드의 하드코딩된 DART 키는 **첫 커밋 전에 제거**한다 (git 이력에 남으면 공개 시 노출).

**[제안] 저장 = parquet + DuckDB.** 서버형 DB 없이 파일만으로 운영. 조건 검색은 DuckDB가 parquet을 직접 질의. 컨테이너 볼륨 하나로 이식 가능. 사용자가 많아지면 이후 Postgres로 옮길 수 있게 store 계층에서 추상화.

---

## 3. 데이터 계층

| 데이터 | 소스 | 갱신 | 저장 |
|---|---|---|---|
| 일별 시세·시총 | FinanceData/marcap `data/marcap-YYYY.parquet` (raw.githubusercontent, 키 불필요, 평일 09:30 UTC 자동 갱신) | 매 영업일, 당해 파일만 재다운로드 | `prices/raw/marcap-YYYY.parquet` |
| 보조 시세 | pykrx (KRX_ID/PW 환경변수) | marcap 장애 시만 | 동일 스키마로 정규화 |
| 수정계수 | 계산 | 시세 갱신 후 | `prices/adj_factor.parquet` (Code, Date, factor) |
| 재무제표 | OpenDART 일괄 zip (§5) | 운영자 호출 | `fin/raw/*.zip` → `fin/long.parquet` |
| 유상증자 | DART API | 운영자 호출 | `events/rights_issue.parquet` |
| 배당 | KIND | 연 1회 | `events/dividend.parquet` |
| 산업 분류 | [미결 Q4] | | `ref/company.parquet` |

---

## 4. 수정종가 [결정 + 확인 필요]

**기존 방식의 문제 (데이터로 확인):** `ChangesRatio`(소수 둘째 자리 반올림된 %)를 역누적곱해서, 기업 이벤트가 없어도 오차가 누적된다.
예: 동화약품 2026-01-02 종가 6210 / 기존 adj_price 6208.

**새 방식:** marcap의 `Changes`(전일 대비 변동액)로 당일 기준가 `base_t = Close_t − Changes_t`를 구한다.
- 이벤트가 없으면 `base_t == Close_{t−1}` → 조정계수 1 (오차 0).
- 분할·증자 등으로 기준가가 바뀐 날만 `f_t = base_t / Close_{t−1}` ≠ 1.
- `adj_close_t = Close_t × ∏_{s>t} f_s` (최신가 = 원가격 유지, 기존 방식과 같은 기준).
- 거래정지(Volume=0)·상장 첫날·데이터 누락일 처리 규칙을 테스트로 고정한다.
- **알려진 한계(2026-11-10)**: 회생계획에 따른 감자처럼 거래소 기준가가 병합 비율과 다르게 정해지면(STX 2026-11-06: 103주 → 1주, 기준가 18,360 ≠ 3,530×103) 이 방식은 기존 주주의 실제 손실을 수익률 0%로 만든다. 테스트 버전은 리포트 한계 문구로만 고지하고, DART 감자 결정(병합 비율)으로 계수를 대체하는 개선은 다음 업데이트 과제다.

**회귀 기준:** 기존 `adj_marcap`과 비교해 (1) 일별 수익률 차이가 반올림 오차 범위 내인지, (2) 큰 차이가 나는 종목·날짜를 목록화해 원인을 분류한다 (새 방식이 맞는 경우 / 이상 데이터 / 버그). 분류 결과는 운영자에게 확인받는다.

---

## 5. 재무제표 [결정 + 미결]

### 5.1 다운로드
- 목록은 `POST /disclosureinfo/fnltt/dwld/list.do`로 확인 가능 (파일명: `{YYYY}_{1Q|2Q|3Q|4Q}_{BS|PL|CF|CE}_{timestamp}.zip`, timestamp는 재생성 시 바뀜 → 목록에서 이름을 읽는다).
- 실제 zip 다운로드는 단순 HTTP 요청이 HTML 오류를 반환했다 (조사). 따라서:
  1. **기본 경로: inbox** — 운영자가 받은 zip을 `fin/inbox/`에 넣으면 `kquant dart-ingest`가 처리.
  2. **자동 경로:** 운영자가 `kquant dart-download --year 2026 --period 3Q`를 호출하면 실제 브라우저(Playwright)로 페이지에서 다운로드 버튼을 누르는 방식. 사이트의 봇 차단을 우회하는 헤더 조작은 하지 않는다.
- 이미 처리한 파일은 해시로 기록해 중복 처리를 막는다.

### 5.2 정리 (기존 로직 유지 + 효율화)
- 연결 우선, 연결 없는 기업만 별도. 공백·코드 정규화.
- 항목 매핑: `dart_항목명.xlsx` → `src/kquant/fin/dart_item_map.csv`로 옮겼다(구현됨) (연도별 IFRS 코드 + `-full` 변형 + 추가 항목명). **매핑되지 않은 상위 빈도 항목명을 리포트로 출력**해 매핑 누락을 사람이 확인한다.
- 지배주주 귀속 당기순이익, 비지배지분 차감 자본 (`set_owner_prop` 규칙 유지).
- **long 포맷**으로 저장: `corp_code, symbol, fiscal_year, fiscal_q, item, value, period_basis(3M|YTD|FY|instant), statement_type(CFS|OFS), source_file`.
- 파생:
  - 손익·현금흐름 항목: 분기 3개월 값, Q4 단독 = 연간 − 3Q 누적.
  - TTM(2026-11-07 결정) = 올해 누적 + 직전 연간 − 전년 동기 누적, 4Q이면 연간 값. 3개월 컬럼은 쓰지 않는다. 원본의 3개월 컬럼이 누적과 맞지 않는 경우(재작성된 누적, 3개월 컬럼 오기입)가 약 4% 종목에 있어서, 최신 누적값을 쓰는 이 공식이 더 안전하다.
  - 재무상태표 항목: 시점값(instant).
- 기존 wide 패널(`{항목}_{연도}`)은 long에서 생성한다 → 기존 전략 회귀 테스트용.

### 5.3 시점 정합성 (look-ahead 방지)
- 각 행에 `available_date`(그 재무 정보를 시장이 알 수 있게 된 날) 부여.
- [제안] 기본: 법정 제출 기한 = 분기·반기 말 +45일, 사업보고서 = 연말 +90일. DART 접수일을 구할 수 있으면 실제 접수일 사용.
- 백테스트·조건 검색 모두 "기준일에 available인 가장 최근 값"만 사용.
- 기존 11월 리밸런싱 + 2Q 데이터 조합은 이 규칙에서도 유효(8/14 마감). 이 규칙이 기존 결과와 달라지는 지점은 회귀 리포트에 표시한다.

### 5.3.1 as-of 스냅샷 (구현됨, `kquant.fin.snapshot`)
- `snapshot(long, as_of)` → 종목별 wide 표. 기준 보고서 = `available_date ≤ as_of`인 가장 최근 결산기준일이다. 그 보고서에 없는 항목은 이전 보고서로 채우지 않는다(NaN).
- 손익·현금흐름: `{항목}_q`(3개월, 3M이 없으면 누적 차분), `{항목}_ttm`(위 TTM 공식), `{항목}_fy`(직전 완결 연간). 재무상태표: `{항목}`(시점값). 모두 전년 동기 `…_1y`를 함께 준다(결산기준일 12개월 전 보고서 기준).
- 보고서는 결산기준일(월말)로만 맞춘다. 비12월 결산도 같은 규칙이고, 결산월 변경 등으로 날짜가 맞지 않으면 NaN이다.
- 상장폐지 종목도 마지막 보고서가 남으므로, 유니버스는 기준일 가격이 있는 종목과 교집합으로 만든다.

### 5.4 데이터 품질 검사
- 단위 이상(구현됨, `kquant.fin.quality`): 보고서 하나 전체가 10³·10⁶배(드물게 1/10⁶)로 기입된 경우. 재무상태표 항목의 직전·직후 보고서 대비 배율로 판정하고(끝 보고서는 시총 대비 배율로 확인), 파생 항목을 만들기 전에 10^k로 나눠 보정한다. 판정표는 `fin/quality.parquet`. 기존의 `3S` 하드코딩 제외를 일반화한 것이다.
- 통화: DART `통화` 컬럼이 KRW가 아닌 기업(외국 기업, 아남전자·두산밥캣 등, 33개 종목·875개 보고서)이 있다. long 포맷에 `currency` 컬럼으로 보존하고 환산하지 않는다. 비KRW 기간은 유니버스에서 제외한다(§7). 단위 오기입 판정은 같은 통화 구간 안에서만 한다.
- 기간 공백: 해소됨(2026-11-06, 원본 txt·zip으로 2016 1Q–2026 2Q 전 분기 확보).

---

## 6. 조건 검색 (카탈로그 + 필터 DSL) [결정]

### 6.1 카탈로그
`catalog/fields.yaml`이 **유일한 진실 원천**이다. 사용자에게 보이는 표, 필터 검증, SQL 생성이 모두 이 파일에서 나온다.

```yaml
- id: revenue
  label: 매출액
  aliases: [매출, 수익, sales]
  unit: KRW
  kind: flow                 # flow | stock | price | ratio
  periods: [quarter, ttm, annual]
  default_period: null       # null이면 기간 미지정 시 되묻기
  source: fin
  description: 연결 우선, 연결 없으면 별도
- id: market_cap
  label: 시가총액
  unit: KRW
  kind: price
  periods: [latest]          # 최근 거래일
  default_period: latest
  as_of: "{prices.latest_date}"
```

- 파생 지표(PER, PBR, ROE, 부채비율, 매출 성장률 등)도 계산식과 함께 카탈로그에 등록한다.
- `list_fields` 도구는 카탈로그를 표로 반환한다. 반환 정보: 항목, 가능 기간, 단위, 데이터 기준일.

### 6.2 필터 DSL (LLM이 생성 → kquant가 검증/실행)
```json
{"as_of": "latest",
 "where": [
   {"field": "market_cap", "period": "latest", "op": ">=", "value": 200000000000},
   {"field": "revenue",    "period": "ttm",    "op": ">=", "value": 30000000000}
 ],
 "market": ["KOSPI", "KOSDAQ"],
 "sort": {"field": "market_cap", "order": "desc"},
 "limit": 50}
```
- 필드·기간·연산자·단위는 카탈로그 화이트리스트로 검증한다. SQL은 kquant가 파라미터 바인딩으로 생성한다. 사용자 문자열이 SQL에 직접 들어가지 않는다.
- 다음 경우는 실행하지 않고 구조화된 오류를 반환한다. 에이전트는 이 오류로 되묻는다.
  - 검증 실패: 없는 필드, 지원하지 않는 기간
  - 모호성: 기간 미지정
- 금액 단위 ("2000억") 변환은 LLM이 아니라 kquant의 `parse_amount`가 담당한다. 필터 DSL은 문자열 금액 표현도 허용한다. 예: `"value": "2000억"`.

### 6.3 구현 (P3, 2026-11-07)
- `kquant.catalog`: `fields.yaml`(항목 36개: flow 8, stock 13, price 3, ratio 12), `catalog.py`(조회·별칭·제안), `amount.py`(`parse_number`), `features.py`(기준일 단면 지표, 비율 계산 `RATIOS`), `screen.py`(검증·실행).
- **SQL/DuckDB를 쓰지 않는다**: 단면 표(종목 약 2,800개)를 pandas로 만들고 화이트리스트된 비교 연산만 적용한다. 문자열이 쿼리나 수식으로 해석되는 경로 자체가 없어 주입 위험이 없다. (§2의 DuckDB 제안은 필요해질 때 다시 검토)
- 기본값: 시장 미지정 시 KOSPI+KOSDAQ(KONEX는 명시해야 포함), limit 20(최대 100), 조건 최대 20개, 정렬 미지정 시 시가총액 내림차순.
- 대상은 기준일에 거래 행이 있는 종목이다(거래정지일에는 marcap 행이 없어 빠진다). 재무 보고 통화가 KRW가 아니면 재무 기반 값은 비운다.
- 비율의 분모가 0 이하이면 값 없음(NaN) — 적자 기업의 PER, 자본잠식 기업의 PBR/ROE/부채비율, 전년 동기 값이 0 이하인 성장률.
- 모든 응답(오류 포함)에 `DISCLAIMER`를 붙인다.

---

## 7. 전략 [결정 + 미결]

- 각 전략은 순수 함수다: `rank(snapshot: DataFrame, params) -> DataFrame[symbol, score, rank, 진단 컬럼...]`.
- 레지스트리에 이름, 필요 필드, 조정 가능 파라미터(범위 포함), 설명을 등록한다. 이 레지스트리가 그리드와 카탈로그 설명의 근거다.
- 조합: 기존과 동일하게 교집합에서 순위 평균을 낸다. 기존 규칙인 "`최종` 문자열이 들어간 컬럼"은 명시적 `rank` 컬럼으로 대체한다.
- **서비스 전략 (2026-11-07 결정, 테스트 버전)**: legacy 전략 코드를 그대로 옮기지 않고 아래 3개만 새로 구현한다. quality·lynch·ncav는 두지 않는다.
  - `value`: 1/PER, 1/PSR, 1/PBR, 1/PCR(시총 대비 영업활동현금흐름) 각각의 순위 평균. 역수라 적자 기업이 상위에 오지 않는다.
  - `salesmt`: 전년 동기 대비 영업이익 성장률 `(올해 − 전년) / 전년` 순위 하나. 대상은 올해·전년 동기 영업이익이 모두 양수인 기업뿐이다(순이익 조건·F-스코어 없음).
  - `momentum`: 수익률 = 리밸런싱일 직전 거래일 수정종가 / 그날로부터 달력 6개월 전(휴장이면 그 이전 마지막 거래일) 수정종가 − 1. 높을수록 상위. 시작 시점 가격이 없는 종목은 제외. 리밸런싱 당일 종가는 쓰지 않는다.
- **공통 유니버스 (`universe.py`, 2026-11-07 결정)**: 세 전략 모두에 적용한다.
  - 회사명에 금융·지주·스팩·중국·홀딩스·리츠가 들어가면 제외(업종 코드 기반 제외는 Q4 결정 후 검토).
  - 자본잠식 없음(자본총계 > 자본금), 당기순이익 > 0.
  - 사용하는 재무 보고 기간의 `currency`가 KRW가 아니면 제외한다(2026-11-07 결정, 테스트 버전). 통화를 바꾼 기업(아남전자, 두산밥캣)은 바꾼 뒤 기간만 빠진다. 환율 환산은 나중에 붙이면 다시 포함할 수 있다.
  - legacy의 유보율 조건(부채비율 정의 오류로 무력)과 **거래대금 하위 10% 제외는 쓰지 않는다.**
  - ~~이상 수익률 종목은 해당 리밸런싱 기간에서 제외한다(2026-11-06 결정)~~ → 2026-11-07 변경: 기준일 거래정지(거래량 0) 종목 제외로 대체, 보유 중 이상 수익률은 리포트에 참고 표시(§8). 시총 컷(규모)은 그리드 파라미터다.
- legacy 동치성 비교는 로컬(`local/`)에서만 하며, 전략 정의가 달라졌으므로 서비스 전략의 성공 기준이 아니다.
- **구현 (P4, 2026-11-07)**: `kquant.universe.build_universe`, `kquant.strategies`(`value`·`salesmt`·`momentum`, 레지스트리·조합 `base`), `kquant.strategies.select.select`(백테스트·추천 공통 선정 함수).
  - 판단 기준일 `d` = 리밸런싱일 직전 거래일. 가격 단면·시가총액·재무 스냅샷·모멘텀 모두 `d` 기준이다.
  - 유니버스 세부(기본안): KOSPI·KOSDAQ만(KONEX 제외), 자본잠식 = 자본총계(비지배 포함) ≤ 자본금, 순이익 조건 = TTM 지배당기순이익 > 0, 최신 보고서가 `d`보다 15개월 넘게 오래되면 제외(보고 중단). 제외 사유를 모두 기록한다.
  - value: 지표 값이 없으면 그 지표에서 최하위 순위. 조합·전략의 동점은 종목코드 순.
  - 기준일 거래량 0(거래정지) 종목은 유니버스에서 뺀다(`halted`). 기간 중 이상 수익률 종목을 미리 빼는 규칙은 2026-11-07 삭제했다(§8).
- (참고, legacy 관련 이전 결정 2026-11-06: 99999 결측 유지, salesmt 이중 `-1` 수정, quality 차입금 0→1 유지 — 서비스 전략에는 해당 없음.)

---

## 8. 백테스트 + 그리드 사전 계산 [결정 + 미결]

- 엔진: 가격을 메모리에 한 번만 로드한다(기간별 parquet 반복 로드 제거). 리밸런싱 기간마다 동일 비중, 정수 주식 수, 잔액은 현금. `adj_mode`(이탈 종목 처리) 의미는 유지한다.
- 산출물: 그리드 키별 **일별 수익률 시계열** + 기간별 요약을 저장한다. 리포트의 연도 구간(`start_year`–`end_year`) 지표는 시계열에서 그때그때 계산한다.
- **증분:** 종료된 리밸런싱 기간의 결과는 고정한다. 매일 재계산하는 것은 진행 중인 기간뿐이다. 재무 데이터나 전략 코드가 바뀌면 해당 그리드 키를 전체 재계산하며, 이는 코드·데이터 해시로 감지한다.
- 그리드는 `src/kquant/backtest/grid.yaml`로 정의한다. **최종 그리드(2026-11-10 결정, 54키)**:
  - 전략: value, salesmt, momentum **단일 전략만**(조합 없음, `combinations: false`). 추천(`recommend`)은 1–3개 조합 허용.
  - 종목 수: 5/10/20
  - 규모: 전체 / 소형 = 시총 하위 30% / 대형 = 시총 상위 30%(백분위 ≥ 0.7)
  - 시총 하한: 없음
  - 리밸런싱: `annual`(연 1회) / `quarterly`(분기 1회) — 아래 "일정"
  - 거래비용: 매수 0, 매도 0.20%(증권거래세, 2026년 세율을 전 기간에 적용 — 확정). 수수료·슬리피지 미반영. 기간마다 전량 매도·재매수(보수적, 사용자 결정). 매도세는 종료된 기간의 마지막 날에만 빼고, 진행 중 기간의 최신일 값에는 빼지 않는다.
  - 마켓타이밍: 포함하지 않음 (Q2 결정, 2026-11-06)
- **일정(2026-11-10)**: 판단일 = 정기 보고서 법정 기한(3/31 사업, 5/15 1Q, 8/14 반기, 11/14 3Q) 이후 첫 거래일에서 **5거래일 뒤**(`schedule.LAG_TRADING_DAYS`, 사용자 결정 — 실제 접수일 없이 지연 제출 보고서를 미리 아는 것을 줄이는 여유분), 매수일 = 그다음 거래일, 기간 마지막 평가일 = 다음 판단일. `annual`은 3/31 기한만 쓴다. 첫 판단 2018-04-09, 첫 매수 2018-04-10(2017 사업보고서 기준). 3월 중 구성은 실제 접수일이 없어 look-ahead가 되므로 하지 않는다.
- **리포트 기간(2026-11-10)**: 사용자가 달력 연도 구간 `start_year`–`end_year`(2018–데이터 기준 연도)를 고른다. 저장된 일별 시계열을 자르고, 직전 연도 마지막 값을 기준점으로 붙여 첫 거래일 수익률도 포함한다. 시작 연도 1월에는 그 전에 구성한 포트폴리오를 보유한 상태다.
- 요청 시 백테스트(그리드 없이)는 테스트 서버 성능을 고려해 쓰지 않는다(2026-11-10 측정: 키 하나 15–95초).
- 벤치마크: KOSPI·KOSDAQ 지수 수익률을 함께 저장한다(리포트 비교용). ~~지수 소스는 FinanceDataReader~~ → **구현: marcap으로 계산한 시가총액 가중 지수**(외부 의존 없음, 가격 수익률).
- **구현 (P5, 2026-11-07)**: `kquant.backtest` — `schedule`(일정), `engine.simulate_period`, `benchmark.market_index`, `grid`(`grid.yaml`, `run_grid`, `load_result`), CLI `kquant run-grid [--full] [--key …]`. 결과는 `dataset/grid/`(git 제외, 다시 만들 수 있음).
  - ~~리밸런싱 일정 `nov_apr`·`semiannual`, 첫 기간 2017-11, 420키~~ → 2026-11-10 위 최종 그리드로 대체.
  - 이탈 처리(기본안, legacy `adj_mode` 대체): 거래 없는 날은 직전 가격으로 평가, 상장폐지 종목은 마지막 거래 가격으로 고정(그 가격에 현금화). legacy의 "이탈일 제거"(상폐 손실을 무시해 낙관 편향)와 "0으로 채움"(거래정지일에 가치 0)은 쓰지 않는다.
  - 매수: 리밸런싱일 종가, 정수 주식, 동일 금액. 평가는 수정종가 비율(기간 중 분할도 정확). 거래비용은 `buy_cost_rate`·`sell_cost_rate`(현재 0·0.002, 리포트 한계 문구에 표시), 배당 미반영(리포트에 고지). (2026-11-09의 편도 0.25%는 2026-11-10 세금만 반영으로 변경.)
  - 거래정지·이상 수익률(2026-11-07 변경): 리밸런싱일에 거래가 없는 종목(행 없음 또는 거래량 0)은 사지 않고 다음 순위로 채운다(`excluded`). 기간 중 이상 수익률을 이유로 미리 빼지 않는다(미래 정보 편향). 보유 종목의 이상일은 `abnormal_held`에 기록하고 리포트에 참고로 표시한다.
  - 기간 말 정산(2026-11-07, 방식 A): 기간 마지막 날 거래정지인 보유 종목은 종료 후 첫 거래일 수정종가로 정산(`settled`), 아직 정지 중이면 잠정 가격 + `pending`.
  - 증분: 데이터(long, adj_events)·코드·그리드 지문이 같으면 종료된 기간은 재사용(`pending` 제외), 진행 중 기간만 재계산.

---

## 9. 리포트 [제안]

- Plotly 기반 **자체 완결형 HTML** 1파일로 만든다(JS 내장, 외부 서버 불필요 → 정적 호스팅·직접 전달 모두 가능).
- 내용:
  - 설정 요약
  - 누적수익 곡선(벤치마크 비교)
  - 낙폭(drawdown) 차트
  - 연도별 수익률 막대 + 표
  - 지표 표: CAGR, 누적, MDD, Sharpe, Sortino, Calmar, 변동성, 승률(양수 연도 비율), 최악 연도
  - 데이터 기준일, 한계(생존편향·배당 표본) 고지
- 종목은 포함하지 않는다.
- 출력 경로는 `KQUANT_REPORT_DIR`이다. 반환 URL은 `KQUANT_REPORT_BASE_URL`이 있으면 링크로, 없으면 파일 경로로 반환한다(호스팅 미정 대응).
- 같은 그리드 키와 데이터 기준일 조합은 캐시해서 재생성하지 않는다.
- **구현 (P6, 2026-11-07)**: `kquant.reports.metrics`(지표), `kquant.reports.html.build_report`(Plotly 내장 HTML: 설정, 지표 타일, 누적 수익 vs 시장 지수, 낙폭, 연도별 막대+표, 지표 표, 기간 표, 데이터 품질 제외 종목, 계산 방식·한계, 고지). 기간은 달력 연도 구간 `start_year`–`end_year`(2026-11-10, 이전 `window_years` 대체). 파일명은 (키, 연도 구간, 그리드 지문, 기준일) 해시라 같은 조합은 다시 만들지 않는다. 색은 dataviz 기본 팔레트 1–3번(검증 통과), 다크 모드 지원.

---

## 10. 종목 추천 + 공시/기사 [결정 + 미결]

- 사용자가 고른 전략 설정으로 **최신 기준일** 스냅샷에 전략을 적용해 상위 N개를 반환한다.
- 반환 항목: 기업명, 종목코드, 시장, 산업, 주요 지표, 전략 내 순위.
- 매수 수량과 매매 신호는 반환하지 않는다. 모든 응답에 "투자 권유 아님" 고지를 붙인다. 이 고지는 MCP 도구 응답 자체에 포함해서 LLM이 빠뜨려도 남게 한다.
- 공시: OpenDART `list.json`(corp_code 지정, 최근 N건)을 쓴다. 공식 API이고 일 2만 건 한도라 충분하다.
- 기사: **보류.** 네이버 검색 API 약관이 결과를 AI에 입력하는 것을 금지하고, 수익화 페이지 노출도 제한한다 [미결 Q5].
- **구현 (P6, 2026-11-07)**: `kquant.service`(공개 도구 진입점: `catalog`, `screen`, `strategy_options`, `strategy_report`, `recommend`), `kquant.ingest.dart_api`(운영자 CLI `kquant update-corp-codes`로 상장사 고유번호 매핑, 추천 시 `list.json` 최근 90일 공시 5건, 타임아웃 10초·1시간 캐시·실패 시 빈 목록+사유). 추천은 최신 가격일을 판단 기준일로 `select`를 그대로 쓴다. 산업 분류는 Q4 결정 전이라 `industry: null`.

---

## 10.1 MCP 서버 + Hermes (구현, P7, 2026-11-07)
- `kquant.mcp_server`(mcp SDK 2.x `MCPServer`, stdio, 실행 `uv run kquant-mcp`): 공개 도구 5개 `list_fields`, `screen`, `strategy_options`, `strategy_report`, `recommend` — 모두 `readOnlyHint`. 서버 `instructions`에 대화 규칙(카탈로그 먼저, 되묻기, 수량·신호 금지, 고지)을 넣었다.
- Claude Code: 저장소 루트 `.mcp.json`으로 같은 서버를 붙인다. 자동 E2E `tests/integration/test_mcp_stdio.py`(실제 서버 프로세스 + MCP 클라이언트).
- Hermes 자산 `hermes/`: `SOUL.md`(공개 프로필 대화 규칙, 2026-11-10 스킬에서 옮김), `config.example.yaml`(공개 프로필의 MCP 등록·`tools.include`·`platform_toolsets`·standalone, API 서버 .env 키. 운영 작업은 운영자 프로필 cron 또는 OS 스케줄러), `README.md`(배포 순서). 설정 키는 2026-11-10 Hermes v0.22.2에서 실제로 띄워 대조했다(§10.2 검증).
- 레드팀 체크리스트 1번 자동화: `kquant check-hermes --url …` — `/v1/toolsets` 응답에서 허용 목록 밖 도구를 찾으면 실패(종료 코드 2).

## 10.2 Docker (구현, P8, 2026-11-10)
- `Dockerfile`: `python:3.13-slim` + uv(`uv sync --frozen`, lock 해시 검증). 단계 `base`(의존성·패키지) → `test`(dev 의존성 + `tests/`, `pytest`) / `runtime`(venv만 복사, uid 1000 비루트, `ENTRYPOINT kquant`). 이미지에는 코드만 넣고 데이터·`.env`·legacy는 `.dockerignore`로 뺀다.
- `compose.yaml`: `kquant`(운영 CLI, `./dataset` → `/data` 읽기·쓰기), `mcp`(`kquant-mcp` stdio, Hermes가 `docker compose run --rm -T mcp`로 띄움 — `/data` 읽기 전용, 쓰기는 `./dataset/reports` → `/reports`뿐, `read_only`·`cap_drop: ALL`·`no-new-privileges`, 비밀값은 `DART_API_KEY`만). 비밀값은 compose 옆 `.env`에서 변수 치환으로만 넣는다.
- 공개 응답의 오류는 최상위 `problem` 키로 준다(`{"ok": false, "problem": {code, message, …}, "disclaimer"}`). Hermes는 최상위 `error` 키를 실패로 세어 3회 연속이면 MCP 서버를 60초(전역) 멈추므로, 되묻기·입력 오류가 다른 사용자를 막지 않게 했다(2026-11-10 결정 D2).
- **공개 운영 구성(2026-11-10 결정 D5)**: Hermes 공개 게이트웨이와 kquant를 한 컨테이너에 두고 MCP는 하위 프로세스로 띄운다(`hermes/compose.public.example.yaml`, `hermes/Dockerfile.public`, 초안·미검증). docker 소켓·host 네트워크·`~/.hermes` 전체 마운트 금지, 데이터 읽기 전용, 공개는 `127.0.0.1:8643`만 — `tests/redteam/test_docker_static.py`로 고정. 위 `mcp` 서비스(docker compose stdio)는 로컬 검증·Claude Code용.
- MCP 도구는 정의되지 않은 인자를 거부한다(`mcp_server._forbid_unknown_args`, 스키마 `additionalProperties: false`, 2026-11-10 레드팀 M1).
- 검증(2026-11-10, Windows PC의 Docker Desktop Linux 컨테이너): 같은 코드·데이터로 `run-grid --full` → 지문 동일(`65001b5cc2ed8a66`), `periods`·`daily`·`benchmark` 1,188/113,778/2,169행이 Windows 결과와 **완전히 같음**(최대 수치 차이 0). 컨테이너 안 `pytest` 108개 통과(실데이터·MCP stdio E2E 포함), `report`·`recommend` CLI와 비루트 사용자의 리포트 쓰기 확인.
- 한계(당시): 첫 검증 PC는 TLS를 가로채는 프록시 환경이라 컨테이너 빌드 안에서 PyPI를 받을 수 없어, 검증 이미지는 호스트에서 해시 검증으로 받은 Linux 패키지를 넣어 만들었다(저장소 밖 임시 Dockerfile, 런타임 구성은 동일). → 아래 마무리 검증에서 해소.
- **마무리 검증(2026-11-10, 프록시 없는 환경, Docker Desktop 27.3.1 Linux)**:
  - 저장소 `Dockerfile`·`compose.yaml` 그대로 `docker compose build`(약 1분, `uv sync --frozen`, uv 0.11.19 태그 존재 확인)와 `--target test` 빌드 성공. 컨테이너 `pytest` 120 통과·3 skip(데이터 읽기 전용 마운트). Docker 설정 파일 정적 점검 2개는 이미지에 파일이 없어 실패하던 것을 skip으로 바꿨다.
  - 같은 PC·같은 데이터(marcap ~2026-10-23)에서 컨테이너 `run-grid --full`과 Windows 호스트 결과 완전히 같음: 지문 `adcc0993a68ee9d2`, `periods` 1,188·`daily` 113,022·`benchmark` 2,160행, 최대 차이 0.
  - 컨테이너 네트워크 작업: `update-prices`(marcap 2026 → 2026-11-06, 이벤트 72,924 → 72,928, 기존 이벤트 변경 0), `update-corp-codes`(4,013건), `recommend`에 DART 공시 첨부 확인. 이후 증분 그리드 지문 `692ae15f3ba36629`(기준일 2026-11-06).
  - 공개 `mcp` 서비스(읽기 전용·cap_drop)에 MCP stdio 클라이언트로 연결: 도구 5개, 리포트 쓰기(`/reports`), 공시 첨부, 삭제된 인자(`min_marcap`) 거부 확인.
  - Hermes v0.22.2 격리 프로필(`kquantpub`, standalone, :8643)에서 `mcp_servers`(docker compose stdio)로 E2E: 카탈로그 안내, 기간 모호 시 되묻기, 리포트 생성·고지, 수량 요청 거부, 프롬프트 주입(`terminal`·`update-prices`) 거부 — 실제 노출 도구는 `skill_manage`·`skill_view`·`skills_list`와 메타 도구 `tool_search`·`tool_describe`·`tool_call`(kquant 5개만 대리 호출)뿐.
  - `check-hermes`: v0.22 `/v1/toolsets`는 꺼진 toolset도 `enabled: false`로 주므로 켜진 항목만 판정하도록 고쳤다(이전 판정은 항상 실패). MCP 도구는 이 목록에 나오지 않는다.

## 11. 레드팀 검증 체계 [제안]

레드팀은 단계적으로 구축한다. 각 단계 산출물은 `tests/redteam/`의 자동 테스트와 `docs/redteam/`의 점검 기록이다.

| 영역 | 공격/점검 질문 | 형태 |
|---|---|---|
| 데이터 정합성 | 수정종가가 분할일에 연속인가? 재무 항목이 연도별로 같은 의미로 매핑되는가? 단위 이상치가 섞였나? | 속성 기반 테스트 + 표본 수기 대조 |
| 시점 편향 | 백테스트가 기준일 이후 공시값·배당을 쓰는가? 상폐 종목이 빠져 있나(생존편향)? | 시점 이동(재무값을 미래로 미는) 테스트, 상폐 종목 포함 확인 |
| 전략 정확성 | 전략 지표·순위가 정의대로 계산되는가? (legacy 비교는 로컬 참고용) | 표본 손계산 대조 + 단위 테스트 |
| 도구 보안 | 필터 DSL로 SQL 주입·경로 조작이 가능한가? 공개 프로필에 terminal이 노출되나? 프롬프트 주입으로 운영 도구를 호출할 수 있나? | 퍼징 + `/v1/toolsets` 점검 + 주입 시나리오 |
| 출력 준수 | 응답에 매수 수량·매매 신호가 섞이나? 고지가 빠지나? 카탈로그 밖 항목을 지어내나? | 대화 시나리오 평가 |

- Claude Code 쪽에는 레드팀 전용 서브에이전트(`.claude/agents/red-team.md`)를 두고, 각 단계 완료 시 실행한다.
- **구현 (P3–P7, 2026-11-07)**: `tests/redteam/` — `test_screen_injection.py`(필터 DSL 주입·퍼징), `test_lookahead.py`(미래 공시값 변조·가격 접근 감시), `test_mcp_surface.py`(도구 화이트리스트·읽기 전용 표시, 공개 경로의 운영 함수 호출 금지(AST 검사), 알 수 없는 도구·잘못된 타입 거부, 오류·성공 응답의 고지, Hermes 노출 도구 판정). 출력 준수(수량·신호 키 없음)는 `tests/unit/test_service.py`.

---

## 12. 단계 계획

| 단계 | 내용 | 완료 조건 |
|---|---|---|
| P0 | git 초기화(키 제거 후), pyproject, 기준 데이터 스냅샷 | legacy 코드가 pandas 2.x 별도 환경에서 실행됨 |
| P1 | 수정종가 재구현 + marcap 동기화 | §4 회귀 리포트 + 운영자 확인 |
| P2 | 재무 수집·정리(long) + 시점 정합성 + 품질 검사 | 기존 corp_fin과 대조 리포트 + 운영자 확인 |
| P3 | 카탈로그 + 필터 DSL + 조건 검색 | 예시 질의 통과 + 주입 레드팀 |
| P4 | 서비스 전략 3종(value·salesmt·momentum) + 공통 유니버스 | (1) 표본 종목의 지표·순위를 손으로 재계산해 일치 (2) 유니버스 규칙별 단위 테스트 (3) look-ahead 레드팀: as_of 이후 공시·가격을 쓰지 않음(재무값을 미래로 밀면 결과가 바뀌지 않아야 함) |
| P5 | 백테스트 엔진 + 그리드 증분 | (1) 합성 데이터에서 손계산 수익률과 일치 (2) 회계 항등식: 매 거래일 보유평가액 + 현금 = 총액, 기간 이월 금액 연속 (3) 증분 결과 = 전체 재계산 결과 (4) 상폐 종목이 표본에 남아 있음(생존편향 점검) |
| P6 | HTML 리포트 + 추천 + 공시 | 샘플 리포트 확인 |
| P7 | MCP 서버 + Hermes skill + 공개 프로필 설정 + 보안 레드팀 | Claude Code·Hermes 양쪽에서 E2E |
| P8 | Docker화 | Linux 컨테이너에서 전체 파이프라인 실행 |

---

## 13. 미결 질문 (운영자)

- ~~Q1. 재무제표 원본~~ → 해결(2026-11-06): 원본 txt·zip으로 2016 1Q–2026 2Q 전 분기를 확보했다.
- ~~Q2. 마켓타이밍~~ → 해결(2026-11-06): 그리드에 포함하지 않는다.
- ~~Q3. 기존 전략의 의도치 않은 동작~~ → 해결(2026-11-07): legacy 전략을 옮기지 않고 서비스 전략 3종을 새로 정의했다(§7). legacy 문제는 로컬 `local/docs/legacy-issues.md`에만 기록한다.
- **Q4. 산업 분류 소스:** 현재 `Industry`는 FinanceDataReader 상장목록 기반이다(2026 초 장애 보고). 후보는 DART 기업개황(`induty_code`, 표준산업분류), KIND 상장법인목록, 배당 정보의 업종이다.
- **Q5. 기사:** 네이버 API 약관 문제로 보류하고, 우선 공시만 제공할 것을 제안한다. 대안은 언론사 RSS(약관 개별 확인), 또는 링크만 반환하고 LLM에는 넘기지 않는 방식이다.
- **Q6. 서비스 유료화 여부:** 리포트 고지 문구와 네이버 등 외부 데이터 약관 판단에 영향을 준다.
