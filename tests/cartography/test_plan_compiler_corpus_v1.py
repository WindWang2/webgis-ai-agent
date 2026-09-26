"""F12 端到端语料测试（ADR-0214 DoD-8）。

中文自然语言需求 → resolve_intent_adaptive（规则快路径，零 LLM）→
MapProductPlanner → MapPlanIR 投影 → obligations → 确定性编译 →
引擎 CAS 提交 → receipt → finalization 对账 → replay。

锁定的是**编译器契约**（确定性、最小 diff、user-wins、终态可证明），
不锁定 planner 的 recipe 选择（那是 planner 自己的测试域）。
"""
import shutil
import uuid

import pytest

from app.lib.cartography.plan_ir import UserLockSnapshot
from app.services.gis_harness.components import CartographyComponent
from app.services.gis_harness.intent import MapRequestIntent
from app.services.gis_harness.planner import (
    MapProductPlan,
    MapProductPlanner,
    PlannedLayer,
)
from app.services.map_plan_compiler.apply import apply_plan
from app.services.map_plan_compiler.compiler import compile_plan
from app.services.map_plan_compiler.plan_amendment import PlanAmendment
from app.services.map_plan_compiler.projector import amend_plan_ir, project_plan_ir
from app.services.map_plan_compiler.receipt import PLAN_RECEIPTS_KEY
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    UpsertSourceIntent,
)
from app.services.session_data import session_data_manager


@pytest.fixture
async def session():
    sid = f"f12-corpus-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    import os

    from app.services.mapspec.store import BASE_STORAGE_DIR
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


async def _seed(session_id: str) -> int:
    engine = MapSpecLifecycleEngine()
    assert not (await engine.apply_mutation(session_id, InitProjectIntent())).is_error
    for src in ("src:main", "src:boundary"):
        up = await engine.apply_mutation(
            session_id, UpsertSourceIntent(
                source_id=src, source={"type": "geojson"}))
        assert not up.is_error
    state = await session_data_manager.get_map_state(session_id)
    return int(state.get("_cartographic_mutation_revision", 0))


def _plan_for(query: str, *, role="primary", layer_type="fill",
              cartography="choropleth", ref="src:main", **kw) -> MapProductPlan:
    """NL → 规则意图 → planner plan（bound_ref 落定 = 数据解析完成态）。"""
    intent, _ = _resolve(query)
    planner = MapProductPlanner()
    plan = planner.plan_from_intent(intent, use_memo=False)
    planned = PlannedLayer(role=role, layer_type=layer_type,
                           cartography=cartography, bound_ref=ref, enabled=True)
    plan = plan.model_copy(update={
        "map_layers": [planned],
        "components": [CartographyComponent(id="comp-title", type="title",
                                            enabled=True, position="top-center")],
    })
    return plan


def _resolve(query: str):
    from app.services.gis_harness.intent import resolve_intent_adaptive
    return resolve_intent_adaptive(query, use_llm=False)


async def _finalize(sid: str, compilation):
    from app.services.gis_harness.display_confirmation import is_display_confirmed
    from app.services.map_plan_compiler.finalization import check_final_display
    from app.services.session_data import session_data_manager

    state = await session_data_manager.get_map_state(sid)
    confirmed = await is_display_confirmed(sid, render_seq=0)
    return check_final_display(compilation.display_expectations, state,
                               display_confirmed=bool(confirmed))


# ── 语料 ─────────────────────────────────────────────────────────────────

CORPUS = [
    ("choropleth_ratio", "制作湖北省人口密度分级设色图",
     dict(role="primary", layer_type="fill", cartography="choropleth")),
    ("heat_density", "把外卖订单画成密度热力图",
     dict(role="primary", layer_type="heatmap", cartography="visual_heatmap")),
    ("point_overlay", "在地图上标出所有监测站点位置分布",
     dict(role="primary", layer_type="circle", cartography="point_overlay")),
    ("proportional_symbol", "用比例符号展示各城市GDP规模对比",
     dict(role="primary", layer_type="circle", cartography="proportional_symbol")),
]


@pytest.mark.cartography
@pytest.mark.asyncio
@pytest.mark.parametrize("case_id,query,layer_kw", CORPUS)
async def test_corpus_nl_to_committed_mapspec(session, case_id, query, layer_kw):
    """单轮：中文 NL → 编译 → 提交 → 终态可证明 → 重放幂等。"""
    engine = MapSpecLifecycleEngine()
    base_revision = await _seed(session)
    plan = _plan_for(query, **layer_kw)
    ir = project_plan_ir(plan)

    # 确定性：同输入两次投影+编译完全一致
    ir_again = project_plan_ir(_plan_for(query, **layer_kw))
    assert ir.ir_fingerprint() == ir_again.ir_fingerprint()

    state = await session_data_manager.get_map_state(session)
    revision = int(state.get("_cartographic_mutation_revision", 0))
    compilation = compile_plan(ir, state, base_revision=revision)
    assert compilation.status == "compiled", compilation.obligations.reason_codes

    result = await apply_plan(session, compilation, engine=engine)
    assert result.status == "applied", \
        [(a.error_code, a.error_msg) for a in result.receipt.applied if not a.ok]
    assert result.receipt.decision.get("kind") == "plan_compile"

    final = await _finalize(session, compilation)
    assert final.status == "confirmed", \
        [(r.target, r.expected, r.actual) for r in final.failed_rows]

    state_after = await session_data_manager.get_map_state(session)
    ring = state_after.get(PLAN_RECEIPTS_KEY) or []
    assert len(ring) == 1 and ring[0]["receipt_id"] == result.receipt.receipt_id


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_corpus_multi_turn_amendments_minimal_diff(session):
    """多轮：加统计图 / 隐藏图层 / 换配色改分类数 / 改标题 —— 每轮只触达目标。"""
    engine = MapSpecLifecycleEngine()
    await _seed(session)

    async def revision_now():
        state = await session_data_manager.get_map_state(session)
        return int(state.get("_cartographic_mutation_revision", 0))

    async def run_round(ir):
        state = await session_data_manager.get_map_state(session)
        compilation = compile_plan(ir, state, base_revision=await revision_now())
        result = await apply_plan(session, compilation, engine=engine)
        return compilation, result

    plan = _plan_for("制作湖北省人口密度分级设色图", role="primary",
                     layer_type="fill", cartography="choropleth")
    plan = plan.model_copy(update={"map_layers": [
        PlannedLayer(role="primary", layer_type="fill", cartography="choropleth",
                     bound_ref="src:main", enabled=True),
        PlannedLayer(role="reference", layer_type="line", cartography="boundary",
                     bound_ref="src:boundary", enabled=True),
    ]})
    ir1 = project_plan_ir(plan)
    c1, r1 = await run_round(ir1)
    assert r1.status == "applied"
    round1_targets = {m.target for m in c1.mutations}
    assert round1_targets == {"pl-li-01-primary", "pl-li-02-reference", "comp-title"}, \
        "首轮从零 spec 补齐主层/参考层/title（seed 只建了 sources）"

    # 轮 2：加统计图 + 隐藏参考层 —— 只触达这两个目标
    ir2 = amend_plan_ir(ir1, [
        PlanAmendment(kind="add_chart", chart_type="chart_panel",
                      title="人口结构", layer_id="pl-li-01-primary"),
        PlanAmendment(kind="set_layer_visibility",
                      layer_id="pl-li-02-reference", visible=False),
    ])
    c2, r2 = await run_round(ir2)
    assert r2.status == "applied"
    round2 = [(m.intent, m.target) for m in c2.mutations]
    assert round2 == [
        ("patch_layer_presentation", "pl-li-02-reference"),
        ("patch_component", "comp-chart-01"),
    ], f"第二轮必须最小 diff：{round2}"
    assert all(m.target != "pl-li-01-primary" for m in c2.mutations), "主层不得被触碰"

    # 轮 3：换配色 + 改分类数（restyle）——paint+legend 双变化合并为单笔最小 upsert
    ir3 = amend_plan_ir(ir2, [
        PlanAmendment(kind="restyle_layer", layer_id="pl-li-01-primary",
                      paint={"fill-color": "#6a1b9a"},
                      classification={"k": 6, "method": "quantile"})])
    c3, r3 = await run_round(ir3)
    assert r3.status == "applied"
    assert all(m.target == "pl-li-01-primary" for m in c3.mutations)
    assert len(c3.mutations) == 1, "paint+legend 双变化合并为单笔（最少 mutation）"
    merged = c3.mutations[0]
    assert merged.intent == "upsert_layer" and merged.payload.get("merge") is True
    assert merged.payload["layer"]["paint"]["fill-color"] == "#6a1b9a"
    assert merged.payload["layer"]["legend_spec"]["classification"]["k"] == 6

    # 轮 4：改标题 —— 只 patch title 组件 options
    ir4 = amend_plan_ir(ir3, [PlanAmendment(kind="set_title", title="湖北人口密度（2020）")])
    c4, r4 = await run_round(ir4)
    assert r4.status == "applied"
    assert [(m.intent, m.target) for m in c4.mutations] == [
        ("patch_component", "comp-title")]

    # 终态：期望面全部在位
    final = await _finalize(session, c4)
    assert final.status == "confirmed", \
        [(r.target, r.expected, r.actual) for r in final.failed_rows]
    # 回执链：4 轮 receipt 环 + supersede 链完整
    state = await session_data_manager.get_map_state(session)
    ring = state.get(PLAN_RECEIPTS_KEY) or []
    assert len(ring) == 4
    for prev, curr in zip(ring, ring[1:]):
        assert curr["supersedes"] == prev["ir_id"]


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_corpus_user_lock_blocks_plan_compile(session):
    """user-wins：锁定层出现在 IR 锁快照里 ⇒ 编译 blocked、spec 不动。"""
    engine = MapSpecLifecycleEngine()
    await _seed(session)
    plan = _plan_for("制作湖北省人口密度分级设色图")
    ir = project_plan_ir(plan, user_locks=UserLockSnapshot(
        layer_ids=["pl-li-01-primary"]))
    state = await session_data_manager.get_map_state(session)
    revision = int(state.get("_cartographic_mutation_revision", 0))
    compilation = compile_plan(ir, state, base_revision=revision)
    assert compilation.status == "blocked"
    assert any(f.code == "PLAN_LOCK_CONFLICT" for f in compilation.obligations.blocking)
    result = await apply_plan(session, compilation, engine=engine)
    assert result.status == "blocked"
    state_after = await session_data_manager.get_map_state(session)
    assert state_after.get("layers") == state.get("layers")


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_corpus_replay_is_idempotent(session):
    """重放双路径：(a) 重编译同 IR → desired 已满足零 mutation；
    (b) 原编译对象带陈旧 CAS 重放 → 引擎幂等去重全命中。指纹均不变。"""
    from app.lib.cartography.quality_loop import cartographic_fingerprint

    engine = MapSpecLifecycleEngine()
    await _seed(session)
    plan = _plan_for("把外卖订单画成密度热力图", role="primary",
                     layer_type="heatmap", cartography="visual_heatmap")
    ir = project_plan_ir(plan)

    state = await session_data_manager.get_map_state(session)
    revision_before = int(state.get("_cartographic_mutation_revision", 0))
    compilation = compile_plan(ir, state, base_revision=revision_before)

    async def apply(c):
        result = await apply_plan(session, c, engine=engine)
        state_after = await session_data_manager.get_map_state(session)
        return result, cartographic_fingerprint(state_after)

    r1, fp1 = await apply(compilation)
    assert r1.status == "applied"

    # (a) 重编译（fresh state）→ 全部满足 → empty（零 mutation）
    state2 = await session_data_manager.get_map_state(session)
    revision2 = int(state2.get("_cartographic_mutation_revision", 0))
    recompiled = compile_plan(ir, state2, base_revision=revision2)
    assert recompiled.mutations == []
    r2, fp2 = await apply(recompiled)
    assert r2.status == "empty"
    assert fp2 == fp1

    # (b) 原编译对象（陈旧 base revision）重放 → 幂等去重，不推进
    r3, fp3 = await apply(compilation)
    assert r3.status == "applied"
    assert all(a.duplicate for a in r3.receipt.applied)
    assert fp3 == fp1
