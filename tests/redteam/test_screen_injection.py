"""레드팀: 필터 DSL 주입·퍼징 (spec §11 도구 보안).

공격 가정: LLM(또는 프롬프트 주입된 사용자 입력)이 임의의 JSON을 screen에 넘긴다.
기대: 예외 없이 구조화된 오류를 돌려주고, 카탈로그 밖 컬럼·코드 실행·경로 접근이 불가능하다.
"""

import json
import random

from kquant.catalog.screen import screen

KNOWN_CODES = {
    "invalid_query", "unknown_key", "unknown_field", "period_required", "unsupported_period", "bad_operator",
    "bad_value", "bad_market", "bad_sort", "bad_limit", "bad_as_of", "too_many_conditions",
}
PAYLOADS = [
    "revenue; DROP TABLE fin;--", "' OR 1=1 --", "__import__('os').system('id')", "../../etc/passwd",
    "symbol", "Marcap", "name", "price_date", "per.ttm", "market_cap.latest", "${jndi:ldap://x}",
    "{{7*7}}", "eval('1')", "\x00", "a" * 10_000, "매출액_ttm", "@market_cap > 0", "lambda: 1",
]


def _check(out):
    json.dumps(out, ensure_ascii=False)  # 직렬화 가능
    assert "disclaimer" in out
    if not out["ok"]:
        assert out["problem"]["code"] in KNOWN_CODES, out
        assert len(json.dumps(out, ensure_ascii=False)) < 3000  # 공격 문자열을 그대로 길게 되돌려주지 않음


def test_payloads_in_every_slot(ctx):
    for p in PAYLOADS:
        for q in [
            {"where": [{"field": p, "op": ">", "value": 1}]},
            {"where": [{"field": "pbr", "op": p, "value": 1}]},
            {"where": [{"field": "pbr", "op": ">", "value": p}]},
            {"where": [{"field": "revenue", "period": p, "op": ">", "value": 1}]},
            {"sort": {"field": p}}, {"fields": [p]}, {"market": [p]}, {"as_of": p}, {p: 1},
        ]:
            out = screen(q, ctx)
            _check(out)
            assert out["ok"] is False, (p, q)


def test_internal_columns_not_reachable(ctx):
    # 내부 컬럼명(symbol, Marcap, per.ttm 등)은 카탈로그 id가 아니므로 필드로 쓸 수 없다
    for name in ["symbol", "Marcap", "per.ttm", "fin_period_end", "currency"]:
        assert screen({"sort": {"field": name}}, ctx)["problem"]["code"] == "unknown_field"


def _rand_value(rng, depth=0):
    choices = [None, True, 1, -1, 1e308, float("nan"), "", "x", "1조", [], {}, PAYLOADS[rng.randrange(len(PAYLOADS))]]
    v = rng.choice(choices)
    if depth < 2 and rng.random() < 0.3:
        v = [_rand_value(rng, depth + 1) for _ in range(rng.randrange(3))]
    if depth < 2 and rng.random() < 0.2:
        v = {rng.choice(["field", "op", "value", "period", "zzz"]): _rand_value(rng, depth + 1) for _ in range(2)}
    return v


def test_random_structures_never_raise(ctx):
    rng = random.Random(20261107)
    keys = ["as_of", "where", "market", "sort", "fields", "limit", "extra"]
    for _ in range(500):
        q = {rng.choice(keys): _rand_value(rng) for _ in range(rng.randrange(4))}
        if rng.random() < 0.5:
            q["where"] = [{"field": rng.choice(["pbr", "revenue", "per", "x"]), "op": rng.choice([">", "between", "~"]),
                           "value": _rand_value(rng)} for _ in range(rng.randrange(3))]
        _check(screen(q, ctx))


def test_condition_and_limit_caps(ctx):
    many = [{"field": "pbr", "op": ">", "value": 0}] * 21
    assert screen({"where": many}, ctx)["problem"]["code"] == "too_many_conditions"
    assert screen({"limit": 101}, ctx)["problem"]["code"] == "bad_limit"
    assert screen({"limit": True}, ctx)["problem"]["code"] == "bad_limit"
