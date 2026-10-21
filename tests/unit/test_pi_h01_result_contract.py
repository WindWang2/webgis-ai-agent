"""H01（K1/K7）：Pi 结果契约的证据投影与 typed 错误码。

钉住（ADR-0224 结果面的生产缺陷修复）：
- K1：``evidence.capability_bind`` / ``evidence.action_ir`` 必须投影**真实
  生产者键**（``capability_bind.py`` 的 evidence dict 与
  ``gis_action/service.py`` 的 action IR evidence dict）——master 现状抽取键
  ``decision/code/capability_id`` 与生产者键错配，capability id 永远丢失、
  action_ir 证据恒空 → 整键省略。
- K7：registry 权威错误码（UNKNOWN_TOOL / TIER3_CONFIRMATION_REQUIRED /
  CREDENTIALS_REQUIRED / PERMISSION_DENIED / PROVIDER_UNAVAILABLE /
  SCHEMA_VALIDATION_REJECTED）在 envelope error.class 有确定性映射，不依赖
  异常文本匹配；桥内 not-found / tier-3 拒绝带稳定机器码（先例：
  native_surface_reject 的 details.error）。
- 全部有界：无 payload 泄漏、reason_codes/finding_codes 截断。
"""
import pytest
from httpx import ASGITransport, AsyncClient

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import set_tool_registry
from app.api.routes.pi_tools import get_bridge_secret
from app.main import app
from app.services.chat.pi_result_abi import build_result_envelope
from app.services.chat.pi_turn_context import issue_turn_token
from app.tools.registry import ToolRegistry


class _Result:
    def __init__(self, **kw):
        self.status = kw.get("status", "ok")
        self.llm_payload = kw.get("llm_payload", "done")
        self.slim_event = {}
        self.geojson_ref = kw.get("geojson_ref")
        self.raw_result = kw.get("raw_result", {})
        self.error_msg = kw.get("error_msg")
        self.map_actions = kw.get("map_actions", [])
        self.background_job_ids = kw.get("background_job_ids", [])
        self.capability_evidence = kw.get("capability_evidence")
        self.action_evidence = kw.get("action_evidence")


@pytest.fixture(autouse=True)
def abi_on(monkeypatch):
    monkeypatch.delenv("PI_RESULT_ABI", raising=False)
    monkeypatch.delenv("PI_TOOL_ABI", raising=False)
    monkeypatch.delenv("PI_SURFACE_SCOPE_ENFORCE", raising=False)


# ── K1：evidence 投影真实生产者键 ────────────────────────────────────


def test_capability_bind_evidence_projects_real_producer_keys():
    """capability_bind.py 拒绝路径 evidence 的真实键（master@0415ad8d）；
    alternatives 是 {"kind","id",...} 形状（全部三路径一致）。"""
    result = _Result(
        status="error",
        capability_evidence={
            "tool": "poi_query",
            "action": "refused",
            "capabilities": ["poi_query"],
            "capability": "poi_query",
            "status": "ineligible",
            "reason": "geometry mismatch: expected point",
            "code": "CAPABILITY_INELIGIBLE",
            "reason_codes": ["geometry_mismatch", "min_features"],
            "alternatives": [
                {"kind": "capability", "id": "admin_aggregate",
                 "score": 0.7, "status": "eligible"},
                {"kind": "capability", "id": "grid_binning",
                 "score": 0.6, "status": "eligible"},
                {"kind": "capability", "id": "third_one",
                 "score": 0.5, "status": "eligible"},
            ],
        },
    )
    env = build_result_envelope(result)
    ev = env.get("evidence", {}).get("capability_bind")
    assert ev, "capability_bind evidence must be present"
    assert ev.get("action") == "refused"
    assert ev.get("capability") == "poi_query", "capability id 不得丢失"
    assert ev.get("status") == "ineligible"
    assert ev.get("code") == "CAPABILITY_INELIGIBLE"
    assert "geometry_mismatch" in ev.get("reason_codes", [])
    assert ev.get("alternatives") == ["admin_aggregate", "grid_binning"], (
        "替代能力必须从生产者 {kind,id} 形状投影（≤2 条）"
    )


def test_capability_bind_allowed_evidence_projects_real_keys():
    """capability_bind.py 允许路径 evidence（无 reason_codes 键）。"""
    result = _Result(
        status="ok",
        capability_evidence={
            "tool": "heatmap_data",
            "action": "allowed",
            "capabilities": ["density"],
            "code": "",
            "reason": "",
            "alternatives": [],
            "rank_capability": "density",
            "rank": 1,
            "score": 0.87,
        },
    )
    env = build_result_envelope(result)
    ev = env.get("evidence", {}).get("capability_bind")
    assert ev and ev.get("action") == "allowed"
    assert ev.get("capability") == "density"


def test_action_ir_evidence_projects_real_producer_keys():
    """gis_action/service.py evidence 的真实键 —— master 现状恒空（键省略）。"""
    result = _Result(
        status="ok",
        action_evidence={
            "policy_version": "gis_action_ir_bind.v1",
            "plan_id": "plan-abc123",
            "compile_id": "compile-xyz",
            "kind": "acquire",
            "status": "compiled",
            "finding_codes": [],
            "args_projected": True,
        },
    )
    env = build_result_envelope(result)
    ev = env.get("evidence", {}).get("action_ir")
    assert ev, "action_ir evidence must be present (master 现状恒空)"
    assert ev.get("plan_id") == "plan-abc123", "plan_id 不得丢失"
    assert ev.get("kind") == "acquire"
    assert ev.get("status") == "compiled"


def test_action_ir_blocked_findings_projected_and_bounded():
    result = _Result(
        status="error",
        action_evidence={
            "policy_version": "gis_action_ir_bind.v1",
            "plan_id": "p" * 80,
            "compile_id": "c" * 80,
            "kind": "analyze",
            "status": "blocked",
            "finding_codes": [f"F{i}" for i in range(20)],
            "args_projected": False,
        },
    )
    env = build_result_envelope(result)
    ev = env["evidence"]["action_ir"]
    assert len(ev.get("finding_codes", [])) <= 4, "finding_codes 必须有界"
    assert len(ev.get("plan_id", "")) <= 64


def test_capability_reason_codes_bounded():
    result = _Result(
        status="error",
        capability_evidence={
            "tool": "t",
            "action": "refused",
            "capability": "c",
            "status": "ineligible",
            "code": "CAPABILITY_INELIGIBLE",
            "reason_codes": [f"check_{i}" for i in range(30)],
            "alternatives": [
                {"kind": "capability", "id": f"a{i}"} for i in range(10)
            ],
        },
    )
    env = build_result_envelope(result)
    ev = env["evidence"]["capability_bind"]
    assert len(ev.get("reason_codes", [])) <= 4
    assert len(ev.get("alternatives", [])) <= 2


# ── K7：typed 错误码确定性映射 ────────────────────────────────────────


@pytest.mark.parametrize("code,expected_class", [
    ("PROVIDER_UNAVAILABLE", "provider_unavailable"),
    ("UNKNOWN_TOOL", "not_found"),
    ("TOOL_NOT_EXECUTABLE", "not_found"),
    ("TIER3_CONFIRMATION_REQUIRED", "authorization"),
    ("CREDENTIALS_REQUIRED", "authorization"),
    ("PERMISSION_DENIED", "authorization"),
    ("SCHEMA_VALIDATION_REJECTED", "schema_validation"),
])
def test_registry_codes_map_deterministically(code, expected_class):
    """registry 权威码 → 封闭 error.class；不再依赖异常文本匹配。"""
    env = build_result_envelope(_Result(
        status="error", raw_result={"code": code}, error_msg="",
    ))
    assert env["error"]["class"] == expected_class
    assert env["error"]["code"] == code


def test_text_fallback_still_works_for_unstructured_errors():
    env = build_result_envelope(_Result(status="error", error_msg="upstream timed out"))
    assert env["error"]["class"] == "timeout"


# ── K7：桥内 not-found / tier-3 拒绝机器码（HTTP 边界） ───────────────


@pytest.fixture
def h01_registry():
    registry = ToolRegistry()
    registry.register(
        "h01_tier3_tool",
        "tier-3 destructive tool (H01 test)",
        lambda **kw: {"success": True},
        parameters={"type": "object", "properties": {}},
        tier=3,
    )
    old_registry = bridge_mod._tool_registry
    set_tool_registry(registry)
    assert registry.has("h01_tier3_tool")
    yield registry
    bridge_mod._tool_registry = old_registry


@pytest.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(autouse=True)
def clean_turn_state():
    from app.services.chat.pi_turn_context import pi_turn_registry
    from app.services.chat.pi_surface_receipt import surface_receipt_store

    pi_turn_registry._local_context = None
    surface_receipt_store.clear()
    yield
    pi_turn_registry._local_context = None
    surface_receipt_store.clear()


async def _register_turn(session_id: str, turn_id: str):
    from app.agent_pi_bridge import register_active_pi_turn

    await register_active_pi_turn(session_id, turn_id)


def _proxy_body(tool_call_id, inner_tool, token):
    return {
        "toolCallId": tool_call_id,
        "name": "webgis_execute",
        "arguments": {"toolName": inner_tool, "arguments": {}},
        "turnToken": token,
    }


@pytest.mark.asyncio
async def test_unknown_tool_rejection_carries_machine_code(h01_registry, async_client):
    session_id, turn_id = "sess-h01-unknown", "turn-h01-unknown"
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)
    await _register_turn(session_id, turn_id)

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": get_bridge_secret()},
        json=_proxy_body("call-h01-1", "totally_unknown_h01_tool", token),
    )
    assert resp.status_code == 200  # typed reject，不是 HTTP 错误
    data = resp.json()
    assert data["isError"] is True
    details = data.get("details") or {}
    assert details.get("error") == "UNKNOWN_TOOL", (
        "桥内 not-found 拒绝必须带稳定机器码（master 现状纯文本）"
    )
    assert details.get("tool") == "totally_unknown_h01_tool"


@pytest.mark.asyncio
async def test_tier3_rejection_carries_machine_code(h01_registry, async_client):
    session_id, turn_id = "sess-h01-tier3", "turn-h01-tier3"
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)
    await _register_turn(session_id, turn_id)

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": get_bridge_secret()},
        json=_proxy_body("call-h01-2", "h01_tier3_tool", token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["isError"] is True
    details = data.get("details") or {}
    assert details.get("error") == "TIER3_CONFIRMATION_REQUIRED", (
        "桥内 tier-3 拒绝必须带稳定机器码（与 registry 同码）"
    )
    assert details.get("tool") == "h01_tier3_tool"
    # 工具未被真实执行
    assert "success" not in str(data.get("content"))
