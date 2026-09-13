"""接线集成测试：ToolDispatchService × governor、ChatContextAssembler × 账本。

验证真实派发链路（含 dedup/错误折叠/wave gate）在 governor 在场时的行为
契约：正常调用照常成功、拒绝走诚实失败路径（dedup 释放）、kill-switch
完全直通、context token 事实入账。
"""
from __future__ import annotations

import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.governor.config import (
    GovernorConfig,
    load_manifest,
    reset_governor_config_for_tests,
)
from app.services.governor.contract import Dimension
from app.services.governor.governor import (
    HarnessResourceGovernor,
    reset_governor_for_tests,
)
from app.services.tool_dispatch_service import ToolDispatchService

_MANIFEST = pathlib.Path(__file__).resolve().parents[2] / "config" / "governor_budgets.json"


def _tc(name: str, args_json: str, call_id: str) -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": args_json}}


@pytest.fixture()
def clean_governor_env():
    import os
    saved = {k: v for k, v in os.environ.items() if k.startswith("GOVERNOR_")}
    for k in list(saved):
        os.environ.pop(k)
    reset_governor_config_for_tests()
    reset_governor_for_tests()
    yield
    for k in list(os.environ):
        if k.startswith("GOVERNOR_"):
            os.environ.pop(k)
    os.environ.update(saved)
    reset_governor_config_for_tests()
    reset_governor_for_tests()


def _service_with_registry(cost: str = "light") -> tuple:
    registry = MagicMock()
    registry.dispatch = AsyncMock(return_value={"success": True, "value": 1})
    registry.metadata.return_value = {"cost": cost}
    return ToolDispatchService(registry=registry), registry


@pytest.mark.asyncio
async def test_dispatch_with_governor_success_path(clean_governor_env):
    service, registry = _service_with_registry()
    result = await service.dispatch(
        tc=_tc("get_map_meta", "{}", "call-1"),
        session_id="sess-a",
        executed_tools=set(),
    )
    assert result.status == "ok"
    registry.dispatch.assert_awaited_once()
    from app.services.governor.governor import get_governor
    gov = get_governor()
    snap = gov.snapshot("sess-a")
    # 完成后：无在飞、有累计 wall_time、无泄漏
    assert snap["budgets"]["session"]["live"]["memory_bytes"] == 0.0
    assert snap["budgets"]["session"]["cumulative"]["wall_time_s"] > 0


@pytest.mark.asyncio
async def test_dispatch_rejection_is_honest_failure(clean_governor_env):
    # 非 provisional 的小内存 session 预算 + heavy 工具先验 → 硬拒
    budgets = load_manifest(_MANIFEST)
    budgets["session"] = budgets["session"].model_copy(
        update={"provisional": False,
                "limits": {Dimension.MEMORY_BYTES: 1e8}})
    config = GovernorConfig()
    gov = HarnessResourceGovernor(config, budgets=budgets)
    reset_governor_for_tests(gov)

    service, registry = _service_with_registry(cost="heavy")
    executed: set = set()
    result = await service.dispatch(
        tc=_tc("heavy_raster_work", "{}", "call-rej"),
        session_id="sess-b",
        executed_tools=executed,
    )
    # 诚实失败：未执行、错误族状态、dedup 槽位已释放（可重试）
    registry.dispatch.assert_not_awaited()
    assert result.status == "error"
    assert "resource governor" in (result.error_msg or "").lower()
    assert executed == set()  # dedup 占位已释放
    raw = result.raw_result or {}
    assert raw.get("code") == "RESOURCE_GOVERNOR_REJECT"

    # 同参重试仍会再次被拒（dedup 已释放 —— 不会被「在飞/已成功」谎言拦截）
    result2 = await service.dispatch(
        tc=_tc("heavy_raster_work", "{}", "call-rej-2"),
        session_id="sess-b",
        executed_tools=set(),
    )
    assert result2.status == "error"


@pytest.mark.asyncio
async def test_dispatch_kill_switch_passthrough(clean_governor_env):
    import os
    os.environ["GOVERNOR_TOOL_SURFACE"] = "0"
    service, registry = _service_with_registry()
    result = await service.dispatch(
        tc=_tc("heavy_raster_work", "{}", "call-ks"),
        session_id="sess-c",
        executed_tools=set(),
    )
    assert result.status == "ok"
    registry.dispatch.assert_awaited_once()
    from app.services.governor.governor import get_governor
    gov = get_governor()
    assert gov.snapshot("sess-c")["budgets"] == {}


@pytest.mark.asyncio
async def test_context_assembler_records_tokens(clean_governor_env):
    from app.services.chat.context_assembler import ChatContextAssembler

    assembler = ChatContextAssembler()
    messages = [
        {"role": "system", "content": "你是 WebGIS 助手。" * 20},
        {"role": "user", "content": "画一张中国人口密度图" },
        {"role": "assistant", "content": "好的，正在处理。" * 30},
        {"role": "user", "content": "继续"},
    ]
    res = await assembler.assemble("sess-ctx-1", list(messages))
    assert res is not None
    from app.services.governor.governor import get_governor
    gov = get_governor()
    snap = gov.snapshot("sess-ctx-1")["budgets"]
    tokens = (snap.get("session", {}).get("cumulative", {})
              .get("context_tokens", 0))
    assert tokens > 0  # R12 只读记账生效
