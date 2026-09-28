"""MapPlanCompilerService —— F12 门面（ADR-0214 D7）。

把 project → check → compile → apply → finalize 串成一个可测、可注入
的门面；调用方（Pi 工具 / 未来 planner 接线）只面对本门面。任务流程
仍归 Pi/planner（本服务不排任务）；MapSpec 提交仍只经 lifecycle 引擎。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

from app.lib.cartography.plan_ir import MapPlanIR, UserLockSnapshot, spec_doc_of
from app.services.map_plan_compiler.apply import PlanApplyResult, apply_plan
from app.services.map_plan_compiler.compiler import (
    PlanCompilation,
    compile_plan,
)
from app.services.map_plan_compiler.finalization import (
    FinalizationCheck,
    check_final_display,
)
from app.services.map_plan_compiler.obligations import (
    ObligationReport,
    check_obligations,
)
from app.services.map_plan_compiler.plan_amendment import PlanAmendment
from app.services.map_plan_compiler.projector import amend_plan_ir, project_plan_ir
from app.services.map_plan_compiler.receipt import receipt_is_stale

logger = logging.getLogger(__name__)

__all__ = ["MapPlanCompilerService", "map_plan_compiler_service"]


class MapPlanCompilerService:
    """无状态门面（全部依赖可注入 —— 引擎/store 在测试中可替换）。"""

    # ── 锁快照（obligations 前置闸的生产输入；权威仍在引擎守卫）────────
    async def lock_snapshot_for(self, session_id: str) -> UserLockSnapshot:
        """从会话 workbench 态读取当前用户锁（P1 修复：生产路径必须把
        锁面喂给 obligations，杜绝"编译放行 → 引擎中途拒 → 部分提交"）。"""
        from app.services.mapspec.lifecycle_engine import (
            locked_component_ids_of,
            locked_layer_ids_of,
        )
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id) or {}
        doc = spec_doc_of(state)
        layer_ids = locked_layer_ids_of(doc)
        component_ids = locked_component_ids_of(doc)
        wb = doc.get("workbench") if isinstance(doc.get("workbench"), dict) else {}
        snapshot = UserLockSnapshot(
            layer_ids=layer_ids, component_ids=component_ids,
            fingerprint=f"wb:{len(layer_ids)}:{len(component_ids)}",
        )
        if wb.get("_rev") is not None:
            snapshot = snapshot.model_copy(update={
                "workbench_revision": int(wb.get("_rev") or 0)})
        return snapshot

    # ── project / amend ────────────────────────────────────────────────
    def project(self, plan: Any, **kwargs: Any) -> MapPlanIR:
        return project_plan_ir(plan, **kwargs)

    def amend(self, base: MapPlanIR, amendments: Sequence[PlanAmendment]) -> MapPlanIR:
        return amend_plan_ir(base, amendments)

    # ── check / compile ────────────────────────────────────────────────
    def check(
        self, ir: MapPlanIR, current: Optional[Dict[str, Any]],
        *, analysis_output_ids: Sequence[str] = (),
    ) -> ObligationReport:
        return check_obligations(ir, current, analysis_output_ids=analysis_output_ids)

    def compile(
        self, ir: MapPlanIR, current: Optional[Dict[str, Any]],
        *, base_revision: int = 0,
        analysis_output_ids: Sequence[str] = (),
    ) -> PlanCompilation:
        return compile_plan(
            ir, current, base_revision=base_revision,
            analysis_output_ids=analysis_output_ids,
        )

    async def compile_for_session(
        self, session_id: str, ir: MapPlanIR,
    ) -> PlanCompilation:
        """从会话真值读当前 spec + revision 再编译（生产入口）。

        ADR-0217（H10）：编译前先做 classification 投影期物化 —— blueprint
        携带分级参数而 legend 缺 breaks 时，从会话数据确定性重算
        breaks/legend/paint（"把分级数改成 7" 不再是只改数字字段）。
        物化失败/数据缺失 → typed 记录 + 原样回落既有 token 行为
        （kill switch ``GIS_ACTION_DERIVE``，默认 ON；fail-open）。
        """
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id) or {}
        try:
            revision = int(state.get("_cartographic_mutation_revision", 0) or 0)
        except (TypeError, ValueError):
            revision = 0
        ir, derive_codes = await self.materialize_classification(
            ir, state, session_id=session_id)
        compilation = self.compile(ir, state, base_revision=revision)
        if derive_codes:
            compilation = compilation.model_copy(update={
                "reason_codes": (
                    list(compilation.reason_codes) + derive_codes)[:24]})
        return compilation

    async def materialize_classification(
        self, ir: MapPlanIR, current: Any,
        *, session_id: str,
    ) -> tuple[MapPlanIR, list[str]]:
        """classification 参数 → 完整 legend_spec（投影期；compiler 保持纯）。

        返回 (可能更新的 IR, 有界 reason codes)。任何故障回落原 IR。
        """
        import os

        if os.getenv("GIS_ACTION_DERIVE", "1") == "0":
            return ir, []
        try:
            from app.services.gis_action.derive import (
                materialize_classification as _materialize,
            )
            from app.services.session_data import session_data_manager

            async def _loader(ref: str):
                try:
                    resolved = await session_data_manager.resolve_alias(
                        session_id, ref)
                except Exception:  # noqa: BLE001 — 别名解析失败按原 ref 取
                    resolved = ref
                data = await session_data_manager.get(session_id, resolved)
                return data if isinstance(data, dict) else None

            updated, records = await _materialize(
                ir, current=current, load_geojson=_loader)
        except Exception:  # noqa: BLE001 — 物化绝不阻断编译链
            logger.warning("[map-plan-compiler] classification derive skipped",
                           exc_info=True)
            return ir, []
        codes = [f"DERIVE:{r.code}:{r.layer_id[:24]}" for r in records[:8]]
        return updated, codes

    # ── apply ──────────────────────────────────────────────────────────
    async def apply(
        self, session_id: str, compilation: PlanCompilation,
        *, engine: Optional[Any] = None, origin: str = "agent",
    ) -> PlanApplyResult:
        return await apply_plan(
            session_id, compilation, engine=engine, origin=origin,
        )

    # ── finalize ───────────────────────────────────────────────────────
    async def finalize(
        self,
        session_id: str,
        compilation: PlanCompilation,
        *,
        current: Optional[Dict[str, Any]] = None,
        render_seq: int = 0,
    ) -> FinalizationCheck:
        """最终显示对账：期望面（编译产物）vs 当前 spec + render ACK。"""
        from app.services.gis_harness.display_confirmation import (
            display_mode,
            is_display_confirmed,
        )
        from app.services.session_data import session_data_manager

        if current is None:
            state = await session_data_manager.get_map_state(session_id) or {}
            current = state
        mode = display_mode()
        confirmed = await is_display_confirmed(session_id, render_seq=render_seq)
        return check_final_display(
            compilation.display_expectations, current,
            display_confirmed=bool(confirmed), ack_mode=mode,
        )

    # ── stale ──────────────────────────────────────────────────────────
    @staticmethod
    def receipt_stale(receipt: Any, current_fingerprint: str) -> bool:
        return receipt_is_stale(receipt, current_fingerprint)


map_plan_compiler_service = MapPlanCompilerService()
