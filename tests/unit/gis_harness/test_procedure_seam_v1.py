"""ProcedureBinding seam 集成 —— 真实 SessionPlan 路径（ADR-0220 DoD）。

不启动 LLM/Pi：走真实持久化缝
（``apply_tool_result("webgis_map_intent")`` → gis_chapter）与真实投影
（``format_session_plan_projection``），验证：

    skill selection → plan binding（落 chapter）→ tool evidence
    （行推进 + kernel steps）→ coverage → 投影行 / pi 卡
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.skills.loader import get_skill_library
from app.services.gis_harness.skills.policy import SkillPolicyDecision
from app.services.gis_harness.skills.procedure_binding import (
    bind_procedure_to_plan,
)
from app.services.gis_harness.skills.procedure_coverage import (
    compute_procedure_coverage,
    procedure_pi_card,
)
from app.services.session_data import session_data_manager
from app.services.session_plan import (
    apply_tool_result,
    format_session_plan_projection,
    load_session_plan,
)


@pytest.fixture
async def sid():
    session_id = "sess-procedure-seam"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)


def _intent_result(library, skill_id="spatial_autocorrelation_analysis"):
    """模拟 webgis_map_intent 的服务端结果（plan payload 含绑定块）。

    生产路径：tools.py 在 intent 缝计算 binding → plan_payload
    ["procedure_binding"] → apply_tool_result 原样落 chapter。
    """
    skill = library.get(skill_id)
    dec = SkillPolicyDecision(
        mode="execute_guided", selected_skill=skill.id,
        skill_version=skill.version, trust_tier="core", confidence=0.9)
    plan = {
        "plan_id": "plan-seam-1",
        "manifest_fingerprint": "fp-seam-1",
        "query": "成都房价空间聚集分析",
        "recipe_id": (skill.recipe_refs or ["none"])[0],
        "intent": {
            "query": "成都房价空间聚集分析",
            "task": "spatial_autocorrelation",
            "scope": {"name": "成都", "level": "city"},
            "subject": {"type": "poi", "category": "房价"},
        },
        "data_requirements": [
            {"capability": "spatial_weights_diagnostics", "status": "pending",
             "resolved_tool": "webgis_spatial_weights",
             "resolved_algorithm": "knn_weights", "depends_on": []},
            {"capability": "global_morans_i", "status": "pending",
             "resolved_tool": "webgis_global_moran",
             "resolved_algorithm": "moran_i", "depends_on": [
                 "spatial_weights_diagnostics"]},
        ],
        "analysis_steps": [
            {"capability": "local_morans_i", "status": "pending",
             "resolved_tool": "webgis_lisa",
             "resolved_algorithm": "lisa", "depends_on": ["global_morans_i"]},
            {"capability": "thematic_cartography", "status": "pending",
             "resolved_tool": "webgis_thematic_map",
             "resolved_algorithm": "", "depends_on": ["local_morans_i"]},
        ],
    }
    binding = bind_procedure_to_plan(skill, dec, plan)
    assert binding is not None
    plan["procedure_binding"] = binding.to_bounded_dict()
    return skill, dec, plan


@pytest.mark.asyncio
async def test_full_chain_selection_to_coverage(sid):
    """DoD 场景：skill selection → binding 落 chapter → 行推进 → coverage。"""
    library = get_skill_library()
    skill, dec, plan = _intent_result(library)

    # 1) intent 结果经真实持久化缝写入 SessionPlan
    events = await apply_tool_result(
        sid, "webgis_map_intent", {"success": True, "plan": plan},
        success=True)
    assert events
    stored = await load_session_plan(sid)
    assert stored is not None and stored.gis_chapter is not None
    block = stored.gis_chapter.get("procedure_binding")
    assert isinstance(block, dict) and block["skill_id"] == skill.id

    # 2) 投影：无 procedure 行缺席（旧章节零漂移由下方负例覆盖）
    text0 = format_session_plan_projection(stored)
    assert "Procedure: skill=" in text0

    # 3) 运行期：数据/分析工具推进（真实 apply 路径）
    for tool, cap in (
        ("webgis_spatial_weights", "spatial_weights_diagnostics"),
        ("webgis_global_moran", "global_morans_i"),
        ("webgis_lisa", "local_morans_i"),
        ("webgis_thematic_map", "thematic_cartography"),
    ):
        await apply_tool_result(
            sid, tool, {"success": True}, success=True,
            geojson_ref=f"ref:{cap}")

    stored = await load_session_plan(sid)
    cov = compute_procedure_coverage(
        stored.gis_chapter, envelope=stored, library=library)
    assert cov is not None
    by_state = {s.step_id: s.state for s in cov.steps}
    assert by_state["weights_diagnostic"] == "covered"
    assert by_state["global_test"] == "covered"
    assert by_state["local_clusters"] == "covered"
    assert by_state["variable_check"] == "unknown"  # obligation-only 诚实
    # 注：kernel StepEvidence 只在 runtime.apply_tool_evidence 路径挂载
    # （plain apply_tool_result 只推进行状态）—— coverage 的 covered 以
    # 行 complete 为准，kernel evidence 缺席如实披露（corpus 测试覆盖
    # kernel evidence 绑定面）。
    # complete（replay 兼容）= True（无 missing）；fully_covered = False
    # （variable_check 无运行期检视证据 —— honest）
    assert cov.complete
    assert not cov.fully_covered

    # 4) 投影行反映覆盖（下一轮 Pi 上下文）
    text = format_session_plan_projection(stored)
    assert "Procedure: skill=spatial_autocorrelation_analysis" in text
    assert "covered=3/4" in text
    assert "next=variable_check" in text

    # 5) pi 卡有界
    card = procedure_pi_card(cov)
    assert card["phase"]["current"] == "variable_check"
    assert card["phase"]["index"] == 1 and card["phase"]["total"] == 4


@pytest.mark.asyncio
async def test_projection_zero_drift_without_binding(sid):
    """无绑定块（旧章节/无技能命中）→ 投影零漂移（无 Procedure 行）。"""
    plan = {
        "plan_id": "plan-nb",
        "query": "普通制图",
        "intent": {"query": "普通制图", "task": "distribution_overview",
                   "scope": {"name": "成都", "level": "city"},
                   "subject": {"type": "poi", "category": "咖啡"}},
        "data_requirements": [
            {"capability": "poi_query", "status": "pending",
             "resolved_tool": "query_local_poi"}],
        "analysis_steps": [],
    }
    await apply_tool_result(
        sid, "webgis_map_intent", {"success": True, "plan": plan},
        success=True)
    stored = await load_session_plan(sid)
    text = format_session_plan_projection(stored)
    assert "Procedure:" not in text
    assert compute_procedure_coverage(stored.gis_chapter) is None


@pytest.mark.asyncio
async def test_supersede_drops_old_procedure_generation(sid):
    """新 goal supersede：旧章节（含旧绑定）归档，新章节从新绑定开始。"""
    library = get_skill_library()
    _, _, plan1 = _intent_result(library)
    await apply_tool_result(
        sid, "webgis_map_intent",
        {"success": True, "plan": plan1, "intent": plan1["intent"]},
        success=True)
    await apply_tool_result(
        sid, "webgis_spatial_weights", {"success": True},
        success=True, geojson_ref="ref:spatial_weights_diagnostics")

    # 异 goal 新意图（scope 变化）→ supersede；新 plan 无绑定块（模拟
    # 技能未命中的新目标）
    plan2 = {
        "plan_id": "plan-2",
        "query": "北京地铁网络可达性",
        "intent": {"query": "北京地铁网络可达性", "task": "network_accessibility",
                   "scope": {"name": "北京", "level": "city"},
                   "subject": {"type": "network", "category": "地铁"}},
        "data_requirements": [
            {"capability": "network_query", "status": "pending",
             "resolved_tool": "query_network"}],
        "analysis_steps": [],
    }
    await apply_tool_result(
        sid, "webgis_map_intent", {"success": True, "plan": plan2},
        success=True)
    stored = await load_session_plan(sid)
    assert stored.gis_chapter["plan_id"] == "plan-2"
    # 旧 generation 的绑定/覆盖不再投影（零漂移；不为新 goal 冒充方法）
    assert compute_procedure_coverage(stored.gis_chapter) is None
    assert "Procedure:" not in format_session_plan_projection(stored)


@pytest.mark.asyncio
async def test_replace_voids_rows_and_coverage_marks_stale(sid):
    """同 goal replace：旧行 void → 对应 procedure 步骤 stale（证据失效）。"""
    library = get_skill_library()
    _, _, plan1 = _intent_result(library)
    await apply_tool_result(
        sid, "webgis_map_intent", {"success": True, "plan": plan1},
        success=True)
    await apply_tool_result(
        sid, "webgis_spatial_weights", {"success": True},
        success=True, geojson_ref="ref:spatial_weights_diagnostics")

    # 同 goal replace：行语义签名变化（算法换了）→ 完成事实不携带
    # （intent_diff 判 lost）→ progress 行 void → 对应步骤 stale。
    plan2 = {
        **plan1,
        "plan_id": "plan-seam-2",
        "manifest_fingerprint": "fp-seam-2",
        "data_requirements": [
            {**r, "resolved_algorithm": "knn_weights_v2"}
            if r["capability"] == "spatial_weights_diagnostics" else dict(r)
            for r in plan1["data_requirements"]
        ],
        "analysis_steps": [dict(r) for r in plan1["analysis_steps"]],
        # 沿用 plan1 绑定块：验证「绑定 + voided progress」的 stale 语义
        "procedure_binding": plan1["procedure_binding"],
    }
    await apply_tool_result(
        sid, "webgis_map_intent", {"success": True, "plan": plan2},
        success=True)
    stored = await load_session_plan(sid)
    # 同 goal：void 行 → capability stale；未执行行 → unknown（都不冒充 covered）
    cov = compute_procedure_coverage(
        stored.gis_chapter, envelope=stored, library=library)
    assert cov is not None
    states = {c.capability_id: c.state for c in cov.capabilities}
    # voided 行 → stale；未执行行 → unknown（都不能冒充 covered）
    assert states["spatial_weights_diagnostics"] == "stale", states
    assert states["global_morans_i"] == "unknown", states
    step_states = {s.step_id: s.state for s in cov.steps}
    assert step_states["weights_diagnostic"] == "stale"
    # stale ≠ missing（replay 兼容 complete 不被 stale 阻断），但强语义
    # fully_covered 不成立（stale 步骤不冒充完成）
    assert not cov.fully_covered
