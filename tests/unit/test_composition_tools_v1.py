"""Composition tools 契约测试（ADR-0214 D6）。

覆盖：三工具注册与 reason codes、discover 有界候选 + W5 备选接线、
apply 的 conformance/锁预检与确定性提交、plan_replace 只读性与 props
前置校验。工具经 registry.dispatch 调用（与生产同路径）。
"""
import uuid

import pytest

from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    r = ToolRegistry()
    from app.tools.composition_tools import register_composition_tools
    register_composition_tools(r)
    return r


@pytest.fixture
async def clean_session():
    sid = f"f11-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    session_dir = BASE_STORAGE_DIR / sid
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)


import shutil  # noqa: E402


def test_reason_code_vocab_locked():
    from app.tools.composition_tools import COMPOSITION_TOOL_REASON_CODES
    assert "missing_session" in COMPOSITION_TOOL_REASON_CODES
    assert "component_locked:user_wins" in COMPOSITION_TOOL_REASON_CODES
    assert "conformance_error" in COMPOSITION_TOOL_REASON_CODES


def test_tools_registered(registry):
    for name in ("webgis_discover_components", "webgis_apply_composition",
                 "webgis_plan_component_replace"):
        assert registry.has(name)


@pytest.mark.asyncio
async def test_discover_requires_session_or_returns_empty(registry):
    res = await registry.dispatch(
        "webgis_discover_components", {}, session_id=None)
    # 无 session 合法（目录级发现）：候选来自 registry，spec 相关字段为空
    assert res["success"] is True
    assert isinstance(res["candidates"], list) and res["candidates"]
    assert res["composition_alternatives"]["version"] == 1


@pytest.mark.asyncio
async def test_discover_returns_bounded_candidates_with_support(registry):
    res = await registry.dispatch(
        "webgis_discover_components",
        {"map_model": "administrative_choropleth",
         "semantic_roles": ["legend"], "output_target": "png", "limit": 4},
        session_id=None)
    assert res["success"] is True
    assert 0 < res["candidate_count"] <= 4
    for cand in res["candidates"]:
        assert "renderer_support" in cand and "exporter_support" in cand
        assert cand["abi_version"] == 1
        assert len(cand["reasons"]) <= 4
    assert isinstance(res["contracts"], list) and res["contracts"]


@pytest.mark.asyncio
async def test_apply_requires_contract(registry, clean_session):
    res = await registry.dispatch(
        "webgis_apply_composition", {"contract_id": "contract.ghost"},
        session_id=clean_session)
    assert res["success"] is False
    assert res["reason_codes"] == ["contract_not_found"]
    assert "可用契约" in res["correction_hint"]


@pytest.mark.asyncio
async def test_apply_composition_end_to_end(registry, clean_session):
    """空会话 → 应用基础专题契约 → 组件/链接/身份块落盘。"""
    res = await registry.dispatch(
        "webgis_apply_composition",
        {"contract_id": "contract.core.basic_thematic"},
        session_id=clean_session)
    assert res["success"] is True, res.get("message")
    report = res["report"]
    assert report["created_count"] > 0
    assert "legend-main" in report["created"]
    identity = report["identity"]
    assert identity["template_id"] == "composition.standard_analysis"
    assert identity["component_abi_version"] == 1
    spec = await mapspec_store.get_mapspec(clean_session)
    layout = spec["layout"]
    types = [c["type"] for c in layout["components"]]
    assert "legend" in types and "scale_bar" in types
    assert ("subtitle", "title") in [
        (lk["src"], lk["dst"]) for lk in layout["component_links"]]
    assert layout["composition"]["contract_id"] == "contract.core.basic_thematic"


@pytest.mark.asyncio
async def test_apply_is_idempotent(registry, clean_session):
    args = {"contract_id": "contract.core.basic_thematic"}
    first = await registry.dispatch(
        "webgis_apply_composition", dict(args), session_id=clean_session)
    assert first["success"] is True
    spec_before = await mapspec_store.get_mapspec(clean_session)
    second = await registry.dispatch(
        "webgis_apply_composition", dict(args), session_id=clean_session)
    assert second["success"] is True
    assert second["report"]["created_count"] == 0
    assert second["report"]["links_added"] == 0
    spec_after = await mapspec_store.get_mapspec(clean_session)
    assert spec_before["layout"]["components"] == spec_after["layout"]["components"]


@pytest.mark.asyncio
async def test_apply_preserves_user_edits(registry, clean_session):
    # 先应用契约，再做一次用户编辑（改标题文本）
    await registry.dispatch(
        "webgis_apply_composition",
        {"contract_id": "contract.core.basic_thematic"},
        session_id=clean_session)
    from app.services.mapspec_store import mapspec_store as store
    spec = await store.get_mapspec(clean_session)
    for c in spec["layout"]["components"]:
        if c["id"] == "title":
            c["style"] = {"fontWeight": "900"}
    await store.save_mapspec(clean_session, spec)
    # 再应用另一契约（变化版）→ 用户编辑保留
    second = await registry.dispatch(
        "webgis_apply_composition",
        {"contract_id": "contract.core.change_comparison"},
        session_id=clean_session)
    assert second["success"] is True
    spec2 = await store.get_mapspec(clean_session)
    title = next(c for c in spec2["layout"]["components"] if c["id"] == "title")
    assert title["style"] == {"fontWeight": "900"}, "模板应用不得重置用户编辑"


@pytest.mark.asyncio
async def test_apply_rejected_on_locked_components(registry, clean_session):
    from app.services.mapspec_store import mapspec_store as store
    spec = {
        "version": "1.0",
        "sources": {},
        "layers": [],
        "layout": {"components": [{"id": "title", "type": "title"}]},
        "workbench": {"lockedComponentIds": ["title"]},
    }
    await store.save_mapspec(clean_session, spec)
    res = await registry.dispatch(
        "webgis_apply_composition",
        {"contract_id": "contract.core.basic_thematic"},
        session_id=clean_session)
    assert res["success"] is False
    assert res["reason_codes"] == ["component_locked:user_wins"]
    assert res["locked_component_ids"] == ["title"]


@pytest.mark.asyncio
async def test_plan_replace_read_only_and_props_validation(registry, clean_session):
    from app.services.mapspec_store import mapspec_store as store
    spec = {
        "version": "1.0", "sources": {}, "layers": [],
        "layout": {"components": [{"id": "north-arrow", "type": "north_arrow"}]},
    }
    await store.save_mapspec(clean_session, spec)
    # props 非法 → 只读计划给出结构化 issues，不产生执行参数
    bad = await registry.dispatch(
        "webgis_plan_component_replace",
        {"component_id": "north-arrow", "to_variant": "compass_rose",
         "options": {"variant": 42}},
        session_id=clean_session)
    assert bad["success"] is False
    assert bad["reason_codes"] == ["props_invalid"]
    assert bad["plan"]["update_params"] is None
    # 合法替换 → 返回 component_update 执行参数；spec 不变（只读）
    ok = await registry.dispatch(
        "webgis_plan_component_replace",
        {"component_id": "north-arrow", "to_variant": "compass_rose"},
        session_id=clean_session)
    assert ok["success"] is True
    assert ok["plan"]["update_params"]["tool"] == "webgis_component_update"
    assert ok["plan"]["update_params"]["params"]["variant"] == "compass_rose"
    assert ok["plan"]["locked"] is False
    spec_after = await store.get_mapspec(clean_session)
    assert not spec_after["layout"]["components"][0].get("variant"), \
        "只读规划器不得写入 spec"


@pytest.mark.asyncio
async def test_plan_replace_locked_target(registry, clean_session):
    from app.services.mapspec_store import mapspec_store as store
    spec = {
        "version": "1.0", "sources": {}, "layers": [],
        "layout": {"components": [{"id": "legend-main", "type": "legend"}]},
        "workbench": {"lockedComponentIds": ["legend-main"]},
    }
    await store.save_mapspec(clean_session, spec)
    res = await registry.dispatch(
        "webgis_plan_component_replace",
        {"component_id": "legend-main", "to_variant": "compact"},
        session_id=clean_session)
    assert res["success"] is False
    assert res["reason_codes"] == ["component_locked:user_wins"]
    assert res["plan"]["update_params"] is None


@pytest.mark.asyncio
async def test_plan_replace_component_not_found(registry, clean_session):
    res = await registry.dispatch(
        "webgis_plan_component_replace",
        {"component_id": "ghost", "to_variant": "x"},
        session_id=clean_session)
    assert res["success"] is False
    assert res["reason_codes"] == ["component_not_found"]


# ── review 修复回归（P1-2 / P2-1）─────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_cas_stale_revision_superseded(registry, clean_session):
    """review P1-2：读-改-写窗口的丢失更新必须被 CAS 拦截 —— 落后的
    expected_revision → superseded 拒绝，spec 不变。"""
    first = await registry.dispatch(
        "webgis_apply_composition",
        {"contract_id": "contract.core.basic_thematic"},
        session_id=clean_session)
    assert first["success"] is True
    current = first["mutation_revision"]
    spec_before = await mapspec_store.get_mapspec(clean_session)
    stale = await registry.dispatch(
        "webgis_apply_composition",
        {"contract_id": "contract.core.basic_thematic",
         "expected_revision": max(0, int(current) - 1)},
        session_id=clean_session)
    assert stale["success"] is False
    assert stale["reason_codes"] == ["apply_superseded"]
    spec_after = await mapspec_store.get_mapspec(clean_session)
    assert spec_before == spec_after, "superseded 提交不得改动 spec"


@pytest.mark.asyncio
async def test_apply_engine_lock_rejection_maps_reason_code(registry, clean_session):
    """review P2-1：引擎守卫拒绝（陈旧锁 id 绕过预检）必须映射为
    component_locked:user_wins，而非空 reason_codes。"""
    from app.services.mapspec_store import mapspec_store as store
    # 锁 id "legend"（族前缀）不在当前组件清单 → 工具预检的
    # locked∩payload 为空；但引擎守卫按族匹配命中新建的 legend-main。
    spec = {
        "version": "1.0", "sources": {}, "layers": [],
        "layout": {"components": [{"id": "title", "type": "title"}]},
        "workbench": {"lockedComponentIds": ["legend"]},
    }
    await store.save_mapspec(clean_session, spec)
    res = await registry.dispatch(
        "webgis_apply_composition",
        {"contract_id": "contract.core.basic_thematic"},
        session_id=clean_session)
    assert res["success"] is False
    assert res["reason_codes"] == ["component_locked:user_wins"]
