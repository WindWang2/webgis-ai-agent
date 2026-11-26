"""栅格采样 Pi 工具面（G39 / WP10）—— 与 UI/API 走同一个 service。

纪律（公共条款 C）：
- 模型只获得有界摘要、状态、数值语义与完整成果 refs；绝不把几十万行
  放进上下文；
- session 由 ToolRegistry 从已授权请求上下文注入（_resolve_session_id），
  模型指定他人 session 即拒绝；
- execute 是 durable 等待语义（G23 契约）：wait_max_s 内收敛收据即返回，
  超时返回 job 句柄 + status 工具指引 —— 绝不把 queued/running 说成完成；
- 工具名 ``webgis_raster_sampling_*`` 与既有 registry 不冲突。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional


from app.services.raster_sampling import service
from app.services.raster_sampling.contracts import RasterSamplingError
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.tools.upload_tools import _resolve_session_id

logger = logging.getLogger(__name__)

class RasterSamplingPlanArgs(BaseModel):
    """plan 工具参数（ref 游标声明：points/lines ref 不被透明解引用）。"""

    mode: str
    points_ref: str = Field("", json_schema_extra={"ref_cursor": True})
    lines_ref: str = Field("", json_schema_extra={"ref_cursor": True})
    line_feature_id: str = ""
    station_crs: str = "EPSG:4326"
    distance_mode: str = "geodesic"
    station_step: Optional[float] = None
    include_vertices: bool = True
    method: str = "source_pixel"
    name: str = ""
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    session_id: Optional[str] = None


class RasterSamplingPreviewArgs(BaseModel):
    plan_id: str
    expected_revision: Optional[int] = None
    limit: int = 16
    session_id: Optional[str] = None


class RasterSamplingExecuteArgs(BaseModel):
    plan_id: str
    expected_revision: Optional[int] = None
    background: bool = False
    wait_max_s: int = 20
    session_id: Optional[str] = None


class RasterSamplingStatusArgs(BaseModel):
    plan_id: str
    session_id: Optional[str] = None


class RasterSamplingResultsArgs(BaseModel):
    plan_id: str = ""
    execution_id: str = Field("", json_schema_extra={"ref_cursor": True})
    page: int = 1
    per_page: int = Field(20, ge=1, le=100)  # 工具上下文行帽（服务面 500）
    include_curves: bool = False
    session_id: Optional[str] = None


_MAX_TOOL_ROWS = 20
_WAIT_RESOLUTION_S = 0.5


def _sid(session_id: Optional[str]) -> str:
    return _resolve_session_id(session_id) or ""


def _typed_error(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, RasterSamplingError):
        return exc.to_payload()
    return {"success": False, "code": "UNEXPECTED",
            "detail": str(exc)[:200]}




def register_raster_sampling_tools(registry: ToolRegistry) -> None:
    """注册 5 个只经标准 dispatcher 的采样工具。"""

    @tool(
        registry,
        args_model=RasterSamplingPlanArgs,
        name="webgis_raster_sampling_plan",
        description=(
            "创建数值栅格采样计划：点集（监测站/样本点 FeatureCollection）或"
            "线路（LineString/MultiLineString）+ 多个授权数值栅格源与波段 → "
            "versioned 计划（站位/预算/方法合同）。"
            "\n何时用：给站点提取 NDVI/地表温度、沿道路/管线查看降雨/坡度/类别"
            "变化的准备阶段。"
            "\n何时不用：(1) 只检视 ≤16 点像元值 — webgis_raster_expression_probe；"
            "(2) DEM 高程剖面 — terrain_profile。"
            "\n关键约束：≤4 源、每源 ≤8 波段、总 ≤16 波段、≤50000 站位；"
            "bilinear 需波段声明 continuous（整数 dtype 不推断语义）；"
            "分类波段只允许 source_pixel。"
        ),
        tier=2,
        domains=["raster"],
        cost="medium",
        param_descriptions={
            "mode": "source_points（点集）| line_stations（沿线站位）",
            "points_ref": "点 FeatureCollection 会话 ref",
            "lines_ref": "线路 FeatureCollection 会话 ref",
            "station_crs": "站位几何 CRS（默认 EPSG:4326）",
            "distance_mode": "geodesic（地理 CRS）| planar_metric（米制投影）",
            "station_step": "沿线站位步长（米；line_stations 必填）",
            "method": "source_pixel | bilinear | bilinear_renormalized",
            "sources": "[{ref_id, bands, band_semantics?, band_units?, band_labels?}]",
        },
        side_effect="state_mutation",  # 计划落盘（可修订持久事实）
        network=False,
        deterministic=False,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=("栅格采样", "点集提取", "沿线剖面", "波段"),
        output_semantic_type="sampling_plan",
        result_size_policy="bounded",
        crs_semantics="crs_aware",
        failure_modes=("invalid_args", "missing_data", "budget"),
    )
    async def plan(
        mode: str,
        sources: List[Any],  # 结构由 service 校验（动态 args 模型不解析嵌套泛型）
        points_ref: str = "",
        lines_ref: str = "",
        line_feature_id: str = "",
        station_crs: str = "EPSG:4326",
        distance_mode: str = "geodesic",
        station_step: Optional[float] = None,
        include_vertices: bool = True,
        method: str = "source_pixel",
        name: str = "",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "UNAUTHORIZED",
                    "detail": "no authorized session for raster sampling"}
        try:
            out = await service.create_plan(
                session_id=sid, owner_token=None, name=name,
                mode=mode, points_ref=points_ref,
                lines_ref=lines_ref,
                line_feature_id=line_feature_id,
                station_crs=station_crs,
                distance_mode=distance_mode,
                station_step=station_step,
                include_vertices=include_vertices,
                method=method, sources=sources,
                trusted_dispatch=True)
        except RasterSamplingError as exc:
            return _typed_error(exc)
        plan_payload = out["plan"]
        return {
            "success": True,
            "plan_id": plan_payload["plan_id"],
            "revision": plan_payload["revision"],
            "mode": plan_payload["mode"],
            "method": plan_payload["method"],
            "stations": out["stations"],
            "sources": [
                {"ref_id": s["ref_id"], "bands": s["bands"],
                 "data_revision": s["data_revision"],
                 "band_semantics": s["band_semantics"]}
                for s in plan_payload["sources"]
            ],
            "params_digest": plan_payload["params_digest"],
            "next": [
                "webgis_raster_sampling_preview (plan_id)",
                "webgis_raster_sampling_execute (plan_id)",
            ],
        }

    @tool(
        registry,
        args_model=RasterSamplingPreviewArgs,
        name="webgis_raster_sampling_preview",
        description=(
            "采样计划有界预览（≤64 站位均匀取样，内联执行，不落成果）："
            "返回样本行、读取记账与成本预估，用于执行前核对波段/单位/方法。"
            "\n关键约束：预览非权威、非全量；plan 修订后旧预览失效"
            "（expected_revision 校验）。"
        ),
        tier=2,
        domains=["raster"],
        cost="light",
        param_descriptions={
            "plan_id": "webgis_raster_sampling_plan 返回的计划 ID",
            "expected_revision": "计划修订号（防旧预览）",
            "limit": "预览站位数（≤64）",
        },
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="medium",
        scale_class="medium",
        tags=("栅格采样", "预览"),
        output_semantic_type="sampling_preview",
        result_size_policy="bounded",
        crs_semantics="crs_aware",
        failure_modes=("invalid_args", "missing_data", "budget"),
    )
    async def preview(
        plan_id: str,
        expected_revision: Optional[int] = None,
        limit: int = 16,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "UNAUTHORIZED",
                    "detail": "no authorized session for raster sampling"}
        try:
            out = await service.preview(
                session_id=sid, plan_id=plan_id, owner_token=None,
                expected_revision=expected_revision, limit=limit,
                trusted_dispatch=True)
        except RasterSamplingError as exc:
            return _typed_error(exc)
        rows = out["rows"][:_MAX_TOOL_ROWS]
        return {
            "success": True,
            "plan_id": out["plan_id"],
            "revision": out["revision"],
            "preview": True,
            "station_selection": out["station_selection"],
            "rows_shown": len(rows),
            "rows_total": out["rows_shown"],
            "rows_truncated": out["rows_truncated"] or len(rows) < out["rows_shown"],
            "rows": [_bounded_row(r) for r in rows],
            "read_metrics": out["read_metrics"],
            "cost_estimate": out["cost_estimate"],
        }

    @tool(
        registry,
        args_model=RasterSamplingExecuteArgs,
        name="webgis_raster_sampling_execute",
        description=(
            "执行采样计划：durable 幂等作业 → 完整样本长表（parquet ref，可经"
            "数据交付下载）+ 站位点层（会话 ref，可加载为地图图层）+ 收据。"
            "\n何时用：预览核对后正式产出持久采样成果。"
            "\n关键约束：wait_max_s 内未完成返回 job_id + 状态（用 "
            "webgis_raster_sampling_status 轮询）—— queued/running 不是完成；"
            "同计划重复执行幂等收敛同一收据。"
        ),
        tier=2,
        domains=["raster"],
        cost="heavy",
        param_descriptions={
            "plan_id": "计划 ID",
            "expected_revision": "计划修订号（防并发改）",
            "background": "true=立即返回 job 句柄；false=等待至多 wait_max_s",
            "wait_max_s": "内联等待上限（0-60s）",
        },
        side_effect="artifact_creation",  # 长表/站位层/收据
        network=False,
        deterministic=True,
        latency_class="slow",
        memory_class="heavy",
        scale_class="large",
        tags=("栅格采样", "执行", "数据产品"),
        output_semantic_type="sampling_receipt",
        result_size_policy="bounded",
        crs_semantics="crs_aware",
        failure_modes=("invalid_args", "missing_data", "budget", "cancelled"),
    )
    async def execute(
        plan_id: str,
        expected_revision: Optional[int] = None,
        background: bool = False,
        wait_max_s: int = 20,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "UNAUTHORIZED",
                    "detail": "no authorized session for raster sampling"}
        try:
            if background:
                out = await service.execute(
                    session_id=sid, plan_id=plan_id, owner_token=None,
                    expected_revision=expected_revision, background=True,
                    trusted_dispatch=True)
            else:
                out = await asyncio.wait_for(
                    service.execute(
                        session_id=sid, plan_id=plan_id, owner_token=None,
                        expected_revision=expected_revision,
                        background=False, trusted_dispatch=True),
                    timeout=min(60, max(1, wait_max_s)))  # 契约：0-60s 钳制
        except asyncio.TimeoutError:
            # wait_for 已取消协程：取消可能发生在作业登记（submit）之前，
            # 此时没有 job 可轮询 —— 只有确认存在活跃 job 才允许返回
            # submitted/status 指引，否则如实报未提交。
            try:
                view = await service.status(session_id=sid, plan_id=plan_id)
            except Exception:  # noqa: BLE001 — 查不到就绝不虚报已提交
                view = {}
            job = (view or {}).get("job") or {}
            job_status = str(job.get("status") or "")
            if job_status in ("pending", "queued", "running", "cancelling"):
                return {
                    "success": True,
                    "submitted": True,
                    "status": job_status,
                    "job_id": job.get("job_id"),
                    "detail": "execution still running after wait_max_s",
                    "next": ["webgis_raster_sampling_status (plan_id)"],
                }
            return {
                "success": False,
                "code": "EXECUTION_NOT_SUBMITTED",
                "detail": (
                    "wait_max_s elapsed and no active sampling job is "
                    "registered (the inline wait was cancelled before/at "
                    "registration); poll status to confirm before retrying"
                ),
            }
        except RasterSamplingError as exc:
            return _typed_error(exc)
        return _execute_outcome(out)

    @tool(
        registry,
        args_model=RasterSamplingStatusArgs,
        name="webgis_raster_sampling_status",
        description=(
            "采样执行状态：job 状态 + 最新收据 + 产物存活；"
            "job 完成但收据缺席时如实标 recovery_required。"
        ),
        tier=2,
        domains=["raster"],
        cost="light",
        param_descriptions={"plan_id": "计划 ID"},
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("栅格采样", "状态"),
        output_semantic_type="job_status",
        result_size_policy="bounded",
        crs_semantics="none",
        failure_modes=("invalid_args", "missing_data"),
    )
    async def status(
        plan_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "UNAUTHORIZED",
                    "detail": "no authorized session"}
        try:
            out = await service.status(session_id=sid, plan_id=plan_id)
        except RasterSamplingError as exc:
            return _typed_error(exc)
        return out

    @tool(
        registry,
        args_model=RasterSamplingResultsArgs,
        name="webgis_raster_sampling_results",
        description=(
            "采样成果读取：分页样本行（长表）+ 曲线抽稀（可选）+ 站位点层 ref +"
            "收据摘要。完整数据用数据交付通道下载，本工具只回有界页。"
        ),
        tier=2,
        domains=["raster"],
        cost="light",
        param_descriptions={
            "plan_id": "计划 ID（与 execution_id 二选一）",
            "execution_id": "执行 ID",
            "page": "页码（1 基）",
            "per_page": "每页行数（≤100）",
            "include_curves": "是否附带显示曲线",
        },
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="medium",
        scale_class="medium",
        tags=("栅格采样", "成果", "分页"),
        output_semantic_type="sampling_results",
        result_size_policy="bounded",
        crs_semantics="crs_aware",
        failure_modes=("invalid_args", "missing_data"),
    )
    async def results(
        plan_id: str = "",
        execution_id: str = "",
        page: int = 1,
        per_page: int = 20,
        include_curves: bool = False,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        per_page = max(1, min(100, int(per_page)))
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "UNAUTHORIZED",
                    "detail": "no authorized session"}
        if not plan_id and not execution_id:
            return {"success": False, "code": "INVALID_PLAN",
                    "detail": "plan_id or execution_id required"}
        try:
            out = await service.results(
                session_id=sid, plan_id=plan_id,
                execution_id=execution_id, owner_token=None,
                page=page, per_page=per_page,
                include_curves=include_curves)
        except RasterSamplingError as exc:
            return _typed_error(exc)
        curves = out.get("curves") or {}
        return {
            "success": True,
            "execution_id": out["execution_id"],
            "plan_id": out["plan_id"],
            "receipt": out["receipt"],
            "pagination": out["pagination"],
            "rows": [_bounded_row(r) for r in out["rows"]],
            "curves": ({"bands": [
                {"source_ref": b["source_ref"], "band": b["band"],
                 "points": b["points"][:16]}
                for b in curves.get("bands", [])
            ], "thinning": curves.get("thinning", {})}
                if curves else None),
            "station_layer_ref": out.get("station_layer_ref", ""),
            "sample_table_ref": ((out["receipt"].get("outputs") or {})
                                 .get("sample_table_ref", "")),
        }


def _bounded_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """工具回执的行级有界投影（支撑像元等重事实不进上下文）。"""
    out = dict(row)
    out.pop("support_pixels", None)
    if out.get("raw_exact"):
        out["raw_exact"] = str(out["raw_exact"])
    return out


def _execute_outcome(out: Dict[str, Any]) -> Dict[str, Any]:
    if "receipt" in out and out.get("receipt") is not None:
        receipt = out["receipt"]
        summary = (receipt if isinstance(receipt, dict)
                   else service.receipt_summary(receipt))
        return {
            "success": True,
            "completed": True,
            "idempotent_reuse": bool(out.get("idempotent_reuse")),
            "execution_id": summary.get("execution_id"),
            "plan_id": summary.get("plan_id"),
            "station_count": summary.get("station_count"),
            "coverage": summary.get("coverage"),
            "outputs": summary.get("outputs"),
            "degraded": summary.get("degraded", []),
            "sample_table_ref": (summary.get("outputs") or {})
            .get("sample_table_ref", ""),
            "station_layer_ref": (summary.get("outputs") or {})
            .get("station_layer_ref", ""),
            "next": [
                "webgis_raster_sampling_results (plan_id) — 分页读取",
                "数据交付（submit_data_delivery）— 下载长表 CSV/GeoParquet",
            ],
        }
    return {
        "success": True,
        "completed": False,
        "status": out.get("status"),
        "job_id": out.get("job_id"),
        "plan_id": out.get("plan_id"),
        "detail": "execution not finished; poll status",
        "next": ["webgis_raster_sampling_status (plan_id)"],
    }
