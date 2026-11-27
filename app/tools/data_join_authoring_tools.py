"""G38：数据关联与空间富集的 Agent 工具面（Pi 经 extensions → dispatch 到此）。

工具语义（reviewer 重点）：
- 全部经 ``app.services.data_join_authoring.service`` 单一编排面 —— 与
  REST 同一权限/同一收据/同一幂等键；
- session_id 由 ToolRegistry 从已授权上下文注入（模型不能指定别的
  session）；计划/运行归属注入 session，跨会话访问一律 not_found；
- 结果只带有界证据与真实可读 ref（诊断计数/样本 ≤50、结果页 ≤200 行、
  收据摘要）；绝不把全表塞进上下文；
- execute 返回 job 时，status 工具区分 queued/running/completed/failed/
  cancelled 与收据缺失（receipt_missing）；有界等待 wait_max_s 内联收敛
  （G23 import 工具同款语义）。
"""

import asyncio
from typing import Any, Dict, Optional

from app.services.data_join_authoring import service
from app.services.data_join_authoring.errors import DataJoinError
from app.tools.registry import ToolRegistry, tool

_MAX_PLAN_BLOB = 32 * 1024


def _fail(exc: DataJoinError) -> Dict[str, Any]:
    out = exc.payload()
    out["error"] = out.get("error_code", exc.code)
    return out


def _actor(session_id: str) -> Dict[str, Any]:
    """工具平面 actor：JobOrigin 上下文 + 受信 session（注入即授权）。"""
    from app.services.jobs.context import current_origin

    origin = current_origin()
    return {
        "user_id": origin.owner_id if origin else None,
        "org_id": origin.org_id if origin else None,
        "owner_token": origin.owner_token if origin else None,
        "trusted_session_id": session_id,
    }


def register_data_join_authoring_tools(registry: ToolRegistry) -> None:
    """注册 G38 数据关联工具族。"""

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_join_describe",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["数据关联", "join", "源检查", "字段", "schema"],
        capabilities=["data_join_authoring"],
        latency_class="medium",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("invalid_args", "missing_data"),
        description=(
            "检查一个候选关联源（session ref / 项目产物修订）：行数、字段与"
            "类型、CRS、几何类型、内容版本指纹。关联前先用它在两源上确认"
            "连接键字段存在且类型可匹配。只接受当前会话有权访问的源。"
        ),
        param_descriptions={
            "source": "源锚 {kind: session_ref|fabric_parquet|project_artifact|table_rows, ...}",
        },
    )
    async def webgis_data_join_describe(
        source: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            card = await service.describe_source(session_id, source, _actor(session_id))
        except DataJoinError as exc:
            return _fail(exc)
        card["source"].pop("completeness", None)
        return card

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_join_plan_upsert",
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        tags=["数据关联", "join", "计划", "draft"],
        capabilities=["data_join_authoring"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("invalid_args", "superseded", "validation_error"),
        description=(
            "创建或更新关联计划草稿（不执行、不改源数据、不改地图）。计划覆盖："
            "两源锚、模式（attribute / spatial_point_in_polygon）、连接键与显式"
            "变换、保留侧、输出基数、空值政策、空间谓词与多命中策略、字段映射、"
            "聚合规则。返回 plan_id/revision/normalized_digest —— 预览与执行都"
            "基于该 digest。plan_id 为空 = 新建；否则按 expected_revision CAS 更新。"
        ),
        param_descriptions={
            "plan": "JoinPlan 计划体（schema_version=1）",
            "plan_id": "已有计划 id（更新时必填）",
            "expected_revision": "更新时的 CAS 修订号（新建忽略）",
        },
    )
    async def webgis_data_join_plan_upsert(
        plan: Dict[str, Any],
        plan_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            if plan_id:
                if expected_revision is None:
                    return {
                        "error": "invalid_args",
                        "message": "更新计划必须携带 expected_revision",
                    }
                view = await service.update_plan(
                    session_id, plan_id, plan,
                    expected_revision=int(expected_revision),
                    actor=_actor(session_id))
            else:
                view = await service.create_plan(
                    session_id, plan, actor=_actor(session_id))
        except DataJoinError as exc:
            return _fail(exc)
        return {
            "plan_id": view["plan_id"],
            "revision": view["revision"],
            "normalized_digest": view["normalized_digest"],
            "status": view["status"],
            "title": view["title"],
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_join_preview",
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        tags=["数据关联", "join", "诊断", "基数", "未匹配"],
        capabilities=["data_join_authoring"],
        latency_class="slow",
        memory_class="medium",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("invalid_args", "validation_error", "missing_data", "budget_exceeded"),
        description=(
            "对计划做全量诊断预览（与执行同一内核）：两侧总行数/空键/重复键/"
            "不同键、预计匹配对、左/右未匹配数、有界异常样本（每桶 ≤50）。"
            "基数违规（如声明多对一但右键重复）会以 blocked + 全量计数返回。"
            "预览与执行绑定同一 normalized_digest + 双源指纹 —— 源或规则变化后"
            "旧预览失效。"
        ),
        param_descriptions={
            "plan_id": "计划 id",
            "expected_revision": "计划修订号（CAS）",
        },
    )
    async def webgis_data_join_preview(
        plan_id: str,
        expected_revision: int,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            result = await service.preview_plan(
                session_id, plan_id, expected_revision=int(expected_revision),
                actor=_actor(session_id))
        except DataJoinError as exc:
            return _fail(exc)
        result.pop("plan", None)  # 计划本体已在调用方；不重复回传
        sample = result.get("sample_rows") or []
        result["sample_rows"] = sample[:50]
        result["sample_row_count"] = len(sample)
        return result

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_join_execute",
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        tags=["数据关联", "join", "执行", "富集", "durable job"],
        capabilities=["data_join_authoring"],
        latency_class="slow",
        memory_class="heavy",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("invalid_args", "superseded", "missing_data", "budget_exceeded"),
        description=(
            "按计划执行关联（冻结双源快照复核 → durable job）：完整派生成果"
            "写入受权载体并登记 ref:data-join/<run_id>，收据含完整行数/匹配"
            "基数/输入版本/内容摘要。同计划同源版本的重复执行幂等收敛到同一"
            "成果。返回 run_id/job_id；用 webgis_data_join_status 轮询，"
            "webgis_data_join_result 分页读取。"
        ),
        param_descriptions={
            "plan_id": "计划 id",
            "expected_revision": "计划修订号（CAS）",
            "wait_max_s": "有界内联等待秒数（≤120；0=立即返回 job）",
        },
    )
    async def webgis_data_join_execute(
        plan_id: str,
        expected_revision: int,
        wait_max_s: int = 45,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        wait_max_s = max(0, min(int(wait_max_s), 120))
        try:
            view = await service.execute_plan(
                session_id, plan_id, expected_revision=int(expected_revision),
                actor=_actor(session_id), background=True)
        except DataJoinError as exc:
            return _fail(exc)
        run_id = view["run_id"]
        deadline = asyncio.get_event_loop().time() + wait_max_s
        while wait_max_s > 0 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.5)
            try:
                cur = await service.inspect_run(session_id, run_id)
            except DataJoinError as exc:
                # run 行已随 execute 落库：inspect 失败是真实错误而非瞬态，
                # 上报 typed 错误，不拿 execute 初始 view 冒充非终态结果
                return _fail(exc)
            if cur["status"] in ("completed", "failed", "cancelled"):
                return _bounded_run(cur)
        view.pop("receipt", None)
        return _bounded_run(view)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_join_status",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["数据关联", "join", "状态", "收据"],
        capabilities=["data_join_authoring"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("invalid_args", "missing_data"),
        description=(
            "查询一次关联执行的状态与有界收据：pending/running/completed/"
            "failed/cancelled；completed 时收据含完整行数、匹配基数、输入"
            "版本指纹、输出 ref/sha、工作 CRS。收据缺失或内容损坏 → "
            "receipt_missing（不虚报完成）。"
        ),
        param_descriptions={
            "run_id": "运行 id（dj-…）",
            "plan_id": "计划 id（可选：列其最近运行）",
        },
    )
    async def webgis_data_join_status(
        run_id: Optional[str] = None,
        plan_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            if run_id:
                view = await service.inspect_run(session_id, run_id)
                return _bounded_run(view)
            if plan_id:
                listing = await service.list_runs(session_id, plan_id=plan_id, limit=5)
                return {"runs": listing["runs"]}
            listing = await service.list_runs(session_id, limit=10)
            return {"runs": listing["runs"]}
        except DataJoinError as exc:
            return _fail(exc)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_join_result",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["数据关联", "join", "结果", "分页"],
        capabilities=["data_join_authoring"],
        latency_class="medium",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="table",
        failure_modes=("invalid_args", "missing_data"),
        description=(
            "分页读取一次已完成关联的输出行（≤200 行/页；行序 = 冻结快照输出"
            "序）。total_rows 为完整行数（不是本页计数）。完整数据经导出交付"
            "（G18）或输出 ref 消费。"
        ),
        param_descriptions={
            "run_id": "运行 id",
            "cursor": "分页游标（首页 0；用 next_cursor 翻页）",
            "limit": "页大小（≤200）",
        },
    )
    async def webgis_data_join_result(
        run_id: str,
        cursor: int = 0,
        limit: int = 50,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            page = await service.run_result_page(
                session_id, run_id, int(cursor), int(limit))
        except DataJoinError as exc:
            return _fail(exc)
        rows = page.get("rows") or []
        for r in rows:
            props = r.get("properties")
            if isinstance(props, dict):
                r["properties"] = {
                    k: (v if not isinstance(v, str) else v[:200])
                    for k, v in list(props.items())[:40]
                }
        return page

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_join_cancel",
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        tags=["数据关联", "join", "取消"],
        capabilities=["data_join_authoring"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("invalid_args", "missing_data"),
        description="请求取消一次进行中的关联执行（两段式：持久取消事实 + 进程内协作检查点）。",
        param_descriptions={"run_id": "运行 id"},
    )
    async def webgis_data_join_cancel(
        run_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            return await service.cancel_run(session_id, run_id)
        except DataJoinError as exc:
            return _fail(exc)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_data_join_list",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["数据关联", "join", "计划列表"],
        capabilities=["data_join_authoring"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("invalid_args",),
        description="列出当前会话的关联计划（含最近运行状态摘要）。",
    )
    async def webgis_data_join_list(
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            plans = await service.list_plans(session_id, limit=20)
            runs = await service.list_runs(session_id, limit=20)
        except DataJoinError as exc:
            return _fail(exc)
        out_plans = []
        for p in plans["plans"]:
            out_plans.append({
                "plan_id": p["plan_id"], "title": p["title"],
                "revision": p["revision"], "status": p["status"],
                "normalized_digest": p["normalized_digest"][:16],
                "has_preview": bool(p.get("preview")),
            })
        return {"plans": out_plans, "recent_runs": runs["runs"]}


def _bounded_run(view: Dict[str, Any]) -> Dict[str, Any]:
    """运行视图的有界投影（收据保留计数/版本/ref，剔除重复大对象）。"""
    receipt = view.get("receipt")
    out = {
        "run_id": view.get("run_id"),
        "plan_id": view.get("plan_id"),
        "status": view.get("status"),
        "job_id": view.get("job_id"),
        "error_code": view.get("error_code"),
        "output_rows": view.get("output_rows"),
        "output_ref": view.get("output_ref"),
        "content_invalidated": view.get("content_invalidated", False),
    }
    if isinstance(receipt, dict):
        counts = receipt.get("counts") or {}
        out["receipt_summary"] = {
            "output_kind": receipt.get("output_kind"),
            "output_rows": receipt.get("output_rows"),
            "matched_pairs": counts.get("matched_pairs"),
            "matched_left_rows": counts.get("matched_left_rows"),
            "unmatched_left_rows": counts.get("unmatched_left_rows"),
            "unmatched_right_rows": counts.get("unmatched_right_rows"),
            "working_crs": receipt.get("working_crs"),
            "plan_digest": receipt.get("plan_digest"),
            "duration_ms": receipt.get("duration_ms"),
        }
    return out
