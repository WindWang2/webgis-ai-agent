"""概率抽样调查 Pi 工具面（GOAL-20261127-03 / WP5）—— 与 UI 走同一 service。

纪律（与 G39 同款）：
- 模型只获得有界摘要、状态与完整成果 refs；绝不把全部样本/记录表
  塞进上下文；
- session 由 ToolRegistry 从已授权请求上下文注入（_resolve_session_id），
  模型指定他人 session 即拒绝；
- 盲标默认：样本清单/记录响应不回显预测类（stratum）——解释员偏差
  防护（goal WP3）；manager 视图只经 REST 显式 blind=False；
- 无参考数据时只能出待核查计划（survey_progress / 无估计），不伪造
  调查结果（goal WP3 硬约束）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.lib.gis.scientific_evidence import build_evidence
from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.services.survey_sampling import service
from app.services.survey_sampling.contracts import SurveySamplingError
from app.tools.registry import ToolRegistry, tool
from app.tools.upload_tools import _resolve_session_id

logger = logging.getLogger(__name__)

_MAX_TOOL_SAMPLES = 200


def _sid(session_id: Optional[str]) -> str:
    return _resolve_session_id(session_id) or ""


def _typed_error(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, SurveySamplingError):
        return exc.to_payload()
    return {"success": False, "code": "UNEXPECTED", "detail": str(exc)[:200]}


def _attach_evidence(
    payload: Dict[str, Any], algorithm_id: str, *, tool_name: str,
    parameters_applied: Dict[str, Any],
) -> Dict[str, Any]:
    descriptor = get_algorithm_registry().get(algorithm_id)
    if descriptor is None:
        return payload
    payload["scientific_evidence"] = build_evidence(
        descriptor, tool=tool_name, parameters_applied=parameters_applied)
    return payload


class FramePreviewArgs(BaseModel):
    source_ref: str = Field(..., json_schema_extra={"ref_cursor": True},
                            description="分类栅格 ref（ref:raster-*）")
    band: int = Field(1, ge=1, le=64)
    class_dictionary: Dict[str, str] = Field(
        ..., description='类别值→类名，如 {"1":"forest","2":"crop","3":"water"}')
    session_id: Optional[str] = None


class DesignPreviewArgs(BaseModel):
    frame_digest: str
    strategy: str = Field("equal", pattern="^(equal|proportional|neyman)$")
    n_total: Optional[int] = Field(None, ge=2, le=200000)
    target_se_oa: Optional[float] = Field(None, gt=0.0, lt=1.0)
    prior_precision: Optional[Dict[str, float]] = None
    session_id: Optional[str] = None


class CreateDesignArgs(BaseModel):
    frame_digest: str
    objective: str = Field(..., max_length=200)
    target_time: str = ""
    strategy: str = Field("equal", pattern="^(equal|proportional|neyman)$")
    n_total: Optional[int] = Field(None, ge=2, le=200000)
    target_se_oa: Optional[float] = Field(None, gt=0.0, lt=1.0)
    prior_precision: Optional[Dict[str, float]] = None
    seed: int = 42
    backup_per_stratum: int = Field(0, ge=0, le=500)
    blind_labelling: bool = True
    max_nonresponse_rate: float = Field(0.2, gt=0.0, lt=1.0)
    notes: str = Field("", max_length=280)
    session_id: Optional[str] = None


class SamplesArgs(BaseModel):
    survey_id: str
    limit: int = Field(_MAX_TOOL_SAMPLES, ge=1, le=_MAX_TOOL_SAMPLES)
    blind: bool = Field(True, description="盲标视图：不含预测类（默认）")
    session_id: Optional[str] = None


class RecordReferenceArgs(BaseModel):
    survey_id: str
    sample_id: str
    status: str = Field(..., description="labelled|undetermined|refused|"
                                         "pending|not_visited|disputed")
    reference_class: Optional[str] = None
    evidence_type: str = Field("field_visit",
                               pattern="^(field_visit|high_res_imagery|"
                                       "existing_survey|other)$")
    evidence_note: str = ""
    quality: str = Field("medium", pattern="^(high|medium|low)$")
    interpreter: str = ""
    observed_at: str = ""
    note: str = ""
    expected_revision: Optional[int] = None
    session_id: Optional[str] = None


class ProgressArgs(BaseModel):
    survey_id: str
    session_id: Optional[str] = None


class EstimateArgs(BaseModel):
    survey_id: str
    session_id: Optional[str] = None


class GetEstimateArgs(BaseModel):
    survey_id: str
    estimate_id: str = ""
    session_id: Optional[str] = None


def register_survey_sampling_tools(registry: ToolRegistry):

    @tool(registry, name="survey_frame_preview",
          capabilities=["survey_sampling"],
          args_model=FramePreviewArgs,
          description="从分类栅格构建概率抽样框：各层（地图类别）有效像元数、"
                      "等面积核查后的层面积、框 digest（冻结可复现）。经纬度/"
                      "Web Mercator 网格面积不均会被 typed 拒绝并要求等面积 "
                      "CRS 重投影。无样本副作用",
          tier=2, domains=["survey_sampling", "statistics"], cost="medium",
          side_effect="deterministic_compute", network=False, deterministic=True,
          latency_class="medium", memory_class="medium", scale_class="large",
          tags=("概率抽样", "抽样框", "精度核查", "分层", "面积估计"),
          output_semantic_type="stats_table",
          result_size_policy="bounded",
          crs_semantics="source_preserve",
          failure_modes=("invalid_args", "missing_data", "unsupported_crs"))
    async def survey_frame_preview(args: FramePreviewArgs) -> dict:
        class_dict: Dict[int, str] = {}
        for k, v in (args.class_dictionary or {}).items():
            try:
                class_dict[int(k)] = str(v)
            except (TypeError, ValueError):
                return {"success": False, "code": "INVALID_FRAME_REQUEST",
                        "detail": f"class dictionary key {k!r} is not an "
                                  "integer class code"}
        try:
            payload = await service.build_survey_frame(
                _sid(args.session_id), None,
                {"session_id": _sid(args.session_id)},
                source_ref=args.source_ref, band=args.band,
                class_dictionary=class_dict, trusted_dispatch=True)
        except SurveySamplingError as exc:
            return _typed_error(exc)
        strata = [
            {"stratum_id": sid, **info}
            for sid, info in payload.get("strata", [])
        ]
        summary = (
            f"抽样框冻结：{payload['total_units']:,} 个有效像元，"
            f"{len(strata)} 层，总面积 {payload['total_area']:,.0f} "
            f"（像元面积均匀性 {payload['pixel_area_spread']:.2%} ≤ "
            f"{payload['pixel_area_tolerance']:.0%}）。"
            f"排除：nodata={payload['excluded']['nodata']:,}、"
            f"字典外={payload['excluded']['outside_dictionary']:,}。"
            f"frame_digest={payload['frame_digest'][:16]}…"
        )
        out = {
            "success": True,
            "summary": summary,
            "frame_digest": payload["frame_digest"],
            "strata": strata[:64],
            "total_units": payload["total_units"],
            "total_area": payload["total_area"],
            "excluded": payload["excluded"],
            "next": "用 survey_design_preview 做样本量预览（无副作用），"
                    "确认后 survey_create_design 锁定并选样",
        }
        return _attach_evidence(out, "survey.frame_build",
                                tool_name="survey_frame_preview",
                                parameters_applied={
                                    "band": args.band,
                                    "classes": len(class_dict)})

    @tool(registry, name="survey_design_preview",
          capabilities=["survey_sampling"],
          args_model=DesignPreviewArgs,
          description="抽样设计预览（无副作用）：等额/比例/Neyman 分配、"
                      "目标 SE 反推总量（Olofsson Eq.13）、每层样本量与"
                      "可解释依据；稀有类不跳过",
          tier=2, domains=["survey_sampling", "statistics"], cost="light",
          side_effect="deterministic_compute", network=False, deterministic=True,
          latency_class="fast", memory_class="light", scale_class="small",
          tags=("抽样设计", "样本量", "分配", "目标精度"),
          output_semantic_type="stats_table",
          result_size_policy="inline_small",
          crs_semantics="crs_free",
          failure_modes=("invalid_args",))
    async def survey_design_preview(args: DesignPreviewArgs) -> dict:
        try:
            preview = service.preview_allocation(
                {"session_id": _sid(args.session_id)}, args.frame_digest,
                strategy=args.strategy, n_total=args.n_total,
                target_se_oa=args.target_se_oa,
                prior_precision=args.prior_precision)
        except SurveySamplingError as exc:
            return _typed_error(exc)
        out = {"success": True,
               "summary": (f"建议分配（{args.strategy}）：总计 "
                           f"{preview['n_total']} 个样本。"
                           + ("；".join(f"{s['warnings'][:2]}"
                                        for s in [preview]) if preview.get(
                                    "warnings") else "")),
               **preview}
        return _attach_evidence(out, "survey.design_preview",
                                tool_name="survey_design_preview",
                                parameters_applied={
                                    "strategy": args.strategy,
                                    "n_total": preview["n_total"]})

    @tool(registry, name="survey_create_design",
          capabilities=["survey_sampling"],
          args_model=CreateDesignArgs,
          description="创建调查：冻结设计快照（digest 不可变）并按 seed "
                      "可复现选样（含预抽替补）。返回 survey_id 与每层样本量。"
                      "盲标默认开",
          tier=2, domains=["survey_sampling", "statistics"], cost="light",
          side_effect="state_mutation", network=False, deterministic=True,
          idempotent=True,
          latency_class="fast", memory_class="light", scale_class="medium",
          tags=("抽样设计", "锁定", "选样", "调查"),
          output_semantic_type="stats_table",
          result_size_policy="inline_small",
          crs_semantics="crs_free",
          failure_modes=("invalid_args", "stale_frame"))
    async def survey_create_design(args: CreateDesignArgs) -> dict:
        try:
            result = await service.create_survey(
                {"session_id": _sid(args.session_id)}, args.frame_digest,
                objective=args.objective, target_time=args.target_time,
                strategy=args.strategy, n_total=args.n_total,
                target_se_oa=args.target_se_oa,
                prior_precision=args.prior_precision, seed=args.seed,
                backup_per_stratum=args.backup_per_stratum,
                blind_labelling=args.blind_labelling,
                max_nonresponse_rate=args.max_nonresponse_rate,
                notes=args.notes)
        except SurveySamplingError as exc:
            return _typed_error(exc)
        return {
            "success": True,
            "summary": (f"调查 {result['survey_id']} 已创建："
                        f"主样本 {result['main_count']} + 替补 "
                        f"{result['backup_per_stratum']}×层；"
                        f"design_digest={result['design_digest'][:16]}…"
                        "（已锁定，变更需新建调查，历史保留）"),
            **result,
            "next": "survey_samples 拉取核查点（盲标视图）→ 野外/判读后 "
                    "survey_record_reference 记录 → survey_estimate 估计",
        }

    @tool(registry, name="survey_samples",
          capabilities=["survey_sampling"],
          args_model=SamplesArgs,
          description="拉取调查样本点清单（GeoJSON，可上图）：坐标、层内"
                      "序位、纳入概率；盲标视图不含预测类。含已完成核查"
                      "状态着色属性",
          tier=2, domains=["survey_sampling"], cost="light",
          side_effect="deterministic_compute", network=False, deterministic=True,
          latency_class="fast", memory_class="light", scale_class="medium",
          tags=("核查点", "样点", "地图", "盲标"),
          output_semantic_type="geojson_fc",
          result_size_policy="bounded",
          crs_semantics="wgs84_output",
          failure_modes=("missing_data",))
    async def survey_samples(args: SamplesArgs) -> dict:
        try:
            doc = service.survey_detail(
                {"session_id": _sid(args.session_id)}, args.survey_id,
                blind=args.blind)
        except SurveySamplingError as exc:
            return _typed_error(exc)
        samples = doc.get("samples", [])[: args.limit]
        features = [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [s["lon"], s["lat"]]},
             "properties": {
                 "sample_id": s["sample_id"],
                 "draw_rank": s["draw_rank"],
                 "is_backup": s["is_backup"],
                 "inclusion_prob": s["inclusion_prob"],
             }}
            for s in samples
        ]
        fc = {"type": "FeatureCollection", "features": features}
        return {
            "success": True,
            "summary": (f"{len(features)} 个核查点"
                        f"（盲标={'是' if args.blind else '否'}，"
                        f"总计 {doc.get('sample_count', 0)}）——"
                        "GeoJSON 已输出供地图加载"),
            "geojson": fc,
            "blind": bool(args.blind),
            "survey_id": args.survey_id,
        }

    @tool(registry, name="survey_record_reference",
          capabilities=["survey_sampling"],
          args_model=RecordReferenceArgs,
          description="记录一条独立参考观测（盲标；append-only 版本化）。"
                      "状态词表：labelled（须带 reference_class）|"
                      "undetermined|refused|pending|not_visited|disputed。"
                      "参考类必须在设计冻结的类别字典内",
          tier=2, domains=["survey_sampling"], cost="light",
          side_effect="state_mutation", network=False, deterministic=True,
          latency_class="fast", memory_class="light", scale_class="small",
          tags=("参考标签", "核查", "调查记录", "CAS"),
          output_semantic_type="stats_table",
          result_size_policy="inline_small",
          crs_semantics="crs_free",
          failure_modes=("invalid_args", "revision_conflict"))
    async def survey_record_reference(args: RecordReferenceArgs) -> dict:
        try:
            result = await service.record_reference(
                {"session_id": _sid(args.session_id)}, args.survey_id,
                sample_id=args.sample_id, status=args.status,
                reference_class=args.reference_class,
                evidence_type=args.evidence_type,
                evidence_note=args.evidence_note, quality=args.quality,
                interpreter=args.interpreter, observed_at=args.observed_at,
                note=args.note, actor="agent:survey_record_reference",
                expected_revision=args.expected_revision)
        except SurveySamplingError as exc:
            return _typed_error(exc)
        # 盲标：响应不含 stratum/预测类
        return {
            "success": True,
            "summary": (f"记录 {args.sample_id} v{result['version']} "
                        f"（{args.status}）；revision={result['revision']}"),
            **result,
        }

    @tool(registry, name="survey_progress",
          capabilities=["survey_sampling"],
          args_model=ProgressArgs,
          description="调查进度：每层计划/主样本/已标注/非响应/响应率；"
                      "无参考数据时只显示待核查计划（不产生估计）",
          tier=2, domains=["survey_sampling"], cost="light",
          side_effect="deterministic_compute", network=False, deterministic=True,
          latency_class="fast", memory_class="light", scale_class="small",
          tags=("调查进度", "响应率", "覆盖"),
          output_semantic_type="stats_table",
          result_size_policy="inline_small",
          crs_semantics="crs_free",
          failure_modes=("missing_data",))
    async def survey_progress(args: ProgressArgs) -> dict:
        try:
            progress = service.survey_progress(
                {"session_id": _sid(args.session_id)}, args.survey_id)
        except SurveySamplingError as exc:
            return _typed_error(exc)
        est_note = ("可以估计" if progress["can_estimate"]
                    else "尚不可估计（每层需 ≥2 个响应；无参考数据只能出"
                         "待核查计划，不得伪造调查结果）")
        return {
            "success": True,
            "summary": (f"已标注 {progress['total_labelled']}/"
                        f"{progress['total_planned']}（响应率 "
                        f"{progress['overall_response_rate']:.0%}）；{est_note}"),
            **progress,
        }

    @tool(registry, name="survey_estimate",
          capabilities=["survey_sampling"],
          args_model=EstimateArgs,
          description="设计一致估计：加权误差矩阵、OA/UA/PA、各类面积与 "
                      "95% CI（Olofsson 2014）。源漂移/类字典漂移 → stale "
                      "拒绝；非响应超设计上限 → 拒绝总体推断",
          tier=2, domains=["survey_sampling", "statistics"], cost="light",
          side_effect="artifact_creation", network=False, deterministic=True,
          latency_class="fast", memory_class="light", scale_class="small",
          tags=("精度估计", "误差矩阵", "面积", "置信区间"),
          output_semantic_type="stats_table",
          result_size_policy="bounded",
          crs_semantics="crs_free",
          failure_modes=("invalid_args", "stale_source", "not_evaluable"))
    async def survey_estimate(args: EstimateArgs) -> dict:
        try:
            payload = await service.estimate_survey(
                {"session_id": _sid(args.session_id)}, args.survey_id,
                actor="agent:survey_estimate")
        except SurveySamplingError as exc:
            return _typed_error(exc)
        oa = payload.get("overall_accuracy") or {}
        out = {
            "success": True,
            "summary": (f"估计完成（status={payload.get('status')}）："
                        f"OA={oa.get('estimate')}"
                        f"±{oa.get('se')}；estimate_id="
                        f"{payload.get('estimate_id')}。"
                        "完整矩阵/UA/PA/面积 CI 见 estimate 字段与 refs"),
            **payload,
        }
        return _attach_evidence(out, "survey.stratified_estimate",
                                tool_name="survey_estimate",
                                parameters_applied={
                                    "survey_id": args.survey_id})

    @tool(registry, name="survey_get_estimate",
          capabilities=["survey_sampling"],
          args_model=GetEstimateArgs,
          description="读取已保存的估计结果（最新或指定 estimate_id）",
          tier=2, domains=["survey_sampling"], cost="light",
          side_effect="deterministic_compute", network=False, deterministic=True,
          latency_class="fast", memory_class="light", scale_class="small",
          tags=("估计结果", "报告"),
          output_semantic_type="stats_table",
          result_size_policy="bounded",
          crs_semantics="crs_free",
          failure_modes=("missing_data",))
    async def survey_get_estimate(args: GetEstimateArgs) -> dict:
        from app.services.survey_sampling import store

        scope = {"session_id": _sid(args.session_id)}
        if args.estimate_id:
            payload = store.get_estimate(scope, args.survey_id,
                                         args.estimate_id)
            if payload is None:
                return {"success": False, "code": "SURVEY_NOT_FOUND",
                        "detail": f"estimate {args.estimate_id} not found"}
            return {"success": True, **payload}
        estimates = store.list_estimates(scope, args.survey_id)
        if not estimates:
            return {"success": False, "code": "SURVEY_NOT_FOUND",
                    "detail": "no estimates yet — run survey_estimate first"}
        latest = estimates[-1]
        return {
            "success": True,
            "summary": (f"最新估计 {latest.get('estimate_id')}："
                        f"status={latest.get('status')}"),
            **latest,
        }
