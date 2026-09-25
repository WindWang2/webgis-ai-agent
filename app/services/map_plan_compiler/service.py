"""MapPlanCompilerService —— F12 门面（ADR-0214 D7）。

把 project → check → compile → apply → finalize 串成一个可测、可注入
的门面；调用方（Pi 工具 / 未来 planner 接线）只面对本门面。任务流程
仍归 Pi/planner（本服务不排任务）；MapSpec 提交仍只经 lifecycle 引擎。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from app.lib.cartography.plan_ir import MapPlanIR
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
        """从会话真值读当前 spec + revision 再编译（生产入口）。"""
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id) or {}
        try:
            revision = int(state.get("_cartographic_mutation_revision", 0) or 0)
        except (TypeError, ValueError):
            revision = 0
        return self.compile(ir, state, base_revision=revision)

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
