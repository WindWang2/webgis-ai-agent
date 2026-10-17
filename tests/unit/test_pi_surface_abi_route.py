"""Pi surface ABI 回调边界测试（Goal 04, ADR-0224）。

经 /pi-tools/execute 全 HTTP 边界（bridge secret + turn token + live-turn）
钉住 fingerprint echo 与 opt-in scope 校验：
- echo 匹配 → 正常 dispatch（details 携带 resultAbi envelope）；
- echo 不匹配 → typed surface_stale 拒绝（模型可读 guidance，非 HTTP 错误）；
- receipt 缺席 → echo 不校验（master 兼容）；
- PI_SURFACE_SCOPE_ENFORCE=1 → 面外 bare-name typed 拒绝，proxy 通道不受限；
- scope 校验不越权：echo 字段不是权限（无 echo 的合法调用照常执行）。
"""
import pytest
from httpx import ASGITransport, AsyncClient

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import set_tool_registry
from app.api.routes.pi_tools import get_bridge_secret
from app.main import app
from app.services.chat.pi_surface_receipt import (
    record_turn_surface,
    surface_receipt_store,
)
from app.services.chat.pi_tool_spec import compile_tool_spec
from app.services.chat.pi_turn_context import issue_turn_token
from app.tools.registry import ToolRegistry


@pytest.fixture
def test_registry():
    registry = ToolRegistry()
    registry.register(
        "pi_abi_echo",
        "Echo test tool",
        lambda msg, session_id=None: {
            "success": True, "message": f"echo:{msg}", "session": session_id,
        },
        parameters={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    )
    old_registry = bridge_mod._tool_registry
    set_tool_registry(registry)
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

    pi_turn_registry._local_context = None
    surface_receipt_store.clear()
    yield
    pi_turn_registry._local_context = None
    surface_receipt_store.clear()


@pytest.fixture(autouse=True)
def abi_flags(monkeypatch):
    monkeypatch.delenv("PI_TOOL_ABI", raising=False)
    monkeypatch.delenv("PI_RESULT_ABI", raising=False)
    monkeypatch.delenv("PI_SURFACE_SCOPE_ENFORCE", raising=False)


async def _register_turn(session_id: str, turn_id: str):
    from app.agent_pi_bridge import register_active_pi_turn

    await register_active_pi_turn(session_id, turn_id)


def _mint_receipt(registry, session_id: str, turn_id: str, tool: str = "pi_abi_echo"):
    spec = compile_tool_spec(registry, tool)
    assert spec is not None
    record_turn_surface(
        [spec], session_id=session_id, turn_id=turn_id,
        candidates=[tool], selected=[tool], drops=[],
    )
    return spec.fingerprint()


def _body(tool_call_id, name, arguments, token, **extra):
    return {
        "toolCallId": tool_call_id,
        "name": name,
        "arguments": arguments,
        "turnToken": token,
        **extra,
    }


@pytest.mark.asyncio
async def test_valid_echo_dispatches_with_result_abi(test_registry, async_client):
    session_id, turn_id = "sess-abi-ok", "turn-abi-ok"
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)
    await _register_turn(session_id, turn_id)
    fp = _mint_receipt(test_registry, session_id, turn_id)

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": get_bridge_secret()},
        json=_body(
            "call-abi-1", "pi_abi_echo", {"msg": "hi"}, token,
            toolFingerprint=fp,
        ),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["isError"] is False
    assert "echo:hi" in data["content"][0]["text"]
    abi = data["details"].get("resultAbi")
    assert abi and abi["abi"] == "pi_result.v1" and abi["status"] == "ok"


@pytest.mark.asyncio
async def test_stale_tool_fingerprint_echo_is_typed_rejected(test_registry, async_client):
    session_id, turn_id = "sess-abi-stale", "turn-abi-stale"
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)
    await _register_turn(session_id, turn_id)
    _mint_receipt(test_registry, session_id, turn_id)  # 真相指纹在册

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": get_bridge_secret()},
        json=_body(
            "call-abi-2", "pi_abi_echo", {"msg": "hi"}, token,
            toolFingerprint="deadbeefdeadbeef",
        ),
    )
    assert resp.status_code == 200  # typed reject，不是 HTTP 错误
    data = resp.json()
    assert data["isError"] is True
    assert data["details"]["code"] == "SURFACE_FINGERPRINT_MISMATCH"
    assert "webgis_execute" in data["content"][0]["text"]
    # 工具未被真实执行（stale 面上无副作用）
    assert "echo:" not in data["content"][0]["text"]


@pytest.mark.asyncio
async def test_stale_surface_fingerprint_echo_is_typed_rejected(
        test_registry, async_client):
    session_id, turn_id = "sess-abi-sfp", "turn-abi-sfp"
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)
    await _register_turn(session_id, turn_id)
    _mint_receipt(test_registry, session_id, turn_id)

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": get_bridge_secret()},
        json=_body(
            "call-abi-3", "pi_abi_echo", {"msg": "hi"}, token,
            surfaceFingerprint="0000000000000000",
        ),
    )
    data = resp.json()
    assert data["isError"] is True
    assert data["details"]["code"] == "SURFACE_FINGERPRINT_MISMATCH"


@pytest.mark.asyncio
async def test_no_receipt_echo_is_ignored_for_compat(test_registry, async_client):
    """receipt 缺席（动态面关 / 旧 turn）：echo 不校验 —— master 行为。"""
    session_id, turn_id = "sess-abi-norec", "turn-abi-norec"
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)
    await _register_turn(session_id, turn_id)

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": get_bridge_secret()},
        json=_body(
            "call-abi-4", "pi_abi_echo", {"msg": "hi"}, token,
            toolFingerprint="anything-at-all",
        ),
    )
    data = resp.json()
    assert data["isError"] is False
    assert "echo:hi" in data["content"][0]["text"]


@pytest.mark.asyncio
async def test_no_echo_dispatches_normally(test_registry, async_client):
    """echo 缺席（旧扩展）→ 照常执行 —— echo 不是权限。"""
    session_id, turn_id = "sess-abi-noecho", "turn-abi-noecho"
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)
    await _register_turn(session_id, turn_id)
    _mint_receipt(test_registry, session_id, turn_id)

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": get_bridge_secret()},
        json=_body("call-abi-5", "pi_abi_echo", {"msg": "hi"}, token),
    )
    data = resp.json()
    assert data["isError"] is False


@pytest.mark.asyncio
async def test_scope_enforcement_rejects_out_of_surface_bare_name(
        test_registry, async_client, monkeypatch):
    monkeypatch.setenv("PI_SURFACE_SCOPE_ENFORCE", "1")
    session_id, turn_id = "sess-abi-scope", "turn-abi-scope"
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)
    await _register_turn(session_id, turn_id)
    # receipt 只含 pi_abi_echo —— 注册面上的其他名字裸调应被拒
    _mint_receipt(test_registry, session_id, turn_id)
    # 注册一个面外工具（在 registry 但不在 receipt.selected）
    test_registry.register(
        "pi_abi_other", "other", lambda: {"success": True},
        parameters={"type": "object", "properties": {}},
    )

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": get_bridge_secret()},
        json=_body("call-abi-6", "pi_abi_other", {}, token),
    )
    data = resp.json()
    assert data["isError"] is True
    assert data["details"]["code"] == "SURFACE_SCOPE_VIOLATION"

    # proxy 通道不受 scope 校验（长尾 fallback 语义不变）
    resp2 = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": get_bridge_secret()},
        json=_body(
            "call-abi-7", "webgis_execute",
            {"toolName": "pi_abi_other", "arguments": {}}, token,
        ),
    )
    data2 = resp2.json()
    assert data2["isError"] is False, data2


@pytest.mark.asyncio
async def test_scope_enforcement_off_by_default(test_registry, async_client):
    session_id, turn_id = "sess-abi-scopeoff", "turn-abi-scopeoff"
    token = issue_turn_token(get_bridge_secret(), session_id, turn_id)
    await _register_turn(session_id, turn_id)
    _mint_receipt(test_registry, session_id, turn_id)
    test_registry.register(
        "pi_abi_other2", "other2", lambda: {"success": True},
        parameters={"type": "object", "properties": {}},
    )

    resp = await async_client.post(
        "/pi-tools/execute",
        headers={"X-Pi-Bridge-Secret": get_bridge_secret()},
        json=_body("call-abi-8", "pi_abi_other2", {}, token),
    )
    data = resp.json()
    assert data["isError"] is False
