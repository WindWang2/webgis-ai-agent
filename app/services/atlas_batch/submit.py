"""Atlas batch 提交入口 —— REST 路由与 agent 工具共用的单一天然入口。

职责（提交线程，无渲染）：

1. ``AtlasJobSpec`` 构造 + 语义校验（typed 400 证据）；
2. committed spec 存在性 + **dry-run 规划**（纯规划无渲染，毫秒级）——
   分页策略对当前数据不成立（如 category 属性无合法值/无内联图层）在
   提交时即给 typed 拒绝，而不是 job 跑起来才失败；
3. ``submit_durable_job``（幂等键 = 规范化参数；双击/重连复用同一 job 行）。

红线：不做渲染、不阻塞 worker；失败码与 executor 对齐（同一词表）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.lib.cartography.atlas_job_spec import (
    ATLAS_JOB_SPEC_VERSION,
    AtlasJobSpec,
    ClassificationScope,
)
from app.lib.cartography.mapspec_schema import MapSpecSchemaError

logger = logging.getLogger(__name__)

ATLAS_TASK_TYPE = "atlas_export"
ATLAS_DISPLAY_NAME = "批量图册导出"


def build_atlas_job_spec(
    *,
    session_id: str,
    atlas_policy: Dict[str, Any],
    classification_scope: str = "shared",
    target_dpi: int = 300,
    title: str = "",
    simulated_data: bool = False,
) -> AtlasJobSpec:
    """提交参数 → AtlasJobSpec（构造/语义校验；失败抛 MapSpecSchemaError）。"""
    try:
        scope = ClassificationScope(str(classification_scope or "shared"))
    except ValueError:
        raise MapSpecSchemaError(
            "atlas_scope_invalid",
            f"classification_scope 非法: {classification_scope!r}（shared|local）")
    try:
        spec = AtlasJobSpec(
            spec_version=ATLAS_JOB_SPEC_VERSION,
            session_id=str(session_id or ""),
            atlas=atlas_policy,
            classification_scope=scope,
            target_dpi=int(target_dpi or 300),
            title=str(title or ""),
            simulated_data=bool(simulated_data),
        )
        spec.validate_semantics()
    except MapSpecSchemaError:
        raise
    except Exception as ex:  # noqa: BLE001 — pydantic 校验面 → typed 400
        raise MapSpecSchemaError("atlas_policy_invalid", f"atlas 参数非法: {ex}") from ex
    return spec


def dry_run_plan(spec: AtlasJobSpec, document: Dict[str, Any]) -> Dict[str, Any]:
    """提交期可行性预检（纯规划）：页数/降级/首末页 id（有界摘要）。

    抛 ``MapSpecSchemaError`` = 策略对当前数据不成立（提交即 typed 拒绝）。
    """
    from app.services.atlas_batch.planner import plan_atlas_binding

    plan = plan_atlas_binding(spec, document)
    bindings = plan.bindings
    return {
        "page_count": len(bindings),
        "cover": bool(spec.atlas.include_cover),
        "first_page_id": bindings[0].page_id if bindings else "",
        "last_page_id": bindings[-1].page_id if bindings else "",
        "degradations": plan.ir.degradations,
    }


def submit_atlas_job(
    spec: AtlasJobSpec,
    *,
    origin_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """提交 durable job（幂等）；返回 submit_durable_job 的结果 dict。

    ``origin_session_id``：REST 路由传入已验证归属的 session（与 spec 同值）；
    agent 工具路径由 origin contextvar 携带（submit_durable_job 内部消费）。
    """
    from app.services.atlas_batch.tasks import run_atlas_export
    from app.services.jobs.lifecycle import JobKind
    from app.services.jobs.submit import submit_durable_job

    return submit_durable_job(
        celery_task=run_atlas_export,
        task_type=ATLAS_TASK_TYPE,
        display_name=f"{ATLAS_DISPLAY_NAME} · {spec.atlas.atlas_title or spec.session_id[:12]}",
        params=spec.job_params(),
        kind=JobKind.atlas_export,
        session_id=origin_session_id or spec.session_id,
        idempotent=True,
    )


__all__ = [
    "ATLAS_TASK_TYPE",
    "ATLAS_DISPLAY_NAME",
    "build_atlas_job_spec",
    "dry_run_plan",
    "submit_atlas_job",
]
