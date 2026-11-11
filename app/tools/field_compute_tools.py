"""字段计算 Pi 工具面（G19 / WP10）—— 与 UI/API 走**同一个** service。

纪律（与公共条款 C 一致）：
- 模型只获得 schema/字段/单位的有界信息与预览摘要；完整数据留在受权 refs，
  绝不把要素集或秘密放进上下文；
- 公式由闭合 IR 契约校验，用户文字不能成为 host 代码（无 eval/exec/import）；
- 工具调用成功不等于产品验收：apply 返回新 ref、来源、公式摘要、单位、范围、
  行数、错误/未评估状态，以及下一步可用操作（含真实字段名供后续制图引用）；
- 不改 G12 的 Pi runtime / bridge、不改 G13 的资格政策；工具名 ``field_compute_*``
  与既有 registry 不冲突；桥不可用时由宿主如实 ``unavailable/503``。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.data_preparation.field_compute.contracts import (
    FUNCTION_SET_VERSION, IR_VERSION, MAX_PREVIEW_ROWS, MAX_ROW_OPS, MAX_ROWS,
    CompileError, ExecError,
)
from app.services.data_preparation.field_compute.functions import function_help
from app.services.data_preparation.field_compute import service
from app.tools.registry import ToolRegistry, tool
from app.tools.upload_tools import _resolve_session_id

logger = logging.getLogger(__name__)

_MAX_TOOL_SAMPLES = 5
_MAX_TOOL_FUNCTIONS = 40


class FieldComputeCatalogArgs(BaseModel):
    """只读契约发现（无 session 依赖）。"""


class FieldComputePreviewArgs(BaseModel):
    source_ref: str = Field(..., json_schema_extra={"ref_cursor": True})
    columns: List[Dict[str, Any]] = Field(..., min_length=1, max_length=8)
    field_units: Dict[str, str] = Field(default_factory=dict)
    scope: Dict[str, Any] = Field(default_factory=dict)
    timezone: str = "UTC"
    date_format: str = ""
    error_policy: str = "null"
    session_id: Optional[str] = None


class FieldComputeApplyArgs(BaseModel):
    plan_id: str
    expected_fingerprint: str = ""
    project_id: str = ""
    wait_max_s: int = Field(20, ge=0, le=600)
    session_id: Optional[str] = None


class FieldComputeResultArgs(BaseModel):
    output_ref: str = Field(..., json_schema_extra={"ref_cursor": True})
    session_id: Optional[str] = None


def _sid(session_id: Optional[str]) -> str:
    return _resolve_session_id(session_id) or ""


def _persistence_note(receipt) -> str:
    """持久化披露必须与实际保存结果一致（独立评审 P1-2）。

    G23：receipt 可能是 ComputeReceipt 对象，也可能是 durable job
    result_summary 里的 dict 形 —— 两种输入同一披露。
    """

    def _get(key: str):
        return getattr(receipt, key, None) if hasattr(receipt, "columns") else receipt.get(key)

    degraded = _get("degraded") or ()
    if "PROJECT_SAVE_FAILED" in degraded:
        return (" 项目清单保存失败（PROJECT_SAVE_FAILED）：成果仅为本会话 "
                "session ref，重开会话可能失效，不能宣称已可重开找回。")
    if _get("persistence") == "project_artifact":
        return " 该成果已登记到项目清单，可重开找回。"
    return " 该成果仅为本会话 session ref，重开会话可能失效。"


def _typed_error(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, CompileError):
        return {"success": False, "code": exc.code, "detail": exc.detail,
                "position": exc.position, "hint": exc.hint}
    if isinstance(exc, ExecError):
        return {"success": False, "code": exc.code, "detail": exc.detail}
    return {"success": False, "code": "UNEXPECTED", "detail": str(exc)[:200]}


def _receipt_payload(receipt: Any) -> dict:
    """ComputeReceipt（对象或 job result_summary 的 dict 形）→ 工具响应（BASE 同形）。"""
    is_obj = hasattr(receipt, "columns")

    def g(key: str, default=None):
        return getattr(receipt, key, default) if is_obj else receipt.get(key, default)

    raw_cols = g("columns") or []
    cols: List[dict] = [
        (
            {
                "name": c.name, "dtype": c.dtype, "unit": c.unit,
                "unit_status": c.unit_status,
                "measurement_kind": c.measurement_kind,
                "computed": c.computed, "nulls": c.nulls, "errors": c.errors,
                "error_samples": list(getattr(c, "error_samples", []) or []),
            }
            if hasattr(c, "name")
            else {
                "name": c.get("name"), "dtype": c.get("dtype"),
                "unit": c.get("unit"), "unit_status": c.get("unit_status"),
                "measurement_kind": c.get("measurement_kind"),
                "computed": c.get("computed"), "nulls": c.get("nulls"),
                "errors": c.get("errors"),
                "error_samples": list(c.get("error_samples") or []),
            }
        )
        for c in raw_cols
    ]
    derived = [
        {k: c[k] for k in
         ("name", "dtype", "unit", "unit_status", "measurement_kind",
          "computed", "nulls", "errors")}
        for c in cols
    ]
    error_samples = [e for c in cols for e in c["error_samples"]]
    names = ", ".join(str(c["name"]) for c in cols)
    return {
        "success": True,
        "status": "ready",
        "output_ref": g("output_ref"),
        "input_ref": g("input_ref"),
        "plan_id": g("plan_id"),
        "compute_fingerprint": g("compute_fingerprint"),
        "formula_summary": list(g("formula_summary") or []),
        "derived_fields": derived,
        "unit_summary": g("unit_summary"),
        "scope": g("scope"),
        "row_count": g("row_count"),
        "selected_rows": g("selected_rows"),
        "null_count": g("null_count"),
        "error_count": g("error_count"),
        "error_examples": error_samples[:_MAX_TOOL_SAMPLES],
        "precision_policy": g("precision_policy") or "not_evaluated",
        "persistence": g("persistence"),
        "descriptor_minted": g("descriptor_minted"),
        "degraded": list(g("degraded") or []),
        # 数据目标不制造地图验收；渲染/视觉面恒为未评估（无论行数）。
        "not_evaluated": ["全量视觉渲染", "地图渲染回执"],
        "guidance": (
            f"成果 ref {g('output_ref')} 已可读；新字段 {names}"
            " 可直接用于查表、分析与制图。下一轮若用户要求「用刚才的新字段做分级图」，"
            "请基于本结果中的真实 output_ref 与字段名调用既有制图工具，不要重新猜测字段。"
            + _persistence_note(receipt)),
        "next_actions": ["field_compute_result", "field_compute_status",
                         "create_thematic_map", "webgis_layer_upsert"],
    }


async def _job_snapshot(job_id: str) -> Optional[dict]:
    """读取 durable job 行快照（同步短事务；属性在 session 内取齐，
    commit 过期后不再触碰 ORM 对象）。"""
    from app.models.db_model import AnalysisTask
    from app.services.data_operations.runner import _sync_session

    try:
        numeric = int(job_id)
    except (TypeError, ValueError):
        return None
    with _sync_session() as db:
        row = db.get(AnalysisTask, numeric)
        if row is None:
            db.commit()
            return None
        snapshot = {
            "id": row.id, "status": str(row.status), "progress": row.progress,
            "progress_message": (row.progress_message or "")[:200],
            "error_trace": row.error_trace,
            "result_summary": row.result_summary,
            "session_id": row.session_id,
        }
        db.commit()
    return snapshot


async def _job_receipt_or_status(job_id: str) -> "tuple[bool, Optional[dict]]":
    """job 终态判定：完成 → (True, 收据响应)；失败/取消 → (True, typed 错误)；
    进行中 → (False, None)。"""
    row = await _job_snapshot(job_id)
    if row is None:
        return True, {"success": False, "code": "JOB_NOT_FOUND",
                      "error": f"job {job_id} 不存在"}
    status = str(row["status"])
    if status in ("pending", "queued", "running", "cancelling"):
        return False, None
    if status == "completed":
        summary = row["result_summary"] if isinstance(row["result_summary"], dict) else {}
        return True, _receipt_payload(summary)
    error_line = (row["error_trace"] or "").splitlines()[0][:200] if row["error_trace"] else ""
    if status == "cancelled":
        return True, {"success": False, "code": "CANCELLED",
                      "error": f"执行已取消（job {job_id}）；计划可重提"}
    return True, {"success": False, "code": "JOB_INCOMPLETE",
                  "error": f"执行未成功（job {job_id} 终态 {status}）{error_line}",
                  "retry_hint": "failed/stale 可重提同一计划（幂等键释放）"}


def register_field_compute_tools(registry: ToolRegistry) -> None:
    """注册字段计算工具集（独立模块，避免与并行任务改同一文件）。"""

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="field_compute_catalog",
        args_model=FieldComputeCatalogArgs,
        side_effect="pure",
        network=False,
        deterministic=True,
        output_semantic_type="schema",
        tags=["字段计算", "计算列", "单位", "catalog"],
        capabilities=["field_compute"],
        latency_class="medium",
        memory_class="light",
        description=(
            "列出类型化字段计算工作台的函数集版本、闭合表达式函数（数值/条件/"
            "文本/代码/日期/单位/几何）、支持的作用范围与错误策略、资源上限。"
            "用于在设计计算列之前确认能力边界；不读取数据。"
        ),
    )
    async def field_compute_catalog() -> dict:
        return {
            "success": True,
            "function_set_version": FUNCTION_SET_VERSION,
            "ir_version": IR_VERSION,
            "functions": function_help()[:_MAX_TOOL_FUNCTIONS],
            "scope_modes": ["all_rows", "selection", "predicate"],
            "error_policies": ["null", "abort"],
            "limits": {"max_rows": MAX_ROWS, "max_ops_per_row": MAX_ROW_OPS,
                       "preview_rows": MAX_PREVIEW_ROWS},
            "guidance": "表达式是闭合 IR，不是代码；未知函数/import/文件访问一律拒绝。"
                        "单位必须由 field_units 显式声明，字段名只是提示。",
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="field_compute_preview",
        args_model=FieldComputePreviewArgs,
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        tags=["字段计算", "预览", "计算列", "preview"],
        capabilities=["field_compute"],
        latency_class="medium",
        memory_class="medium",
        description=(
            "在已登记的数据 ref 上编译安全表达式并预览计算列：返回字段/类型/单位/"
            "范围/样本 before-after/null 与 error 计数/预算预估，以及供确认执行的 "
            "params_fingerprint。预览不改变原数据，也不产出公开成果 ref。"
            "✅ 用于：人口密度、变化率、条件分类、行政区代码规范化、时间字段提取"
            "等「先预览再生成」场景。公式用 {字段名} 安全引用中文/带空格字段名。"
        ),
        param_descriptions={
            "source_ref": "会话内数据 ref（ref:…），原件不可变",
            "columns": "[{name, expression, declared_unit?}]（≤8 列，声明顺序即计算顺序，只允许引用之前的列）",
            "field_units": "{字段名: canonical unit}（user-declared，字段名不是单位证据）",
            "scope": "all_rows / predicate / selection（selection 必须完整且有稳定 ID + revision）",
            "timezone": "显式时区（UTC / +08:00 等；不猜本地时区）",
            "date_format": "strptime 格式（parse_datetime 单参数时使用）",
            "error_policy": "null（错误行结果=null 并披露）或 abort（遇错终止整次）",
            "session_id": "会话 id（通常运行时注入）",
        },
    )
    async def field_compute_preview(
        source_ref: str,
        columns: List[Dict[str, Any]],
        field_units: Optional[Dict[str, str]] = None,
        scope: Optional[Dict[str, Any]] = None,
        timezone: str = "UTC",
        date_format: str = "",
        error_policy: str = "null",
        session_id: Optional[str] = None,
    ) -> dict:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "NO_SESSION",
                    "error": "缺少有效会话（需要在会话上下文中调用）"}
        if not source_ref.startswith("ref:"):
            return {"success": False, "code": "INVALID_REF",
                    "error": "source_ref 必须是会话内数据 ref（ref:…）"}
        try:
            created = await service.create_plan({
                "session_id": sid, "source_ref": source_ref, "owner": "agent",
                "columns": columns, "scope": scope or {},
                "field_units": field_units or {}, "timezone": timezone,
                "date_format": date_format, "error_policy": error_policy,
            })
            pv = await service.preview(created["plan"].plan_id, sid)
        except (CompileError, ExecError, ValueError) as exc:
            return _typed_error(exc)
        plan = created["plan"]
        return {
            "success": True,
            "status": "previewed",
            "plan_id": plan.plan_id,
            "compute_fingerprint": plan.compute_fingerprint,
            "input_ref": source_ref,
            "data_shape": created["data_shape"],
            "kind": "sample",
            "sampled_rows": pv["sampled_rows"],
            "total_rows": pv["total_rows"],
            "truncated": pv["truncated"],
            "columns": [{k: c[k] for k in (
                "name", "formula", "dtype", "unit", "unit_status",
                "measurement_kind", "computed", "nulls", "errors") if k in c}
                for c in pv["columns"]],
            "before_after": pv["before_after"][:_MAX_TOOL_SAMPLES],
            "null_count_sample": pv["null_count_sample"],
            "error_count_sample": pv["error_count_sample"],
            "error_samples": pv["error_samples"][:_MAX_TOOL_SAMPLES],
            "scope": pv["scope"],
            "unit_summary": pv["unit_summary"],
            "budget": pv["budget"],
            "degraded": pv["degraded"],
            "precision_policy": pv["precision_policy"],
            "confirmation": pv["confirmation"],
            "guidance": (
                "这是采样预览（sample/estimate），不是全量结果。请向用户展示"
                "单位/范围/错误摘要并确认；确认后以 field_compute_apply 执行，"
                "并把 confirmation.params_fingerprint 原样传入 expected_fingerprint。"),
            "next_actions": ["field_compute_apply", "field_compute_preview"],
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="field_compute_apply",
        args_model=FieldComputeApplyArgs,
        side_effect="artifact_creation",
        network=False,
        deterministic=False,
        produced_refs=["data"],
        output_semantic_type="ref",
        result_size_policy="ref_offload",
        tags=["字段计算", "派生", "计算列", "apply"],
        capabilities=["field_compute"],
        latency_class="medium",
        memory_class="medium",
        description=(
            "执行已预览的字段计算计划：按固定版本快照全量派生，产出新的已登记数据 "
            "ref（原件保留），登记成果/谱系并如实披露持久化级别、单位、范围、行数"
            "与错误状态。执行成功不等于地图成功：数据目标只需结果可读可查。"
            "执行是持久任务（durable job）：通常在 wait_max_s 内完成并直接返回收据，"
            "大数据集超时后返回 job 句柄，用 field_compute_status 查询，"
            "完成且产物可读才继续制图。"
            "✅ 用于：用户确认预览后生成可查询、可分析、可制图的计算列成果。"
        ),
        param_descriptions={
            "plan_id": "field_compute_preview 返回的 plan_id",
            "expected_fingerprint": "已看到预览的 confirmation.params_fingerprint（防止参数漂移）",
            "project_id": "可选：保存到项目（真实持久清单）；留空则仅 session ref，生命周期需如实披露",
            "wait_max_s": "同步等待上限秒数（默认 20；超过即返回 job 句柄）",
            "session_id": "会话 id（通常运行时注入）",
        },
    )
    async def field_compute_apply(
        plan_id: str,
        expected_fingerprint: str = "",
        project_id: str = "",
        wait_max_s: int = 20,
        session_id: Optional[str] = None,
    ) -> dict:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "NO_SESSION",
                    "error": "缺少有效会话"}
        import asyncio as _asyncio

        try:
            out = await service.execute_background(
                plan_id, sid, project_id=project_id, owner="agent",
                expected_fingerprint=expected_fingerprint)
        except (CompileError, ExecError, ValueError) as exc:
            return _typed_error(exc)
        if "receipt" in out:
            # 幂等短路：收据已就绪，直接组装（与 BASE 同形）
            return _receipt_payload(out["receipt"])
        job_id = str(out.get("job_id") or "")
        # 有界等待（不长时间占用工具调用）；轮询 durable job 终态
        loop = _asyncio.get_event_loop()
        deadline = loop.time() + max(0, int(wait_max_s))
        while loop.time() < deadline:
            await _asyncio.sleep(0.25)
            done, payload = await _job_receipt_or_status(job_id)
            if done:
                return payload
        return {
            "success": True,
            "status": "running",
            "job_id": job_id,
            "plan_id": plan_id,
            "message": "字段计算仍在进行（持久任务）",
            "guidance": (
                "请用 field_compute_status 查询 job 状态；完成且 output_ref 可读后"
                "再继续制图。"
            ),
            "next_actions": ["field_compute_status", "field_compute_result"],
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="field_compute_status",
        capabilities=["workspace_state_inspection"],
        side_effect="pure",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["字段计算", "任务", "状态", "job"],
        output_semantic_type="text",
        result_size_policy="bounded",
        failure_modes=["empty_result", "not_found"],
        description=(
            "查询字段计算持久任务状态（plan 维度）：job 终态、进度（已计算行/总行）、"
            "收据摘要。只有 job 完成且 output_ref 可读才返回 produced ref —— "
            "Agent 必须据此决定是否继续制图。"
        ),
        param_descriptions={
            "plan_id": "field_compute_preview 返回的 plan_id",
            "session_id": "会话 id（通常运行时注入）",
        },
    )
    async def field_compute_status(
        plan_id: str, session_id: Optional[str] = None
    ) -> dict:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "NO_SESSION",
                    "error": "缺少有效会话"}
        from app.models.db_model import AnalysisTask
        from app.services.data_operations.compute_jobs import find_active_compute_job
        from app.services.data_operations.runner import _sync_session

        active = find_active_compute_job(sid, plan_id)
        row = None
        with _sync_session() as db:
            q = (
                db.query(AnalysisTask)
                .filter(AnalysisTask.task_type == "field_compute_apply")
                .order_by(AnalysisTask.id.desc())
            )
            for candidate in q.limit(10):
                params = candidate.parameters if isinstance(candidate.parameters, dict) else {}
                if params.get("plan_id") == plan_id and candidate.session_id == sid:
                    row = {
                        "id": candidate.id, "status": str(candidate.status),
                        "progress": candidate.progress,
                        "progress_message": (candidate.progress_message or "")[:200],
                        "result_summary": candidate.result_summary,
                    }
                    break
            db.commit()
        if row is None and active is None:
            return {"success": True, "plan_id": plan_id, "job": None,
                    "message": "该计划没有持久任务记录（尚未执行或已清理）"}
        if row is None:
            return {"success": True, "plan_id": plan_id,
                    "job": {"job_id": str(active["job_id"]),
                            "status": active["status"]}}
        summary = row["result_summary"] if isinstance(row["result_summary"], dict) else {}
        completed = str(row["status"]) == "completed"
        output_ref = str(summary.get("output_ref") or "") if completed else ""
        readable = False
        if output_ref:
            from app.services.session_data import session_data_manager as _sdm

            readable = await _sdm.get(sid, output_ref) is not None
        return {
            "success": True,
            "plan_id": plan_id,
            "job": {
                "job_id": str(row["id"]),
                "status": str(row["status"]),
                "progress": row["progress"],
                "message": row["progress_message"],
                "retry_hint": "failed/stale 可重提同一计划（幂等键释放）",
            },
            "row_count": summary.get("row_count"),
            "computed_rows": summary.get("computed_rows"),
            "output_ref": output_ref if readable else None,
            "output_readable": readable,
            "guidance": (
                "成果 ref 可读，可继续制图。"
                if readable
                else "任务未完成或产物不可读；完成且可读后才继续制图。"
            ),
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="field_compute_result",
        args_model=FieldComputeResultArgs,
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["字段计算", "回读", "结果表"],
        capabilities=["field_compute"],
        latency_class="medium",
        memory_class="light",
        description=(
            "回读字段计算成果：验证新数据 ref 真实可读、字段存在、行数对账，"
            "返回有界样本（≤5 行）。用于生成后查表与后续制图前的结果确认。"
        ),
        param_descriptions={
            "output_ref": "field_compute_apply 返回的 output_ref",
            "session_id": "会话 id（通常运行时注入）",
        },
    )
    async def field_compute_result(
        output_ref: str,
        session_id: Optional[str] = None,
    ) -> dict:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "NO_SESSION",
                    "error": "缺少有效会话"}
        try:
            got = await service.get_result(sid, output_ref)
        except (CompileError, ExecError, ValueError) as exc:
            return _typed_error(exc)
        return {"success": True, **got,
                "guidance": "结果可读；可继续查表或用既有制图工具按真实字段做分级图。"}
