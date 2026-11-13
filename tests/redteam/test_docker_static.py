"""레드팀(2026-11-10): 컨테이너 구성 정적 점검 — 이미지에 비밀값·데이터·legacy가 들어가지 않고,
공개 MCP 컨테이너는 최소 권한으로 돈다."""

import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

# 컨테이너 test 이미지에는 Docker 설정 파일이 없다 → 이미지 표식(Dockerfile test 단계 ENV)이 있을 때만 건너뛴다.
# 파일 유무로 판단하면 저장소에서 .dockerignore가 사라져도 조용히 skip된다(2026-11-10 레드팀 D-1).
pytestmark = pytest.mark.skipif(os.environ.get("KQUANT_TEST_IN_IMAGE") == "1", reason="컨테이너 test 이미지 안")


def test_dockerignore_and_runtime_user():
    ign = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for pat in ("**/.env", "dataset", "local", "국내퀀트코드", ".git"):
        assert pat in ign, pat
    runtime = (ROOT / "Dockerfile").read_text(encoding="utf-8").split("AS runtime", 1)[1]
    assert "USER kquant" in runtime and "COPY src" not in runtime and "COPY tests" not in runtime


def test_mcp_service_least_privilege():
    svc = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))["services"]["mcp"]
    data = [v for v in svc["volumes"] if v.split(":")[1] == "/data"]
    assert data and all(v.endswith(":ro") for v in data)
    assert "env_file" not in svc and set(svc["environment"]) <= {"KQUANT_DATA_DIR", "KQUANT_REPORT_DIR",
                                                                  "KQUANT_REPORT_BASE_URL"}
    assert svc.get("read_only") is True and svc.get("cap_drop") == ["ALL"]
    assert svc.get("network_mode") == "none"  # 공개 도구는 외부 네트워크가 필요 없다(2026-11-13)
    assert "no-new-privileges:true" in svc.get("security_opt", [])


def test_public_hermes_container_draft_conditions():
    """2026-11-10 결정 D5: 공개 Hermes 컨테이너(초안)는 docker 소켓·host 네트워크·~/.hermes 전체 마운트 없이,
    데이터 읽기 전용·127.0.0.1:8643만 공개. MCP는 컨테이너 안 하위 프로세스(kquant-mcp)."""
    path = ROOT / "hermes" / "compose.public.example.yaml"
    svc = yaml.safe_load(path.read_text(encoding="utf-8"))["services"]["hermes-public"]
    vols = svc["volumes"]
    assert not any("docker.sock" in v for v in vols)
    assert svc.get("network_mode") != "host" and svc.get("privileged") is not True
    assert not any(v.split(":")[0].rstrip("/") in ("~/.hermes", "${HOME}/.hermes") for v in vols)
    data = [v for v in vols if v.split(":")[1] == "/data"]
    assert data and all(v.endswith(":ro") for v in data)
    assert svc["ports"] == ["127.0.0.1:8643:8643"]
    assert svc.get("cap_drop") == ["ALL"] and "no-new-privileges:true" in svc.get("security_opt", [])
    assert set(svc["environment"]) <= {"KQUANT_DATA_DIR", "KQUANT_REPORT_DIR", "KQUANT_REPORT_BASE_URL"}
    dockerfile = (ROOT / "hermes" / "Dockerfile.public").read_text(encoding="utf-8")
    assert "docker.sock" not in dockerfile and "docker-ce" not in dockerfile and "docker.io" not in dockerfile
    cfg = yaml.safe_load((ROOT / "hermes" / "config.example.yaml").read_text(encoding="utf-8"))
    assert cfg["mcp_servers"]["kquant-public"]["command"].endswith("kquant-mcp")


def test_local_webui_binds_loopback_only():
    """2026-11-10 로컬 1인 채팅(Open WebUI + 리포트 서버): 127.0.0.1에만 열고, Hermes 키는 파일에 쓰지 않으며,
    리포트는 읽기 전용으로만 제공한다. 인증을 끈 구성이라 외부 바인딩은 금지."""
    svcs = yaml.safe_load((ROOT / "hermes" / "compose.webui.local.yaml").read_text(encoding="utf-8"))["services"]
    for name, svc in svcs.items():
        assert all(p.startswith("127.0.0.1:") for p in svc.get("ports", [])), name
        assert not any("docker.sock" in v for v in svc.get("volumes", [])), name
        assert svc.get("network_mode") != "host", name
    assert svcs["open-webui"]["environment"]["OPENAI_API_KEY"].startswith("${HERMES_API_KEY")
    assert "@sha256:" in svcs["open-webui"]["image"]
    # 2026-11-13: 도구 없이 답한 응답에도 고지가 보이도록 화면 상단 배너(dismissible=false: 닫아도 새로고침하면 다시 표시)에 같은 문구
    import json

    from kquant.disclaimer import DISCLAIMER

    banners = json.loads(svcs["open-webui"]["environment"]["WEBUI_BANNERS"])
    assert any(DISCLAIMER in b["content"] and b["dismissible"] is False for b in banners)
    assert svcs["open-webui"]["environment"]["ENABLE_PERSISTENT_CONFIG"] == "false"  # DB의 빈 배너가 덮어쓰지 않게
    assert all(v.endswith(":ro") for v in svcs["reports"]["volumes"])
