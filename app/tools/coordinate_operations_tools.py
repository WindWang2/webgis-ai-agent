"""G45：可审阅坐标操作的 Agent 工具面（Pi 经 extensions → dispatch 到此）。

工具语义（与 REST 同一 service/权限/收据/幂等键）：

- session_id 由 ToolRegistry 从已授权上下文注入（模型不能指定别的 session）；
- 候选只展示 + lock 只收服务器签发 candidate_id —— 模型无法注入 pipeline/
  格网参数（C11）；低精度/ballpark 选择必须显式 lock（不静默 fallback）；
- 回执只带有界证据：候选 ≤ 各 16、预览坐标对 ≤8、结果页 ≤200、收据摘要；
  全量坐标/属性/格网路径绝不进上下文（WP11 资源纪律）。
"""

import asyncio
from typing import Annotated, Any, Dict, Optional

from pydantic import Field

from app.services.coordinate_operations import service
from app.services.coordinate_operations.contracts import (
    CoordinateOperationError,
)
from app.tools.registry import ToolRegistry, tool


def _fail(exc: CoordinateOperationError) -> Dict[str, Any]:
    out = exc.payload()
    out["error"] = out.get("error_code")
    return out


def _actor(session_id: str) -> Dict[str, Any]:
    from app.services.jobs.context import current_origin

    origin = current_origin()
    return {
        "user_id": origin.owner_id if origin else None,
        "org_id": origin.org_id if origin else None,
        "owner_token": origin.owner_token if origin else None,
        "trusted_session_id": session_id,
    }


def register_coordinate_operations_tools(registry: ToolRegistry) -> None:
    """注册 G45 坐标操作工具族。"""

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_coordop_describe_source",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["坐标操作", "reproject", "源检查", "CRS"],
        capabilities=["coordinate_operations"],
        latency_class="medium",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=["invalid_args", "missing_data", "unauthorized"],
        description=(
            "检查一个候选重投影源（session FC ref / fabric-parquet / 项目产物"
            "修订）：行数、几何类型、字段、CRS 权威证据与规范描述（轴/单位/"
            "维度）。缺 CRS 证据或纯表格源会被 typed 拒绝 —— 本域不做坐标范围"
            "推断。重投影计划前先描述源。"
        ),
        param_descriptions={
            "source": "源锚 {kind: session_ref|fabric_parquet|project_artifact, ...}",
        },
    )
    async def webgis_coordop_describe_source(
        source: Annotated[
            Dict[str, Any], Field(json_schema_extra={"ref_cursor": True})],
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            card = await service.describe_source(
                session_id, source, _actor(session_id))
        except CoordinateOperationError as exc:
            return _fail(exc)
        card.pop("completeness", None)
        card["fields"] = (card.get("fields") or [])[:40]
        return card

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_coordop_crs_lookup",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["坐标操作", "CRS", "查询", "轴序", "单位"],
        capabilities=["coordinate_operations"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=["invalid_args"],
        description=(
            "解析一个 CRS 标识（如 EPSG:32650 / WKT 名）：官方轴名/方向/单位、"
            "维度支持（垂向/compound/动态 datum 会 typed 拒绝）、适用范围。"
            "选目标 CRS 前先确认其语义。"
        ),
        param_descriptions={"query": "CRS 标识（EPSG:xxxx 或名称）"},
    )
    async def webgis_coordop_crs_lookup(
        query: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return service.lookup_crs(query)
        except CoordinateOperationError as exc:
            return _fail(exc)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_coordop_list_operations",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["坐标操作", "reproject", "候选", "操作", "格网"],
        capabilities=["coordinate_operations"],
        latency_class="medium",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=["invalid_args", "missing_data", "unsupported"],
        description=(
            "列出冻结源 CRS → 目标 CRS 在当前离线环境的坐标操作候选：可执行项"
            "（含 accuracy/ballpark/适用范围）与不可执行最佳已知项（缺格网原因"
            "与官方来源）。unknown accuracy 显示 unknown 而非 0；候选排序不构成"
            "批准 —— 需用户/计划显式 lock 一个 candidate_id。"
        ),
        param_descriptions={
            "source": "源锚（先 describe 确认 CRS）",
            "target_crs": "目标 CRS 标识",
            "aoi": "可选 {west,south,east,north}（度；影响候选适用性排序）",
        },
    )
    async def webgis_coordop_list_operations(
        source: Annotated[
            Dict[str, Any], Field(json_schema_extra={"ref_cursor": True})],
        target_crs: str,
        aoi: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            out = await service.list_operations(
                session_id, source, target_crs, aoi, _actor(session_id))
        except CoordinateOperationError as exc:
            return _fail(exc)
        return out

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_coordop_plan_upsert",
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        tags=["坐标操作", "reproject", "计划", "draft"],
        capabilities=["coordinate_operations"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=["invalid_args", "superseded", "validation_error"],
        description=(
            "创建或更新坐标操作计划草稿（不执行、不改源）：源锚、目标 CRS、"
            "AOI 及其推导方式、Z 政策（preserve_unconverted=水平变换且 Z 原值"
            "保留）、域外政策（report|fail）。任何实质编辑 → revision+1 并使"
            "旧预览失效。plan_id 为空 = 新建。"
        ),
        param_descriptions={
            "plan": "计划体 {title, source, target_crs, aoi?, aoi_derivation, "
                    "z_policy, empty_geometry_policy, out_of_area_policy}",
            "plan_id": "已有计划 id（更新时必填）",
            "expected_revision": "更新时的 CAS 修订号",
        },
    )
    async def webgis_coordop_plan_upsert(
        plan: Annotated[
            Dict[str, Any], Field(json_schema_extra={"ref_cursor": True})],
        plan_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            if plan_id:
                if expected_revision is None:
                    return {"error": "invalid_args",
                            "message": "更新计划必须携带 expected_revision"}
                view = await service.update_plan(
                    session_id, plan_id, plan,
                    expected_revision=int(expected_revision),
                    actor=_actor(session_id))
            else:
                view = await service.create_plan(
                    session_id, plan, actor=_actor(session_id))
        except CoordinateOperationError as exc:
            return _fail(exc)
        return {
            "plan_id": view["plan_id"],
            "revision": view["revision"],
            "status": view["status"],
            "normalized_digest": view["normalized_digest"],
            "title": view["title"],
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_coordop_lock",
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        tags=["坐标操作", "锁定", "candidate", "冻结"],
        capabilities=["coordinate_operations"],
        latency_class="medium",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=["invalid_args", "superseded", "unsupported",
                        "conflict"],
        description=(
            "锁定一个候选操作：只接受服务器签发的 candidate_id（任意 pipeline"
            "串/格网路径会被拒绝）。锁定会重算候选集并冻结操作定义与 PROJ/"
            "pyproj/格网环境证据；环境漂移 → OPERATION_ENV_CHANGED。锁定后"
            "计划内容不可再改（改动 = 回 draft 新 revision）。"
        ),
        param_descriptions={
            "plan_id": "计划 id",
            "candidate_id": "list_operations 返回的服务器签发候选 id",
            "expected_revision": "CAS 修订号",
        },
    )
    async def webgis_coordop_lock(
        plan_id: str,
        candidate_id: str,
        expected_revision: int,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            view = await service.lock_plan(
                session_id, plan_id, candidate_id,
                expected_revision=int(expected_revision),
                actor=_actor(session_id))
        except CoordinateOperationError as exc:
            return _fail(exc)
        return {
            "plan_id": view["plan_id"],
            "revision": view["revision"],
            "status": view["status"],
            "operation": view["operation"],
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_coordop_preview",
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        tags=["坐标操作", "预览", "样本", "精度"],
        capabilities=["coordinate_operations"],
        latency_class="medium",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=("invalid_args", "superseded", "conflict"),
        description=(
            "锁定计划的有界预览：源顺序前 N 行 before/after 坐标对（≤8）、"
            "源/目标范围、非有限与操作适用范围外发现、锁定操作与 accuracy 语义。"
            "预览成功不等于全量成功，也不发布任何成果 ref。"
        ),
        param_descriptions={
            "plan_id": "计划 id",
            "expected_revision": "CAS 修订号",
            "sample_rows": "样本行数（≤50，默认 25）",
        },
    )
    async def webgis_coordop_preview(
        plan_id: str,
        expected_revision: int,
        sample_rows: int = 25,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            return await service.preview_plan(
                session_id, plan_id, expected_revision=int(expected_revision),
                actor=_actor(session_id), sample_rows=int(sample_rows))
        except CoordinateOperationError as exc:
            return _fail(exc)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_coordop_execute",
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        tags=["坐标操作", "执行", "重投影", "durable"],
        capabilities=["coordinate_operations"],
        latency_class="slow",
        memory_class="heavy",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=["invalid_args", "superseded", "budget_exceeded",
                        "execution_failed"],
        description=(
            "执行锁定计划的全量派生重投影（durable job，可取消/幂等）：冻结源"
            "→ 冻结操作逐顶点变换（all-or-nothing）→ 带 target CRS 的完整成果"
            "+ 独立 WGS84 显示副本。同 plan/源/操作重复执行幂等复用已验证成果。"
            "返回 run_id/job 轮询；用 webgis_coordop_status 查收据。"
        ),
        param_descriptions={
            "plan_id": "计划 id",
            "expected_revision": "CAS 修订号（锁定后的修订号）",
            "background": "默认 true（后台执行）",
        },
    )
    async def webgis_coordop_execute(
        plan_id: str,
        expected_revision: int,
        background: bool = True,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            view = await service.execute_plan(
                session_id, plan_id, expected_revision=int(expected_revision),
                actor=_actor(session_id), background=bool(background))
        except CoordinateOperationError as exc:
            return _fail(exc)
        return {
            "run_id": view["run_id"],
            "status": view["status"],
            "job_id": view.get("job_id"),
            "poll": view.get("poll"),
            "idempotent_reuse": view.get("idempotent_reuse", False),
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_coordop_status",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["坐标操作", "状态", "收据"],
        capabilities=["coordinate_operations"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=["invalid_args", "missing_data"],
        description=(
            "查 run 状态与有界收据（行数/顶点/输出 CRS/操作 manifest/环境证据/"
            "显示副本/预算实测）。wait_max 秒内有界等待终态（轮询 0.5s）。"
        ),
        param_descriptions={
            "run_id": "执行返回的 run id",
            "wait_max": "最多等待秒数（默认 0 = 立即返回）",
        },
    )
    async def webgis_coordop_status(
        run_id: str,
        wait_max: float = 0.0,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        deadline = asyncio.get_event_loop().time() + max(0.0, min(float(wait_max), 60.0))
        view: Dict[str, Any] = {}
        while True:
            try:
                view = await service.inspect_run(session_id, run_id)
            except CoordinateOperationError as exc:
                return _fail(exc)
            if view["status"] in ("completed", "failed", "cancelled") or \
                    asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(0.5)
        receipt = view.get("receipt") or {}
        return {
            "run_id": run_id,
            "status": view["status"],
            "error_code": view.get("error_code"),
            "output_ref": view.get("output_ref"),
            "receipt": {
                k: receipt.get(k) for k in (
                    "rows_total", "rows_transformed", "output_crs",
                    "total_vertices", "operation", "display", "output_ref",
                    "budget_measured", "z_policy")
            } if receipt else None,
        }

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_coordop_result",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        tags=["坐标操作", "结果", "分页"],
        capabilities=["coordinate_operations"],
        latency_class="medium",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=["invalid_args", "missing_data"],
        description=(
            "完成 run 的结果分页（属性 + 几何摘要 + 首坐标；≤200 行/页）。"
            "完整成果经输出 ref（GeoJSON/GPKG 交付）获取，不在上下文展开全量。"
        ),
        param_descriptions={
            "run_id": "run id",
            "cursor": "分页游标（默认 0）",
            "limit": "每页行数（≤200）",
        },
    )
    async def webgis_coordop_result(
        run_id: str,
        cursor: int = 0,
        limit: int = 50,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            return await service.run_result_page(
                session_id, run_id, int(cursor), int(limit))
        except CoordinateOperationError as exc:
            return _fail(exc)

    @tool(
        registry,
        tier=2,
        domains=["dataset"],
        name="webgis_coordop_cancel",
        side_effect="state_mutation",
        network=False,
        deterministic=True,
        tags=["坐标操作", "取消"],
        capabilities=["coordinate_operations"],
        latency_class="fast",
        memory_class="light",
        result_size_policy="bounded",
        output_semantic_type="json",
        failure_modes=["invalid_args", "missing_data"],
        description="请求取消一个执行中的坐标操作 run（协作取消；已终态则幂等返回）。",
        param_descriptions={"run_id": "run id"},
    )
    async def webgis_coordop_cancel(
        run_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not session_id:
            return {"error": "session_required", "message": "缺少 session_id"}
        try:
            return await service.cancel_run(session_id, run_id)
        except CoordinateOperationError as exc:
            return _fail(exc)
