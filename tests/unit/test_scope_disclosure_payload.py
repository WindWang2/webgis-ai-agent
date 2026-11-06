"""Scope uncertainty is mandatory evidence, even under payload pressure."""
import json
import uuid

import pytest

from app.services.llm_result_formatter import slim_tool_result


@pytest.mark.parametrize("word", ["long diagnostic ", "这是很长的地图诊断🗺️"])
@pytest.mark.parametrize("with_summary", [False, True])
def test_scope_disclosure_survives_both_formatter_paths(word, with_summary):
    result = {f"noise{i}": word * 1000 for i in range(40)}
    result.update({
        "scope_geography": {"verdict": "unverified", "reason": "scope_unverified", "evidence": "none"},
        "warnings": ["scope_unverified: 请求范围尚未证实。"],
    })
    if with_summary:
        result["summary"] = word * 1000 + "scope_unverified"
        result["guidance"] = [word * 1000] * 40
    payload = slim_tool_result(result, json.dumps(result, ensure_ascii=False), None)
    assert len(payload.encode("utf-8")) <= 2500
    parsed = json.loads(payload)
    assert parsed["scope_geography"] == result["scope_geography"]
    assert any("scope_unverified" in warning for warning in parsed["warnings"])
    assert any("not verified" in warning for warning in parsed["warnings"])


@pytest.mark.parametrize("word", ["a", "中", "🗺️"])
def test_success_payload_budget_is_utf8_bytes(word):
    result = {"summary": word * 2500}
    payload = slim_tool_result(result, json.dumps(result, ensure_ascii=False), None)
    assert len(payload.encode("utf-8")) <= 2500


def test_formatter_does_not_invent_scope_uncertainty():
    for scope in (None, {"verdict": "matched", "reason": "", "evidence": "name"}):
        result = {"summary": "Small result"}
        if scope:
            result["scope_geography"] = scope
        payload = slim_tool_result(result, json.dumps(result), None)
        assert "scope_unverified" not in payload


@pytest.mark.parametrize("with_summary", [False, True])
def test_scope_reservation_does_not_replace_other_small_safety_warnings(with_summary):
    other_warning = "CRS mismatch: distance calculations are invalid"
    result = {
        "scope_geography": {"verdict": "unverified", "reason": "scope_unverified"},
        "warnings": ["scope_unverified", other_warning],
    }
    if with_summary:
        result["summary"] = "Small result"
    parsed = json.loads(slim_tool_result(result, json.dumps(result), None))
    assert other_warning in parsed["warnings"]
    assert any("not verified" in warning for warning in parsed["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["long diagnostic ", "这是很长的地图诊断🗺️"])
async def test_pi_content_and_sse_use_the_real_dispatch_disclosure(monkeypatch, word):
    from app import agent_pi_bridge as bridge
    from app.services.chat.pi_event_mapper import map_event_to_sse
    from app.services.session_data import session_data_manager
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    scope = {"verdict": "unverified", "reason": "scope_unverified", "evidence": "none"}

    def scope_result(**_):
        return {"summary": word * 1000, "scope_geography": scope,
                "warnings": ["scope_unverified: 请求范围尚未证实。"],
                "guidance": [word * 100] * 40}

    registry.register("scope_budget_probe", "Scope evidence", scope_result,
                      parameters={"type": "object", "properties": {}})
    # Isolate bridge state, not its dispatch or presentation implementation.
    for name, value in (("_tool_registry", registry), ("_dispatch_service", None),
                        ("_dispatch_service_registry", None), ("_session_executed_sets", {}),
                        ("_dispatch_result_cache", {}), ("_session_plan_sse_cache", {})):
        monkeypatch.setattr(bridge, name, value)
    sid = f"scope-budget-{uuid.uuid4().hex}"
    try:
        response = await bridge.dispatch_tool(bridge.PiToolRequest(
            toolCallId="scope-budget-call", name="webgis_execute",
            arguments={"toolName": "scope_budget_probe", "arguments": {}}, sessionId=sid,
        ))
        assert not response.isError
        # Peek without consuming: the real SSE reader pops this entry once.
        cached = bridge._dispatch_result_cache.get((sid, "scope-budget-call"))
        assert cached is not None and cached.status == "ok"
        content = "".join(block.get("text", "") for block in response.content)
        assert cached.llm_payload in content
        assert len(cached.llm_payload.encode("utf-8")) <= 2500
        assert json.loads(cached.llm_payload)["scope_geography"] == scope
        assert "not verified" in content
        assert response.details["scope_geography"] == scope
        sse = map_event_to_sse({
            "type": "tool_execution_end", "toolCallId": "scope-budget-call",
            "toolName": "scope_budget_probe", "result": {}, "isError": False,
        }, sid, cache_lookup=lambda call_id: bridge.get_cached_dispatch_result(call_id, sid))
        payloads = [json.loads(line[6:]) for line in sse.splitlines() if line.startswith("data: ")]
        assert any(p.get("result", {}).get("scope_geography") == scope for p in payloads)
        assert any("scope_unverified" in str(p.get("result", {}).get("warnings")) for p in payloads)
    finally:
        await session_data_manager.clear_session(sid)
