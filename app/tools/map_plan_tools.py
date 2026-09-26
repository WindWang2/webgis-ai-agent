"""Map Plan Compiler 工具面（F12 / ADR-0214 D7，additive）。

``webgis_compile_map_plan``：把中文自然语言需求经
``resolve_intent_adaptive（规则快路径，零 LLM）→ MapProductPlanner →
MapPlanIR 投影 → obligations → 确定性编译 → 引擎 CAS 提交 → receipt
→ finalization 对账`` 的完整编译链暴露给 Pi agent。

LLM 只提供 query 与（可选的）多轮 amendment 选择；低层 MapSpec 拼装
正确性（最小 diff、排序、幂等、锁避让）全部由编译器承担。工具自身
**不发明**任何 mutation —— 提交只经 ``MapSpecLifecycleEngine``。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool, ToolExecutionPolicy

logger = logging.getLogger(__name__)

_MAX_AMENDMENTS = 16


class CompileMapPlanArgs(BaseModel):
    query: str = Field(..., min_length=2, max_length=2000,
                       description="中文自然语言制图需求（如『制作湖北省人口密度分级设色图』）")
    session_id: Optional[str] = Field(None, max_length=128,
                                      description="目标会话；缺省用当前会话上下文")
    operation: str = Field("compile_and_apply", max_length=32,
                           description="compile_and_apply | finalize")
    amendments: Optional[List[Dict[str, Any]]] = Field(
        None, max_length=_MAX_AMENDMENTS,
        description="多轮小修改（如 [{kind:'add_chart',chart_type:'chart_panel',title:'人口结构'}]）")
    layer_bindings: Optional[Dict[str, str]] = Field(
        None, max_length=32,
        description="数据绑定选择：计划图层（'pl-li-01-primary' 或序号 '1'）→ 已在会话内的 source ref；"
                    "缺绑定的新建层会被 obligations 阻塞（DATA_REF_UNRESOLVED）")


def _bounded_summary(result: Any, finalization: Any) -> Dict[str, Any]:
    receipt = result.receipt
    return {
        "success": result.status in ("applied", "empty"),
        "operation": "compile_and_apply",
        "apply_status": result.status,
        "receipt_id": receipt.receipt_id,
        "compile_id": receipt.compile_id,
        "ir_id": receipt.ir_id,
        "plan_revision": receipt.plan_revision,
        "base_revision": receipt.base_revision,
        "final_revision": receipt.final_revision,
        "final_fingerprint": (receipt.final_fingerprint or "")[:24],
        "mutation_count": len(receipt.applied),
        "duplicates": sum(1 for a in receipt.applied if a.duplicate),
        "reason_codes": list(receipt.reason_codes)[:8],
        "finalization": {
            "status": finalization.status,
            "ack_mode": finalization.ack_mode,
            "failed_rows": [
                {"kind": r.kind, "target": r.target, "expected": r.expected,
                 "actual": r.actual, "code": r.reason_code}
                for r in finalization.failed_rows[:8]
            ],
        },
        "decision_id": (receipt.decision or {}).get("decision_id", ""),
    }


def register_map_plan_tools(registry: ToolRegistry):
    """注册编译链工具（additive；经既有 dispatch/review gate）。"""

    @tool(
        registry,
        name="webgis_compile_map_plan",
        capabilities=["thematic_cartography"],
        tier=2,
        domains=["cartography"],
        description=(
            "把用户的中文制图需求编译为确定性的 MapSpec 修改计划并提交。"
            "\n何时用：用户提出完整制图需求（数据+表达），或要在既有成果上做多轮小修改"
            "（加统计图/隐藏图层/换配色/改分类数/改标题/导出）。"
            "\n何时不用：(1) 只想动相机 — 用 fly_to_location/set_map_view；"
            "(2) 需要新增数据上传 — 先走数据面工具，再调用本工具编译。"
            "\n关键约束：多轮修改请传 amendments（只描述改动），工具保证最小 diff，"
            "不触碰用户锁定的图层/组件。"
        ),
        args_model=CompileMapPlanArgs,
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="state_mutation",
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=("制图计划", "确定性编译", "MapSpec", "多轮修改", "plan compiler"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        required_context=("map_state",),
        map_mutations=("add_layer", "remove_layer", "style_layer", "component",
                       "theme", "map_product"),
        failure_modes=("invalid_args", "missing_data", "lock_conflict", "stale_plan"),
    )
    async def webgis_compile_map_plan(
        query: str,
        session_id: Optional[str] = None,
        operation: str = "compile_and_apply",
        amendments: Optional[List[Dict[str, Any]]] = None,
        layer_bindings: Optional[Dict[str, str]] = None,
    ) -> dict:
        from app.services.gis_harness.intent import resolve_intent_adaptive
        from app.services.gis_harness.planner import MapProductPlanner
        from app.services.map_plan_compiler.plan_amendment import PlanAmendment
        from app.services.map_plan_compiler.receipt import (
            PLAN_RECEIPTS_KEY,
        )
        from app.services.map_plan_compiler.compiler import DisplayExpectation
        from app.services.map_plan_compiler.finalization import check_final_display
        from app.services.map_plan_compiler.service import map_plan_compiler_service
        from app.services.session_data import session_data_manager
        from app.services.gis_harness.display_confirmation import (
            display_mode,
            is_display_confirmed,
        )

        sid = (session_id or "").strip()
        if not sid:
            return {"success": False, "error": "session_id 必填（当前会话上下文缺失）"}

        if operation == "finalize":
            state = await session_data_manager.get_map_state(sid) or {}
            ring = state.get(PLAN_RECEIPTS_KEY) or []
            if not ring:
                return {"success": False, "error": "该会话无编译回执（先 compile_and_apply）"}
            last = ring[-1] if isinstance(ring[-1], dict) else {}
            expectations = DisplayExpectation(**(last.get("display_expectations") or {}))
            mode = display_mode()
            confirmed = await is_display_confirmed(sid, render_seq=0)
            check = check_final_display(
                expectations, state, display_confirmed=bool(confirmed), ack_mode=mode)
            return {
                "success": check.status == "confirmed",
                "operation": "finalize",
                "receipt_id": last.get("receipt_id", ""),
                "finalization": {
                    "status": check.status,
                    "ack_ok": check.ack_ok,
                    "rows": [
                        {"kind": r.kind, "target": r.target, "expected": r.expected,
                         "actual": r.actual, "ok": r.ok}
                        for r in check.rows[:32]
                    ],
                    "reason_codes": list(check.reason_codes)[:8],
                },
            }

        if operation != "compile_and_apply":
            return {"success": False,
                    "error": f"未知 operation={operation!r}（compile_and_apply | finalize）"}

        # ── 意图（规则快路径，零 LLM）→ planner plan ────────────────────
        intent, _clar = resolve_intent_adaptive(query, use_llm=False)
        planner = MapProductPlanner()
        plan = planner.plan_from_intent(intent, use_memo=False)

        # ── 数据绑定选择（LLM 选 ref，编译器验活性 —— review P1-2 配套）──
        if layer_bindings:
            clean: Dict[int, str] = {}
            for i, pl in enumerate(plan.map_layers or [], start=1):
                minted = f"pl-li-{i:02d}-{pl.role}"
                ref = layer_bindings.get(minted) or layer_bindings.get(str(i)) or ""
                if ref:
                    clean[i] = str(ref)[:128]
            if clean:
                layers = [
                    pl.model_copy(update={"bound_ref": clean.get(i, pl.bound_ref)})
                    for i, pl in enumerate(plan.map_layers or [], start=1)
                ]
                plan = plan.model_copy(update={"map_layers": layers})

        # ── 投影 → IR（锁快照前置：obligations 在编译前拦截锁冲突，
        #    杜绝"引擎中途拒 → 部分提交"—— review P1-1）──────────────────
        lock_snapshot = await map_plan_compiler_service.lock_snapshot_for(sid)
        ir = map_plan_compiler_service.project(plan, user_locks=lock_snapshot)
        if amendments:
            try:
                parsed = [PlanAmendment(**a) for a in amendments[:_MAX_AMENDMENTS]]
            except Exception as exc:  # noqa: BLE001 — 非法 amendment 参数面 4xx 语义
                return {"success": False, "error": f"amendments 非法: {exc}"}
            ir = map_plan_compiler_service.amend(ir, parsed)

        # ── 编译（含 obligations 闸）→ 提交 → 终态对账 ──────────────────
        compilation = await map_plan_compiler_service.compile_for_session(sid, ir)
        result = await map_plan_compiler_service.apply(sid, compilation)
        finalization = await map_plan_compiler_service.finalize(sid, compilation)
        summary = _bounded_summary(result, finalization)
        summary["plan_id"] = getattr(plan, "plan_id", "")
        summary["recipe_id"] = getattr(plan, "recipe_id", "")
        return summary
