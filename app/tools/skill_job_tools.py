"""G30 技能作业工具面（prepare / start / status）—— Pi @tool 与 UI 同契约。

三个工具（tier 2，注册即进 Pi 动态面：tool_search 可发现、webgis_execute
同管线直呼）：

- ``gis_skill_job_prepare``：零业务副作用 prepared call（typed 拒绝码 +
  有界计划预览；记录 skill_selected 选择证据，仿知识工具先例）；
- ``gis_skill_job_start``：消费 prepared_id（或直接携带 prepared 内容重算
  幂等键）启动 durable 执行 —— 有界等待闭环；绑定当前 turn（JobOrigin），
  不开新 turn、不递归 prompt；
- ``gis_skill_job_status``：按 job id / 会话查运行状态与有界 run 记录。

生命周期红线：start 在 Pi 活跃工具回调内运行时，经 current_origin 继承
session/turn 关联；执行体在后台 task 逐步经 ToolDispatchService 派发 ——
与 webgis_execute 同一 owner/scope/side-effect 闸。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class SkillJobPrepareArgs(BaseModel):
    skill_id: str = Field(..., description="技能 id（目录/检索获得，如 validate_spatial_data）")
    skill_version: str = Field("", description="显式版本（空=当前版本；不匹配即 typed 拒绝）")
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="typed 参数（名称必须与技能声明一致；缺省值自动兜底并标注来源）")
    bindings: List[Dict[str, Any]] = Field(
        default_factory=list,
        description='数据角色绑定 [{"role":"subject","ref":"ref:geojson-..."}]；inline 数据用 {"role":..,"inline":FeatureCollection}')
    session_id: str = Field("", description="会话 id（解析 ref 必需）")


class SkillJobStartArgs(BaseModel):
    prepared_id: str = Field(..., description="prepare 返回的 prepared id（一次性消费）")
    session_id: str = Field("", description="会话 id（校验 prepared 归属）")
    wait_timeout_s: int = Field(
        45, description="有界等待秒数（超时=仍在运行，用 status 轮询；0=立即返回）")


class SkillJobStatusArgs(BaseModel):
    job_id: str = Field("", description="durable job id（空则按 session 列出最近运行）")
    session_id: str = Field("", description="会话 id")


def register_skill_job_tools(registry: ToolRegistry):
    """注册技能作业执行工具（tier 2）。"""

    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="gis_skill_job_prepare",
        description=(
            "准备一个可执行 GIS 技能作业（零副作用）。输入技能 id+版本、typed 参数"
            "与数据角色绑定（会话 ref），返回 prepared_id + 有界计划预览"
            "（将调用的真实工具、读写性质、证据覆盖与缺口）。"
            "\n何时用：用户明确选择了某个技能版本并提供了数据/参数；预览确认后用 "
            "gis_skill_job_start 启动。参数/字段/单位/资格不符会得到 typed 拒绝码。"
        ),
        args_model=SkillJobPrepareArgs,
        side_effect="state_mutation", deterministic=True, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("GIS", "技能", "skill", "作业", "prepare", "execution"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    async def gis_skill_job_prepare(
        skill_id: str, skill_version: str = "",
        params: Optional[Dict[str, Any]] = None,
        bindings: Optional[List[Dict[str, Any]]] = None,
        session_id: str = "",
    ) -> dict:
        from app.lib.runtime.context import current_runtime_context
        from app.services.gis_harness.skill_jobs.prepare import prepare_skill_job
        from app.services.gis_harness.skill_jobs.store import prepared_store

        sid = session_id or ""
        if not sid:
            ctx = current_runtime_context()
            sid = str(getattr(ctx, "session_id", "") or "") if ctx else ""
        try:
            result = await prepare_skill_job(
                session_id=sid, skill_id=skill_id, skill_version=skill_version,
                params=params or {}, bindings=bindings or [])
        except Exception as e:  # SkillJobErrorCode 及资产装载失败都typed返回
            code = getattr(e, "code", "PREPARE_FAILED")
            return {
                "success": False,
                "code": str(code),
                "message": str(getattr(e, "message", e))[:300],
                "detail": getattr(e, "detail", None) or {},
            }
        prepared_store.put(result.prepared, result.inline_payloads)
        # 选择证据（审计面；仿 gis_skill_search 先例 —— 不是执行凭证）
        try:
            from app.services.gis_harness.skills.evidence import SkillEvidenceRecord
            from app.tools.skill_library_tools import get_skill_evidence_recorder

            get_skill_evidence_recorder().record(SkillEvidenceRecord(
                event="skill_selected",
                skill_id=result.prepared.skill_id,
                skill_version=result.prepared.skill_version,
                selection_reason=["explicit_user_selection"],
                confidence=1.0,
                notes=f"prepared:{result.prepared.prepared_id}"[:200],
            ))
        except Exception:  # noqa: BLE001
            logger.debug("[skill-job] selection evidence skipped", exc_info=True)
        return {
            "success": True,
            "prepared_id": result.prepared.prepared_id,
            "skill": {
                "id": result.prepared.skill_id,
                "version": result.prepared.skill_version,
                "digest": result.prepared.skill_digest[:16],
            },
            "idempotency_key": result.prepared.idempotency_key,
            "preview": result.prepared.preview,
            "next_action": (
                "审阅 preview 后调用 gis_skill_job_start(prepared_id=...) 启动执行"),
        }

    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="gis_skill_job_start",
        description=(
            "启动已准备的技能作业（幂等：同内容重复调用复用同一 durable run）。"
            "逐步经统一工具派发执行既有生产工具，绑定当前 turn；有界等待返回"
            "运行状态与有界 run 记录（步骤分支/产物 refs/证据覆盖）。"
            "\n何时用：prepare 成功且预览符合用户意图。取消经任务中心既有取消面。"
        ),
        args_model=SkillJobStartArgs,
        side_effect="state_mutation", deterministic=False, network=False,
        latency_class="medium", memory_class="medium", scale_class="medium",
        tags=("GIS", "技能", "skill", "作业", "执行", "run"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    async def gis_skill_job_start(
        prepared_id: str, session_id: str = "", wait_timeout_s: int = 45,
    ) -> dict:
        from app.lib.runtime.context import current_runtime_context
        from app.services.gis_harness.skill_jobs.runner import (
            SkillJobOwner,
            start_skill_job,
        )
        from app.services.gis_harness.skill_jobs.store import prepared_store

        sid = session_id or ""
        ctx = current_runtime_context()
        if not sid and ctx is not None:
            sid = str(getattr(ctx, "session_id", "") or "")
        entry = prepared_store.pop(prepared_id, session_id=sid) if sid \
            else prepared_store.pop(prepared_id)
        if entry is None:
            # 会话不符与过期同形回执（不泄露 prepared 归属；不消费他人意图）
            return {
                "success": False, "code": "PREPARED_EXPIRED",
                "message": "prepared_id 不存在/已过期/已被消费/归属另一会话 —— 重新调用 prepare",
            }
        prepared, inline_payloads = entry
        owner = SkillJobOwner(
            session_id=prepared.session_id,
            turn_id=str(getattr(ctx, "turn_id", "") or "") if ctx else "",
            tool_call_id=str(getattr(ctx, "tool_call_id", "") or "") if ctx else "",
        )
        try:
            view = await start_skill_job(
                prepared, inline_payloads, owner=owner,
                wait_timeout_s=float(max(0, min(wait_timeout_s, 120))),
                session_busy_check=False)  # Pi 回调内：当前 turn 即合法上下文
        except Exception as e:  # noqa: BLE001 — typed 拒绝统一回执
            code = getattr(e, "code", "START_FAILED")
            return {
                "success": False, "code": str(code),
                "message": str(getattr(e, "message", e))[:300],
                "detail": getattr(e, "detail", None) or {},
            }
        out = {"success": True, **view}
        if view.get("status") in ("running", "pending", "queued"):
            out["note"] = "仍在运行：用 gis_skill_job_status(job_id=...) 轮询"
        return out

    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="gis_skill_job_status",
        description=(
            "查询技能作业运行状态（job_id 定位；缺省列出会话最近运行）。"
            "返回 durable 状态、进度、有界 run 记录（步骤/产物 refs/证据覆盖）——"
            "浏览器刷新后的回看权威。"
        ),
        args_model=SkillJobStatusArgs,
        side_effect="cacheable_read", deterministic=True, network=False,
        latency_class="fast", memory_class="light", scale_class="small",
        tags=("GIS", "技能", "skill", "作业", "状态"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object", result_size_policy="bounded",
    )
    async def gis_skill_job_status(job_id: str = "", session_id: str = "") -> dict:
        from app.lib.runtime.context import current_runtime_context
        from app.services.gis_harness.skill_jobs.runner import (
            get_skill_job_view,
            list_skill_job_views,
        )

        sid = session_id or ""
        if not sid:
            ctx = current_runtime_context()
            sid = str(getattr(ctx, "session_id", "") or "") if ctx else ""
        if job_id:
            try:
                view = await get_skill_job_view(int(job_id))
            except ValueError:
                return {"success": False, "code": "BAD_JOB_ID",
                        "message": "job_id 必须是数字"}
            if view is None:
                return {"success": False, "code": "JOB_NOT_FOUND",
                        "message": f"job {job_id} 不存在"}
            # review P1-2：单读归属验证 —— 行内会话必须与当前回调会话一致
            #（裸 id 枚举不得成为跨会话旁路）。
            row_session = str(view.get("session_id") or "")
            if not row_session or (sid and row_session != sid):
                return {"success": False, "code": "JOB_NOT_FOUND",
                        "message": f"job {job_id} 不存在"}
            return {"success": True, "run": view}
        if not sid:
            return {"success": False, "code": "SESSION_REQUIRED",
                    "message": "需要 job_id 或 session_id"}
        runs = await list_skill_job_views(session_id=sid)
        return {"success": True, "runs": runs[:10]}
