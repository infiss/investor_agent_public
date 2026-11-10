# Hermes 연동 (P7)

kquant 코어는 Hermes를 모른다. Hermes는 `kquant-public` MCP 서버(stdio)를 통해서만 kquant를 쓴다.

| 파일 | 내용 |
|---|---|
| `SOUL.md` | 공개 프로필의 시스템 프롬프트(대화 규칙): 카탈로그 먼저, 모호하면 되묻기, 종목은 요청 시에만, 수량·비중·신호 금지, 고지. 운영자만 고친다 |
| `config.example.yaml` | 공개 프로필 config.yaml(MCP 서버 등록·`tools.include`·`platform_toolsets`·standalone)과 .env 키 — Hermes v0.22.2에서 확인 |
| `compose.public.example.yaml`, `Dockerfile.public` | 공개 Hermes 게이트웨이 + kquant MCP 한 컨테이너 — **초안(미검증)**, 클라우드 배포 준비 때 빌드·검증 |
| `compose.webui.local.yaml` | **로컬 1인 채팅**: Open WebUI(웹 채팅, 127.0.0.1:3000) + 리포트 정적 서버(127.0.0.1:8650) — 이 PC 전용 |
| `../.mcp.json` | Claude Code에서 같은 서버를 붙여 E2E 확인 |

## 로컬에서 웹 채팅으로 쓰기 (1인, 2026-11-10)
브라우저 `http://127.0.0.1:3000` → Open WebUI → Hermes `kquantpub` 게이트웨이(:8643) → kquant MCP. 리포트 링크는 `http://127.0.0.1:8650/…`로 새 탭에서 열린다.
1. Docker Desktop 실행(`docker info`가 되는지 확인).
2. Hermes 프로필 `kquantpub`의 `config.yaml` → `mcp_servers.kquant-public.env`에 `KQUANT_REPORT_BASE_URL: http://127.0.0.1:8650`
   (compose `mcp` 서비스가 이 값을 컨테이너로 넘긴다. 없으면 응답에 파일명만 나온다).
3. 별도 창에서 `hermes -p kquantpub gateway run`(켜 둔다).
4. 저장소 루트에서(PowerShell):
   ```powershell
   $env:HERMES_API_KEY = (Get-Content "$env:LOCALAPPDATA\hermes\profiles\kquantpub\.env" | Select-String '^API_SERVER_KEY=').Line.Split('=',2)[1]
   docker compose -f hermes/compose.webui.local.yaml up -d
   ```
   첫 실행은 Open WebUI가 내장 모델 파일을 받느라 1분 정도 걸린다. 모델 목록의 `kquantpub`을 고르고 대화한다.
5. 끄기: `docker compose -f hermes/compose.webui.local.yaml down`, 게이트웨이 창에서 Ctrl+C.
- 로그인 없이 쓰는 구성(`WEBUI_AUTH=false`)이라 127.0.0.1에만 연다. 제목·태그·후속 질문 자동 생성은 꺼 두었다(요청마다 에이전트가 도구까지 따로 돌기 때문).
- 시작 순서: Docker → 게이트웨이 → Open WebUI. Docker가 꺼진 채 게이트웨이가 뜨면 kquant 연결이 멈춘 상태로 남는다(게이트웨이 재시작).
- 공개 배포용이 아니다. 공개 서버에서는 인증·요청 제한·TLS 프록시를 붙이고 Hermes 8643은 내부에만 둔다(아래 "클라우드 서버 운영 메모").

## Docker로 배포할 때 (P8)
저장소 루트의 `Dockerfile`·`compose.yaml`을 쓴다. 이미지에는 코드만 있고 데이터는 `./dataset`을 `/data`로 붙인다.
```bash
docker compose build
docker compose run --rm kquant update-prices --year 2026   # 연도별 marcap(네트워크)
docker compose run --rm kquant run-grid --full
docker compose run --rm kquant update-corp-codes            # .env의 DART_API_KEY
```
공개 운영(2026-11-10 결정 D5): Hermes 공개 게이트웨이와 kquant를 **한 컨테이너**에 두고 MCP는 하위 프로세스
(`command: /opt/kquant-venv/bin/kquant-mcp`)로 띄운다(`compose.public.example.yaml`). Hermes가 호스트에서 `docker compose run … mcp`로
띄우는 방식은 게이트웨이 사용자에게 docker 소켓 권한(사실상 호스트 root)이 필요해 로컬 검증에만 쓴다.
저장소 `compose.yaml`의 `kquant` 서비스는 운영 작업(데이터 갱신·그리드), `mcp` 서비스는 로컬 검증·Claude Code용이다. 비밀값은 `.env`(git·이미지 밖)로만 넣는다.

## 배포 순서
1. 서버에 저장소와 데이터(`KQUANT_DATA_DIR`)를 둔다. git에 있는 산출물(`dataset/fin/*.parquet`, `dataset/prices/adj_events.parquet`) 외에
   marcap 원본은 `kquant update-prices --year YYYY`로 받는다(연도별).
2. `uv sync` → `uv run kquant run-grid --full`(약 2분) → `uv run kquant update-corp-codes`(DART_API_KEY 필요).
3. 공개용 프로필을 따로 만든다(`hermes profile create kquantpub --no-skills --no-alias`). 그 프로필 폴더의 `config.yaml`·`.env`를
   `config.example.yaml`대로 채우고, `SOUL.md`를 프로필 폴더의 `SOUL.md`로 복사한다. 게이트웨이: `hermes -p kquantpub gateway run`.
   스킬은 두지 않는다(2026-11-10 결정): Hermes `skills` toolset에는 스킬 쓰기 도구 `skill_manage`가 묶여 있고, 대화 후 자동 검토가
   공개 프로필의 스킬을 실제로 고쳤다. Hermes v0.22는 MCP 서버 `instructions`를 모델에 전달하지 않으므로 규칙은 `SOUL.md`로 넣는다.
4. **레드팀 체크리스트 1번**: `uv run kquant check-hermes`(기본 `http://127.0.0.1:8643`, `KQUANT_HERMES_URL`로 변경)로 공개 프로필의 실제 노출 도구를 확인한다.
   포트: 공개 프로필 8643, Hermes 기본 포트 8642는 운영자 프로필 몫(2026-11-10 결정). 다른 포트를 쓰면 `--url`로 반드시 공개 프로필을 가리킨다.
   켜진 내장 toolset이 하나라도 있으면(terminal, file, browser, code, skills 등) 배포하지 않는다. 정상 결과는 `ok: true`, `names: []`.
   Hermes v0.22의 `/v1/toolsets`는 꺼진 toolset도 `enabled: false`로 함께 주므로 켜진 항목만 판정한다. MCP 도구는 이 목록에 나오지 않는다
   (`tools.include`로 제한). 판정은 실패 쪽으로 닫혀 있어 모르는 형식·빈 목록·오류 본문도 실패다. 이 엔드포인트는 `api_server` 플랫폼만 보여 주므로
   telegram 등 다른 플랫폼의 `platform_toolsets`는 설정 파일로 따로 확인한다.

## 클라우드 서버 운영 메모 (2026-11-10, 아직 배포 전 — 계획)
- **포트·노출**: 공개 프로필 API 서버는 `127.0.0.1:8643`에만 바인딩하고, 외부에는 앞단 리버스 프록시(TLS, 443)로만 연다.
  8642(운영 프로필)·9119(대시보드)는 외부에 열지 않는다(방화벽·보안 그룹에서 443만 허용).
- **점검**: `kquant check-hermes`는 서버 안에서 loopback으로 돌린다(기본값 그대로). 밖에서 프록시 주소로 점검하려면
  `KQUANT_HERMES_URL=https://…`. 배포·설정 변경 때마다 실행하고 종료 코드 2면 배포하지 않는다.
- **시작 순서**: Docker(엔진) → Hermes 게이트웨이. 게이트웨이가 먼저 뜨면 kquant MCP 연결이 실패한 채 멈춰 있고 모델이 도구 없이 답한다
  (2026-11-10 확인). systemd라면 게이트웨이 유닛에 `After=docker.service`·`Requires=docker.service`를 둔다.
- **비밀값**: `API_SERVER_KEY`(프로필 .env), `DART_API_KEY`(compose 옆 .env)는 서버 secret으로만 넣는다. 이미지·git에 넣지 않는다.
- **리포트 링크**: `KQUANT_REPORT_BASE_URL`을 정적 호스팅 주소로 두면 응답에 링크가 나간다(없으면 파일명만).
- **데이터 갱신**: 운영자 cron/systemd timer로 `update-prices` → `run-grid`(공개 프로필에는 두지 않음).
- **컨테이너 구성(D5)**: `compose.public.example.yaml` — docker 소켓 마운트 금지, `network_mode: host` 금지, `~/.hermes` 전체가 아닌 공개 프로필 홈만,
  데이터 읽기 전용. 공식 Hermes compose는 host 네트워크·`~/.hermes` 전체 마운트라 그대로 쓰지 않는다. 시작 순서 문제(Docker → 게이트웨이)는 한 컨테이너 구성에서는 사라진다.

## 운영 작업은 공개 서버에 없다
데이터 갱신(`update-prices`, `build-fin`), 그리드 계산(`run-grid`), 고유번호 갱신(`update-corp-codes`)은 CLI 전용이다.
