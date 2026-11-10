"""레드팀: 공개 MCP 서버 공격면 (spec §11 도구 보안·출력 준수).

- 노출 도구는 화이트리스트 다섯 개뿐이고 모두 읽기 전용 표시를 가진다.
- 서버·서비스 코드는 운영 작업(데이터 갱신·다운로드·그리드 계산·임의 실행)을 호출하지 않는다.
- 이상한 인자는 실행 전에 거부되거나 구조화된 오류로 돌아오며, 성공·오류 모두 고지를 포함한다.
"""

import ast
import asyncio
import json
import os
from pathlib import Path

import pytest

import kquant.mcp_server as srv
from kquant.mcp_server import PUBLIC_TOOLS, build_server

SRC = Path(srv.__file__).resolve().parent
FORBIDDEN_CALLS = {"run_grid", "sync_year", "update_corp_codes", "build_long", "build_events", "update_events",
                   "rebuild_events", "system", "popen", "Popen", "eval", "exec", "compile", "__import__", "remove",
                   "unlink", "rmtree", "to_parquet"}
OPERATOR_WORDS = ("update", "download", "ingest", "build", "run", "write", "delete", "grid", "admin", "exec", "shell")


def _call(name, args):
    return asyncio.run(build_server().call_tool(name, args))


def test_tool_whitelist_and_annotations():
    tools = asyncio.run(build_server().list_tools())
    assert sorted(t.name for t in tools) == sorted(PUBLIC_TOOLS)
    for t in tools:
        assert t.annotations and t.annotations.read_only_hint and not t.annotations.destructive_hint
        assert not any(w in t.name for w in OPERATOR_WORDS), t.name


@pytest.mark.parametrize("module", ["mcp_server.py", "service.py", "catalog/screen.py", "catalog/features.py"])
def test_public_path_calls_no_operator_functions(module):
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            called.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    assert not (called & FORBIDDEN_CALLS), called & FORBIDDEN_CALLS
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom | ast.Import) for a in n.names}
    assert not ({"subprocess", "shutil", "run_grid", "sync_year", "update_corp_codes"} & imported)


def test_unknown_tool_rejected():
    from mcp.server.mcpserver.exceptions import ToolError

    for name in ["update_prices", "run_grid", "kquant", "../list_fields", "screen; rm"]:
        with pytest.raises(ToolError):
            _call(name, {})


def test_bad_argument_types_rejected_before_execution():
    from mcp.server.mcpserver.exceptions import ToolError

    for name, args in [("strategy_report", {}), ("strategy_report", {"strategy": "value", "n": "abc"}),
                       ("recommend", {"strategies": ["value"], "include_disclosures": "yes please"}),
                       ("screen", {"where": "DROP TABLE"}), ("screen", {"limit": "all"})]:
        with pytest.raises(ToolError):
            _call(name, args)


@pytest.mark.parametrize("name,args", [
    # 2026-11-10 레드팀 M1: 삭제된 파라미터를 조용히 버리면 요청과 다른 설정의 성과가 나간다
    ("strategy_report", {"strategy": "value", "min_marcap": "300억"}),
    ("strategy_report", {"strategy": "value", "size_cut": 0.1}),
    ("strategy_report", {"strategy": "value", "window_years": 3}),
    ("strategy_report", {"strategy": "value", "bogus": 1}),
    ("recommend", {"strategies": ["value"], "size_cut": 0.1, "include_disclosures": False}),
    ("screen", {"limit": 3, "path": "../../etc"}),
])
def test_unknown_or_removed_args_rejected(name, args):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError):
        _call(name, args)


def test_schemas_forbid_additional_properties():
    for t in asyncio.run(build_server().list_tools()):
        assert t.input_schema.get("additionalProperties") is False, t.name


def test_structured_errors_carry_disclaimer(monkeypatch, ctx):
    monkeypatch.setattr(srv, "_ctx", ctx)
    cases = [
        ("strategy_report", {"strategy": "../../etc/passwd"}),
        ("strategy_report", {"strategy": "value", "schedule": "<script>alert(1)</script>"}),
        ("strategy_report", {"strategy": "value", "start_year": 2030, "end_year": 2017}),
        ("recommend", {"strategies": ["value"], "n": 10_000}),
        ("recommend", {"strategies": ["value"], "min_marcap": "__import__('os')"}),
        ("screen", {"where": [{"field": "pbr", "op": "LIKE", "value": "%"}]}),
        ("screen", {"as_of": "../../../../windows/system32"}),
        ("screen", {"where": [{"field": "revenue", "op": ">", "value": "1조"}]}),  # 기간 되묻기
    ]
    for name, args in cases:
        out = json.loads(_call(name, args).content[0].text)
        assert out["ok"] is False and out["problem"]["code"], (name, out)
        assert "투자 권유가 아닙니다" in out["disclaimer"]
        # 2026-11-10 결정 D2: Hermes는 최상위 "error" 키가 있으면 실패로 세고 3회 연속이면 서버를 60초(전역) 멈춘다
        assert "error" not in out, (name, out)


def test_success_responses_carry_disclaimer(monkeypatch, ctx):
    monkeypatch.setattr(srv, "_ctx", ctx)
    for name, args in [("screen", {"limit": 3}), ("strategy_options", {})]:
        out = json.loads(_call(name, args).content[0].text)
        assert out["ok"] and "투자 권유가 아닙니다" in out["disclaimer"]


def test_hermes_toolset_check_unknown_format_fails():
    """설계 당시 가정한 형식(`toolsets` 키)은 실제 Hermes 형식이 아니다 → 모르는 형식은 실패(fail-closed)."""
    from kquant.hermes_check import evaluate

    good_looking = {"toolsets": [{"name": "mcp-kquant-public", "tools": [{"name": t} for t in PUBLIC_TOOLS]}]}
    assert not evaluate(good_looking)["ok"]


def _v022(enabled: dict[str, list[str]]) -> dict:
    """Hermes v0.22 `/v1/toolsets` 형식: 꺼진 toolset도 enabled=false로 함께 나온다(2026-11-10 실측)."""
    rows = [{"name": "terminal", "tools": ["terminal", "process"]}, {"name": "file", "tools": ["read_file"]},
            {"name": "skills", "tools": ["skill_manage", "skill_view", "skills_list"]}]
    return {"object": "list", "platform": "api_server",
            "data": [{**r, "tools": enabled.get(r["name"], r["tools"]), "enabled": r["name"] in enabled}
                     for r in rows]}


def test_hermes_toolset_check_v022_enabled_only():
    from kquant.hermes_check import evaluate

    # 켜진 내장 toolset이 없으면 통과(공개 프로필 정상 상태). 꺼진 terminal·file은 노출이 아니다
    assert evaluate(_v022({}))["ok"]
    # skills toolset은 공개 프로필에서 실패(2026-11-10 결정: skill_manage가 묶여 있음)
    res = evaluate(_v022({"skills": ["skill_manage", "skill_view", "skills_list"]}))
    assert not res["ok"] and res["dangerous"] == ["skill_manage"]
    assert not evaluate(_v022({"skills": ["skill_view", "skills_list"]}))["ok"]
    res = evaluate(_v022({"terminal": ["terminal", "process"]}))
    assert not res["ok"] and "terminal" in res["dangerous"]


# 2026-11-10 레드팀 H-1~H-3: 판정이 빈 응답·다른 형식·접두사로 통과하지 않는다(fail-closed)
@pytest.mark.parametrize("payload", [
    {}, [], {"object": "list", "data": []},
    {"error": {"message": "unauthorized"}},
    {"toolsets": {"terminal": {"enabled": True}}},
    {"platforms": {"api_server": {"tools": "terminal"}}},
    {"data": [{"name": "skills", "enabled": True}]},                                  # tools 누락
    {"data": [{"name": "skills", "enabled": True, "tools": {"skill_manage": {}}}]},   # dict 형태
    {"data": [{"name": "skills", "enabled": True, "tools": ["skill_view"]}], "mcp_tools": ["terminal"]},
    {"data": [{"name": "skills", "enabled": "true", "tools": ["terminal"]}]},          # enabled 문자열
    {"data": [{"name": "kquant", "enabled": True,
               "tools": ["mcp_kquant_public_terminal", "mcp_kquant-public_shell_exec", "mcp_kquantpwn"]}]},
    {"data": [{"name": "skills", "enabled": True, "tools": ["Terminal"]}]},
])
def test_hermes_check_fails_closed(payload):
    from kquant.hermes_check import evaluate

    assert not evaluate(payload)["ok"]


def test_hermes_check_allows_exact_mcp_names():
    from kquant.hermes_check import evaluate

    tools = [f"mcp__kquant_public__{t}" for t in PUBLIC_TOOLS]
    assert evaluate({"object": "list", "platform": "api_server",
                     "data": [{"name": "kquant-public", "enabled": True, "tools": tools}]})["ok"]


@pytest.mark.skipif(os.environ.get("KQUANT_TEST_IN_IMAGE") == "1", reason="이미지에 hermes/ 없음")
def test_hermes_example_matches_public_tools():
    import yaml

    root = Path(__file__).resolve().parents[2]
    cfg = yaml.safe_load((root / "hermes" / "config.example.yaml").read_text(encoding="utf-8"))
    mcp = cfg["mcp_servers"]["kquant-public"]
    assert set(mcp["tools"]["include"]) == set(PUBLIC_TOOLS)
    assert mcp["tools"]["resources"] is False and mcp["tools"]["prompts"] is False and "env" not in mcp
    assert cfg["platform_toolsets"]["api_server"] == ["kquant-public"]
    assert cfg["gateway"]["standalone"] is True
    assert cfg["curator"]["enabled"] is False and cfg["skills"]["write_approval"] is True
    assert cfg["memory"]["memory_enabled"] is False and cfg["memory"]["user_profile_enabled"] is False
    # 대화 규칙은 SOUL.md로(Hermes는 MCP instructions를 모델에 전달하지 않음): 핵심 규칙이 빠지지 않았는지
    soul = (root / "hermes" / "SOUL.md").read_text(encoding="utf-8")
    for rule in ("list_fields", "되묻", "매수 수량", "금액 배분", "목표가", "disclaimer", "지시문"):
        assert rule in soul, rule


# ---- 레드팀 점검(2026-11-07) 발견 사항 회귀 테스트 ----
BIG = 10**400


@pytest.mark.parametrize("name,args", [
    ("screen", {"where": [{"field": "market_cap", "op": ">", "value": BIG}]}),
    ("screen", {"where": [{"field": "market_cap", "op": "between", "value": [0, BIG]}]}),
    ("recommend", {"strategies": ["value"], "min_marcap": BIG, "include_disclosures": False}),
    ("strategy_report", {"strategy": "value", "start_year": BIG}),
])
def test_huge_numbers_are_structured_errors(monkeypatch, ctx, name, args):
    monkeypatch.setattr(srv, "_ctx", ctx)
    out = json.loads(_call(name, args).content[0].text)
    assert out["ok"] is False and "투자 권유가 아닙니다" in out["disclaimer"]


@pytest.mark.parametrize("name", ["list_fields", "screen", "recommend"])
def test_internal_errors_keep_disclaimer_and_hide_paths(monkeypatch, tmp_path, name):
    from kquant.config import Paths
    from kquant.context import DataContext

    monkeypatch.setattr(srv, "_ctx", DataContext(Paths(data=tmp_path)))  # 데이터 없음
    args = {"strategies": ["value"], "include_disclosures": False} if name == "recommend" else {}
    out = json.loads(_call(name, args).content[0].text)
    assert out["ok"] is False and out["problem"]["code"] == "internal"
    assert "투자 권유가 아닙니다" in out["disclaimer"] and str(tmp_path) not in json.dumps(out, ensure_ascii=False)


def test_dart_http_error_does_not_leak_key(monkeypatch, tmp_path):
    import requests

    from kquant.config import Paths
    from kquant.ingest import dart_api

    monkeypatch.setenv("DART_API_KEY", "SECRET-KEY-XYZ")

    class S:
        def get(self, url, params, timeout):
            r = requests.Response()
            r.status_code, r.reason = 500, "err"
            r.url = requests.Request("GET", url, params=params).prepare().url
            return r

    with pytest.raises(RuntimeError) as ei:
        dart_api.update_corp_codes(Paths(data=tmp_path), session=S())
    assert "SECRET-KEY-XYZ" not in str(ei.value) and ei.value.__cause__ is None


def test_unserializable_result_becomes_structured_error():
    """도구 결과를 JSON으로 못 바꾸면 SDK 오류(고지 없음) 대신 고지 붙은 internal 오류."""
    import pandas as pd

    out = srv.safe(lambda: {"ok": True, "x": pd.NaT})()
    assert out["ok"] is False and out["problem"]["code"] == "internal" and "투자 권유가 아닙니다" in out["disclaimer"]
    assert "error" not in out


def test_check_hermes_default_targets_public_profile(monkeypatch):
    """2026-11-10 결정 D4: 공개 프로필 = 8643(8642는 운영 프로필). CLI 기본값과 예시 설정이 같은 포트를 가리킨다."""
    from kquant import cli

    monkeypatch.delenv("KQUANT_HERMES_URL", raising=False)
    seen = {}
    monkeypatch.setattr(cli, "cmd_check_hermes", lambda a: seen.setdefault("url", a.url) and 0)
    cli.main(["check-hermes"])
    assert seen["url"] == "http://127.0.0.1:8643"
    example = (Path(__file__).resolve().parents[2] / "hermes" / "config.example.yaml")
    if example.exists():
        assert "API_SERVER_PORT=8643" in example.read_text(encoding="utf-8")
