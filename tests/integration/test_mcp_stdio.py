"""MCP stdio E2E (P7): 실제 서버 프로세스를 띄워 MCP 클라이언트로 공개 도구를 호출한다."""

import json
import os
import sys

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from kquant.mcp_server import PUBLIC_TOOLS

pytestmark = pytest.mark.data


def _text(result) -> dict:
    return json.loads(result.content[0].text)


async def _session_run(fn):
    env = {**os.environ, "PYTHONUTF8": "1"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "kquant.mcp_server"], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        return await fn(s)


def test_stdio_end_to_end(real_ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("KQUANT_REPORT_DIR", str(tmp_path))

    async def scenario(s):
        tools = await s.list_tools()
        names = sorted(t.name for t in tools.tools)
        cat = _text(await s.call_tool("list_fields", {}))
        bad = _text(await s.call_tool("screen", {"where": [{"field": "revenue", "op": ">", "value": "1조"}]}))
        good = _text(await s.call_tool("screen", {"where": [{"field": "revenue", "period": "ttm", "op": ">",
                                                             "value": "10조"}], "limit": 3}))
        return names, cat, bad, good

    names, cat, bad, good = anyio.run(_session_run, scenario)
    assert names == sorted(PUBLIC_TOOLS)
    assert cat["ok"] and len(cat["fields"]) > 30
    assert bad["ok"] is False and bad["problem"]["code"] == "period_required"
    assert good["ok"] and good["returned"] == 3
    assert "recommend" not in names
    for r in (cat, bad, good):
        assert "투자 권유가 아닙니다" in r["disclaimer"]
