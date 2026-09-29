"""ADR-0215（f05）情境工具行为化 dispatch 测试。

覆盖 webgis_context_revalidate / webgis_context_bind_mission 的真实
registry.dispatch 路径：hotpath seam 打桩为确定性回执，验证
(1) 指名式参数原样传递；(2) 缺 session 的诚实拒绝；(3) 工具面
异常不外溢堆栈（typed reason 回执）。
"""

import pytest

from app.tools.registry import ToolRegistry
from app.tools.context_revalidation_tools import register_context_revalidation_tools


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_context_revalidation_tools(r)
    return r


@pytest.mark.asyncio
async def test_context_revalidate_dispatches_named_claims(registry, monkeypatch):
    seen = {}

    async def fake_request_revalidation(session_id, *, claim_ids=(), reaffirm_texts=()):
        seen["session_id"] = session_id
        seen["claim_ids"] = list(claim_ids)
        seen["reaffirm_texts"] = list(reaffirm_texts)
        return {"ok": True, "receipt": "restored", "claim_ids": list(claim_ids)}

    monkeypatch.setattr(
        "app.services.gis_context.hotpath.request_revalidation",
        fake_request_revalidation,
    )
    out = await registry.dispatch(
        "webgis_context_revalidate",
        {"claim_ids": ["clm-1", "clm-2"], "reaffirm_texts": ["AOI 不变"]},
        session_id="s-ctx-1",
    )
    assert out["ok"] is True and out["receipt"] == "restored"
    assert seen["session_id"] == "s-ctx-1"
    assert seen["claim_ids"] == ["clm-1", "clm-2"]
    assert seen["reaffirm_texts"] == ["AOI 不变"]


@pytest.mark.asyncio
async def test_context_revalidate_rejects_missing_session(registry):
    out = await registry.dispatch("webgis_context_revalidate", {"claim_ids": []})
    assert "error" in out


@pytest.mark.asyncio
async def test_context_revalidate_tool_error_is_typed_receipt(registry, monkeypatch):
    async def boom(session_id, *, claim_ids=(), reaffirm_texts=()):
        raise RuntimeError("engine down")

    monkeypatch.setattr(
        "app.services.gis_context.hotpath.request_revalidation", boom
    )
    out = await registry.dispatch(
        "webgis_context_revalidate", {"claim_ids": ["clm-x"]}, session_id="s-ctx-2"
    )
    # 失败不外溢堆栈：typed reason 回执（ok=False + reason code）。
    assert out == {"ok": False, "reason": "tool_error:RuntimeError"}


@pytest.mark.asyncio
async def test_context_bind_mission_dispatches_explicit_id(registry, monkeypatch):
    seen = {}

    async def fake_bind(session_id, mission_id):
        seen["session_id"] = session_id
        seen["mission_id"] = mission_id
        return True, "bound"

    monkeypatch.setattr(
        "app.services.gis_context.hotpath.bind_session_mission", fake_bind
    )
    out = await registry.dispatch(
        "webgis_context_bind_mission",
        {"mission_id": "msn-abc123"},
        session_id="s-ctx-3",
    )
    assert out == {"ok": True, "reason": "bound"}
    assert seen == {"session_id": "s-ctx-3", "mission_id": "msn-abc123"}


@pytest.mark.asyncio
async def test_context_bind_mission_rejects_bad_args(registry, monkeypatch):
    async def unreachable(session_id, mission_id):  # pragma: no cover
        raise AssertionError("must not reach engine on invalid args")

    monkeypatch.setattr(
        "app.services.gis_context.hotpath.bind_session_mission", unreachable
    )
    # mission_id 缺省 / 空串：args_model 的 min_length=4 先拦 —— dispatch
    # 面转 typed VALIDATION_ERROR 回执（success=False + correction_hint），
    # 绝不带空 mission_id 打到引擎。
    out = await registry.dispatch(
        "webgis_context_bind_mission", {}, session_id="s-ctx-4"
    )
    assert out.get("success") is False
    assert out.get("code") == "VALIDATION_ERROR"
    out = await registry.dispatch(
        "webgis_context_bind_mission", {"mission_id": ""}, session_id="s-ctx-4"
    )
    assert out.get("success") is False
    assert out.get("code") == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_context_bind_mission_tool_error_is_typed_receipt(registry, monkeypatch):
    async def boom(session_id, mission_id):
        raise KeyError("mission store offline")

    monkeypatch.setattr("app.services.gis_context.hotpath.bind_session_mission", boom)
    out = await registry.dispatch(
        "webgis_context_bind_mission",
        {"mission_id": "msn-zzz"},
        session_id="s-ctx-5",
    )
    assert out == {"ok": False, "reason": "tool_error:KeyError"}
