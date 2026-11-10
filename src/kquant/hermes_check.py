"""레드팀 체크리스트 1번: Hermes API Server의 실제 노출 도구 점검 (spec §1·§11).

``GET {url}/v1/toolsets``(Hermes v0.22: ``{"object": "list", "platform": "api_server", "data": [{"name", "enabled",
"tools"}…]}``, 꺼진 toolset도 ``enabled: false``로 함께 나온다)에서 **켜진 항목의 도구 이름**을 모아, 허용 목록 밖의
도구가 있으면 실패로 보고한다.

판정은 실패 쪽으로 닫혀 있다(fail-closed, 2026-11-10 레드팀 H-1~H-3):
- 형식을 모르는 응답, 오류 본문(``error`` 키), 켜진 항목에 ``tools`` 문자열 목록이 없는 경우, ``data`` 밖의 추가 키,
  ``data``가 빈 응답은 모두 실패다. ``data``가 있고 켜진 내장 toolset이 하나도 없으면 통과다(공개 프로필의 정상 상태 —
  MCP 도구는 이 목록에 나오지 않는다). Hermes 형식이 바뀌면 이 모듈을 그 형식에 맞춰 고친 뒤 통과시킨다.
- 이 엔드포인트는 ``api_server`` 플랫폼만 보여 준다. 다른 플랫폼(telegram 등)의 toolset은 점검하지 못한다.
- v0.22에서 MCP 서버 도구는 이 목록에 나오지 않는다. MCP 노출 범위는 ``mcp_servers.<name>.tools.include``와 kquant MCP
  서버의 도구 목록으로 보장한다. 나오는 버전이면 정확한 이름(``mcp__kquant_public__{도구}``)만 허용한다.
"""

from __future__ import annotations

import os

import requests

from kquant.mcp_server import PUBLIC_TOOLS

# 공개 프로필에는 kquant 도구만 둔다(2026-11-10 결정: skills toolset 제외 — skill_manage가 묶여 있어 스킬 쓰기를 끌 수 없음).
MCP_TOOLS = {f"{p}{t}" for t in PUBLIC_TOOLS for p in ("mcp__kquant_public__", "mcp_kquant_public_")}
ALLOWED = {*PUBLIC_TOOLS, *MCP_TOOLS}
DANGEROUS = ("terminal", "shell", "bash", "file", "browser", "code", "python", "exec", "web", "fetch", "computer",
             "manage", "write", "patch", "process")
_ENVELOPE_KEYS = {"object", "platform", "data"}


def _is_v022(payload) -> bool:
    data = payload.get("data") if isinstance(payload, dict) else None
    return (isinstance(data, list) and bool(data)
            and all(isinstance(x, dict) and isinstance(x.get("enabled"), bool) for x in data))


def evaluate(payload) -> dict:
    problems: list[str] = []
    names: set[str] = set()
    if isinstance(payload, dict) and "error" in payload:
        problems.append("error_response")
    if not _is_v022(payload):
        problems.append("unknown_format")
    else:
        extra_keys = sorted(set(payload) - _ENVELOPE_KEYS)
        if extra_keys:
            problems.append("unexpected_keys:" + ",".join(map(str, extra_keys)))
        if payload.get("platform") not in (None, "api_server"):
            problems.append(f"platform:{payload.get('platform')}")
        for item in payload["data"]:
            if not item["enabled"]:
                continue
            tools = item.get("tools")
            if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
                problems.append(f"malformed_tools:{item.get('name')}")
                continue
            names |= set(tools)
    extra = sorted(n for n in names if n not in ALLOWED)
    danger = sorted(n for n in extra if any(d in n.lower() for d in DANGEROUS))
    return {"ok": not extra and not problems, "names": sorted(names), "unexpected": extra, "dangerous": danger,
            "problems": problems}


def check(url: str, timeout: int = 10) -> dict:
    headers = {}
    key = os.environ.get("HERMES_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    r = requests.get(f"{url.rstrip('/')}/v1/toolsets", headers=headers, timeout=timeout)
    r.raise_for_status()
    return evaluate(r.json())
