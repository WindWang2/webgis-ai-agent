"""必过旅程 4 —— Pi 经生产工具链完成发布准备（真实 registry dispatch）。

与 Pi RPC（/pi-tools/execute → dispatch_tool → registry.dispatch）同一执行链：
本测试直接驱动 ``registry.dispatch`` + ``bind_runtime_context``（与
test_design_spec_tools 同纪律），验证：
- publication_inspect：可授权性检查（逐层 projectable/阻断/字段候选）；
- publication_prepare：发布计划建议（license 阻断解释）；
- publication_draft：私有草稿创建（Pi 唯一可落库动作）；
- publication_validate：草稿可发布性 + 同意边界声明；
- 红线：工具族不能发布/签发链接（publish 必须作者 confirm）。

session 归属 fail-closed：点名他人会话 → session_forbidden。
"""
from __future__ import annotations

import pytest

from tests.unit.publication.conftest import (
    DISTRICTS_FC,
    SCHOOLS_FC,
    SCHOOL_MAPSPEC,
    SESSION_A,
    SESSION_B,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def full_registry():
    """生产同款 registry（init_tools 全量装载 —— 含 _TOOL_MODULES 注册的
    publication 工具族）。"""
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    init_tools(registry)
    return registry


@pytest.fixture
def seeded_world(monkeypatch):
    from tests.unit.publication.conftest import _install_session_world

    _install_session_world(monkeypatch, spec=SCHOOL_MAPSPEC, refs={
        "ref:schools-1": SCHOOLS_FC,
        "ref:districts-1": DISTRICTS_FC,
    })


async def _dispatch(registry, name, arguments, session_id):
    from app.lib.runtime.context import bind_runtime_context

    with bind_runtime_context(session_id=session_id):
        return await registry.dispatch(name, arguments, session_id)


async def test_journey_inspect_and_prepare(full_registry, seeded_world):
    """旅程 4a：inspect → prepare（计划 + license 阻断解释）。"""
    inspect = await _dispatch(full_registry, "publication_inspect", {}, SESSION_A)
    assert inspect["status"] == "inspected"
    assert inspect["publishable"] is True
    layers = {lp["layer_id"]: lp for lp in inspect["layers"]}
    assert layers["lyr-schools"]["projectable"] is True
    assert layers["lyr-schools"]["feature_count"] == 12
    assert "school_name" in layers["lyr-schools"]["candidate_fields"]

    prepare = await _dispatch(full_registry, "publication_prepare", {
        "selected_layer_ids": ["lyr-schools", "lyr-districts"],
        "field_whitelists": {
            "lyr-schools": ["school_name", "level", "students"],
            "lyr-districts": ["district_name", "population"],
        },
        "title": "区县学校分布专题图",
        "purpose": "面向读者的只读展示",
        "attribution": "合成区县数据（simulated）",
        # 故意不给 license → prepare 必须解释该阻断
    }, SESSION_A)
    assert prepare["status"] == "planned"
    reasons = {b["reason"] for b in prepare["publish_blockers"]}
    assert "license_unknown" in reasons
    assert prepare["consent_note"] if "consent_note" in prepare else True
    assert "作者" in prepare["next_step"]

    # 补上 license → 阻断消失
    prepare2 = await _dispatch(full_registry, "publication_prepare", {
        "selected_layer_ids": ["lyr-schools", "lyr-districts"],
        "field_whitelists": {
            "lyr-schools": ["school_name", "level", "students"],
            "lyr-districts": ["district_name", "population"],
        },
        "title": "区县学校分布专题图",
        "license": "CC0-SIMULATED",
    }, SESSION_A)
    reasons2 = {b["reason"] for b in prepare2["publish_blockers"]}
    assert "license_unknown" not in reasons2


async def test_journey_draft_and_validate(full_registry, seeded_world, db_factory, monkeypatch):
    """旅程 4b：draft（私有）→ validate（可发布性 + 同意边界）。"""
    # AsyncSessionLocal 指向测试库（工具经 run_sync 打开 async 会话 ——
    # 直接复用 db_factory 的同文件 async 工厂）。
    import app.core.database as database_module

    from app.core.config import settings

    monkeypatch.setattr(database_module, "AsyncSessionLocal", db_factory)
    monkeypatch.setattr(settings, "DATA_DIR", "/tmp/pub-journey-tools")

    draft = await _dispatch(full_registry, "publication_draft", {
        "title": "区县学校分布专题图",
        "purpose": "Pi 准备的私有草稿",
        "attribution": "合成区县数据（simulated）",
        "license": "CC0-SIMULATED",
        "selected_layer_ids": ["lyr-schools", "lyr-districts"],
        "field_whitelists": {
            "lyr-schools": ["school_name", "level", "students"],
            "lyr-districts": ["district_name", "population"],
        },
    }, SESSION_A)
    assert draft["status"] == "draft_created"
    assert draft["row_status"] == "draft"
    assert "作者在工作台发布面板预览并显式确认发布" in draft["next_step"]

    validate = await _dispatch(full_registry, "publication_validate", {
        "publication_id": draft["publication_id"],
    }, SESSION_A)
    assert validate["status"] == "validated"
    assert validate["can_publish"] is True
    assert validate["hard_blockers"] == []
    assert validate["accessibility_complete"] is True
    assert "Agent 不能代行" in validate["consent_boundary"]


async def test_journey_session_boundary_fail_closed(full_registry, seeded_world):
    """跨会话负例：runtime context 绑定 A，可信会话参数点名 B → 拒绝。

    （dispatch 的第三参是服务端可信 session_id：LLM arguments 里的
    session_id 会被剥离注入 —— 注入面绕不过归属校验。）
    """
    from app.lib.runtime.context import bind_runtime_context

    with bind_runtime_context(session_id=SESSION_A):
        result = await full_registry.dispatch("publication_inspect", {}, SESSION_B)
    assert result.get("code") == "session_forbidden"


async def test_journey_tool_family_cannot_publish(full_registry):
    """同意红线（生产 registry 面）：不存在 publish/link/revoke 工具。"""
    for name in ("publication_publish", "publication_link", "publication_revoke"):
        assert full_registry.get(name) is None, f"工具族不得提供 {name}"
