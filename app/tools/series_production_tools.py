"""Series Production agent tools —— 系列出版生产提交（WP5）。

与 REST（``/api/v1/series-production``）共用同一 service/执行体。提交 =
durable job（幂等键 = 冻结计划 digest + 成员 pins）；执行走既有 Celery
durable 框架（取消/重试/恢复），工具面只做冻结桥接与提交。
"""
import asyncio
import logging
from typing import Optional

from app.tools.design_spec_tools import _actor_context, _guard
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def register_series_production_tools(registry: ToolRegistry) -> None:
    """注册系列生产工具（submit；状态经任务中心通用面轮询）。"""

    @tool(
        registry, name="webgis_series_production_submit",
        tier=2, domains=["cartography"],
        description=(
            "提交系列出版生产（durable job，异步）：以系列冻结计划（逐成员 "
            "session/指纹/版式/规范 pin）为输入，逐成员真实渲染（print_layout "
            "纸面链或出版 PDF 链）+ 指纹缓存 + 成品 QA，产出系列 manifest"
            "（逐成员状态/文件 digest；增量：改一个成员只重做该成员）。"
            "\n何时用：用户说『把这一组地图按统一规范批量出版/导出成品』。"
            "\n前置：系列已建（webgis_map_series_create + add_member）且成员"
            "会话已提交 MapSpec；建议先跑 webgis_map_series_consistency 与"
            " freeze-plan。任务异步：返回 job_id 后在任务中心轮询。"
            "\n负例：成员会话缺失/指纹漂移 → 该成员 blocked 如实披露，"
            "不伪装全成功；未完成文件不标完整。"
        ),
        param_descriptions={
            "series_id": "地图系列 id（webgis_map_series_list 获得）",
            "spec_version": "系列版本（缺省 = 当前版）",
            "session_id": "调用会话（缺省=当前上下文会话）",
        },
        side_effect="artifact_creation", deterministic=True, network=False,
        idempotent=True,
        latency_class="slow", memory_class="heavy", scale_class="large",
        output_semantic_type="text", result_size_policy="inline_small",
        data_mutations=("artifact_write",),
        tags=("系列地图", "出版", "生产", "批量导出"),
        failure_modes=("invalid_args", "not_found", "busy", "member_failed"),
        required_context=("map_state",),
    )
    @_guard
    def webgis_series_production_submit(
        series_id: str = "",
        spec_version: Optional[int] = None,
        session_id: str = "",
    ) -> dict:
        from app.core.database import SessionLocal

        from app.services.map_series.errors import MapSeriesError
        from app.services.map_series.freeze import build_freeze_plan
        from app.services.map_series.store import get_series, map_series_visible
        from app.services.series_production.service import submit_series_production
        from app.services.series_production.submit import (
            freeze_plan_from_series_payload,
        )

        owner, org_id, _role = _actor_context()
        with SessionLocal() as db:
            series = get_series(db, str(series_id))
            if not map_series_visible(series, owner, org_id):
                return {"success": False, "code": "MAP_SERIES_NOT_FOUND",
                        "message": "map series not found",
                        "correction_hint": "系列不存在或对当前调用者不可见"}

        async def _freeze():
            with SessionLocal() as db:
                return await build_freeze_plan(
                    db, str(series_id), spec_version=spec_version,
                    user_id=owner)

        try:
            freeze_payload = asyncio.run(_freeze())
        except MapSeriesError as exc:
            return {"success": False, "code": "MAP_SERIES_ERROR",
                    "message": str(exc)[:240],
                    "correction_hint": "检查系列成员与规范 pin 后重试"}

        unresolvable = freeze_payload.get("unresolvable_member_ids") or []
        if unresolvable:
            return {
                "success": False,
                "code": "MEMBERS_UNRESOLVABLE",
                "message": (
                    f"{len(unresolvable)} 个成员会话不可解析（无权威 MapSpec）："
                    + ",".join(str(x)[:24] for x in unresolvable[:6])
                ),
                "correction_hint": "先补齐成员会话或将其 enabled=false 后重试",
            }
        plan = freeze_plan_from_series_payload(freeze_payload)
        result = submit_series_production(
            plan, freeze_payload=freeze_payload,
            origin_session_id=(str(session_id)[:255] if session_id else None),
            series_name=str(getattr(series, "name", "") or ""),
        )
        return {
            "success": True,
            "status": result.get("status", "submitted"),
            "job_id": result.get("job_id"),
            "idempotent_reuse": result.get("idempotent_reuse", False),
            "member_count": len(plan.members),
            "freeze_plan_digest": plan.plan_digest(),
            "poll_hint": f"GET /api/v1/tasks/jobs/{result.get('job_id')}",
        }


__all__ = ["register_series_production_tools"]
