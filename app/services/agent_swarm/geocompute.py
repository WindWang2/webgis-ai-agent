"""GeoComputeAgent — 空间计算专家（ADR-0188 D4/D6）。

专职复杂算子编排、异步 Celery 调度与「Zero Big Data in Context」提货券
流转。两条工程纪律红线：

- **Celery First**：编排器只做计划构造（``ExecutionPlan`` +
  ``validate_plan``）与 durable 提交（``submit_durable_job`` →
  ``geocompute.tasks.run_geocompute_node``）；缓冲/叠置/可达性/H3/栅格
  统计等重算子绝不内联在 Agent 事件循环；
- **Zero Big Data in Context**：结果唯一通货是 ``SpatialProfileRef``
  （ref_id 提货券 + 有界摘要），序列化硬上限 8KB —— 超限先截 metadata
  （``truncated=True`` 诚实标注），无 key 可截则 typed 诚实失败。

防御逻辑：``estimate_volume`` 三级降级估算（D1 cost_hint → rows_hint →
bbox 面积密度 → 诚实 unknown）；大范围/大体量时任务图头部自动注入
``reproject`` 节点（``infer_utm_crs`` 由 bbox 中心经度推导 UTM zone，
无 bbox 诚实跳过，绝不猜 EPSG）。
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from typing import Any, Callable, Dict, List, Optional

from app.services.agent_swarm.base import BaseSpecialistAgent
from app.services.agent_swarm.contracts import (
    SERIALIZATION_BUDGET_BYTES,
    ComputeRequest,
    ComputeSubmission,
    SpatialProfileRef,
    SpatialProfileTooLargeError,
    VolumeEstimate,
)
from app.services.geocompute.graph import validate_plan
from app.services.geocompute.plan import (
    CrsExpectation,
    ExecutionNode,
    ExecutionPlan,
    ExecutionPolicyKind,
    NodeCategory,
    PayloadKind,
    ResourceBudget,
    ResourceEstimate,
)

#: bbox 面积密度启发式（行/km²；估算用途，诚实标注 assumption）。
DENSITY_ROWS_PER_KM2 = 50
#: 自动 UTM 防御阈值：超过任一即注入投影节点。
AUTO_UTM_AREA_KM2_THRESHOLD = 5_000.0
AUTO_UTM_ROWS_THRESHOLD = 200_000

#: 操作 → 已接线节点类别（durable 交接只承载 features/rows 载荷，故
#: raster_window_operation / artifact_register 不入本表 —— 同
#: api.build_plan_from_json 限制）。
_OPERATION_CATEGORY: Dict[str, NodeCategory] = {
    "buffer_analysis": NodeCategory.VECTOR_OPERATION,
    "multi_ring_buffer": NodeCategory.VECTOR_OPERATION,
    "overlay_analysis": NodeCategory.VECTOR_OPERATION,
    "clip": NodeCategory.VECTOR_OPERATION,
    "dissolve": NodeCategory.VECTOR_OPERATION,
    "convex_hull": NodeCategory.VECTOR_OPERATION,
    "spatial_join": NodeCategory.SPATIAL_JOIN,
    "attribute_join": NodeCategory.ATTRIBUTE_JOIN,
    "aggregate": NodeCategory.AGGREGATE,
    "h3_binning": NodeCategory.AGGREGATE,
    "zonal_stats": NodeCategory.VECTOR_OPERATION,
    "filter": NodeCategory.FILTER,
    "query": NodeCategory.QUERY,
}

_MAX_REF_ID_LEN = 128


def _default_submitter(**kwargs: Any) -> Dict[str, Any]:
    """生产提交缝：durable job 运行时（Celery First 正门；测试注入桩）。"""
    from app.services.geocompute.tasks import run_geocompute_node
    from app.services.jobs.submit import submit_durable_job

    return submit_durable_job(celery_task=run_geocompute_node, **kwargs)


def _bbox_area_km2(bbox: List[float]) -> Optional[float]:
    """bbox 球面近似面积（km²）；非法形状/非正跨度 → None（诚实）。"""
    if not bbox or len(bbox) != 4:
        return None
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (minx, miny, maxx, maxy)):
        return None
    lat_span = maxy - miny
    lon_span = maxx - minx
    if lat_span <= 0 or lon_span <= 0:
        return None
    mid_lat = math.radians((miny + maxy) / 2.0)
    return lon_span * 111.32 * math.cos(mid_lat) * lat_span * 110.57


class GeoComputeAgent(BaseSpecialistAgent):
    """空间计算专家：算子编排 + Celery durable 调度 + 提货券流转。"""

    name = "geocompute"
    role_name = "geocompute"

    TOOL_ALLOWLIST = frozenset({
        # 执行图面（Data Plane 唯一正门）
        "validate_execution_plan",
        "execute_execution_plan",
        "get_execution_run",
        "cancel_execution_run",
        # 矢量算子
        "buffer_analysis",
        "multi_ring_buffer",
        "overlay_analysis",
        "spatial_join",
        "clip_layer",
        "dissolve_layer",
        "convex_hull",
        "reproject_coordinates",
        "aggregate_dataset",
        # 栅格 / 空间统计
        "zonal_stats",
        "h3_binning",
        "h3_lisa",
        "hotspot_analysis",
        "emerging_hotspot_analysis",
        "kriging_interpolation",
        # 网络可达性
        "network_shortest_path",
        "network_service_area",
        "network_od_matrix",
        "network_centrality",
        "network_accessibility",
        "nearest_facility",
    })

    SPECIALIST_PROMPT = (
        "你是空间计算专家（GeoCompute），专职算子编排与异步计算调度。"
        "纪律（Celery First）：所有重算子（缓冲/叠置/可达性/H3/栅格统计）"
        "必须编入 durable 执行图经 Celery Worker 执行，绝不内联在对话事件"
        "循环里解析大 GeoJSON；纪律（Zero Big Data in Context）：结果只以"
        "SpatialProfileRef 提货券回传（ref_id + 有界摘要 ≤8KB），原始几何"
        "绝不进上下文；大范围输入自动注入 UTM 投影防御并披露 CRS；估算不到"
        "的体积诚实为 unknown。"
    )

    def __init__(
        self,
        *,
        submitter: Optional[Callable[..., Dict[str, Any]]] = None,
        **base_kwargs: Any,
    ) -> None:
        super().__init__(**base_kwargs)
        self._submitter = submitter or _default_submitter

    # ── 估算与防御（D6）─────────────────────────────────────

    @staticmethod
    def infer_utm_crs(bbox: Optional[List[float]]) -> Optional[str]:
        """bbox 中心点 → UTM WGS84 CRS（北 326xx / 南 327xx）；无证据 → None。"""
        if not bbox or len(bbox) != 4:
            return None
        try:
            minx, miny, maxx, maxy = (float(v) for v in bbox)
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(v) for v in (minx, miny, maxx, maxy)):
            return None
        lon = (minx + maxx) / 2.0
        lat = (miny + maxy) / 2.0
        zone = min(60, max(1, int((lon + 180.0) // 6.0) + 1))
        return f"EPSG:{326 if lat >= 0 else 327}{zone:02d}"

    def estimate_volume(
        self,
        *,
        descriptor: Optional[Any] = None,
        bbox: Optional[List[float]] = None,
        rows_hint: Optional[int] = None,
    ) -> VolumeEstimate:
        """三级降级估算：D1 cost_hint → rows_hint → bbox 密度 → unknown。"""
        area = _bbox_area_km2(bbox)
        cost_rows = None
        cost_hint = getattr(descriptor, "cost_hint", None) if descriptor else None
        if cost_hint is not None:
            cost_rows = getattr(cost_hint, "rows", None)
        if cost_rows:
            return VolumeEstimate(rows=int(cost_rows), area_km2=area, method="cost_hint")
        if rows_hint:
            return VolumeEstimate(rows=int(rows_hint), area_km2=area, method="rows_hint")
        if area is not None:
            return VolumeEstimate(
                rows=int(area * DENSITY_ROWS_PER_KM2),
                area_km2=area,
                method="bbox_density",
            )
        return VolumeEstimate(rows=None, area_km2=None, method="unknown")

    # ── 编排与提交（D4/D6）──────────────────────────────────

    def plan_computation(self, request: Any) -> ComputeSubmission:
        """意图 → 合法 durable 执行图 → Celery 提交 → SpatialProfileRef。

        编排器进程内只做：构造、校验（``validate_plan``）、提交、提货券
        组装 —— 零算子执行（Celery First）。
        """
        self.heartbeat("plan:start")
        self.check_deadline()
        req = request if isinstance(request, ComputeRequest) else ComputeRequest(**request)

        category = _OPERATION_CATEGORY.get(req.operation)
        if category is None:
            raise ValueError(
                f"unsupported operation {req.operation!r}; "
                f"wired: {sorted(_OPERATION_CATEGORY)}"
            )

        volume = self.estimate_volume(
            descriptor=req.descriptor, bbox=req.bbox, rows_hint=req.rows_hint,
        )
        utm = self.infer_utm_crs(req.bbox)
        needs_defense = (
            (volume.area_km2 is not None and volume.area_km2 > AUTO_UTM_AREA_KM2_THRESHOLD)
            or (volume.rows is not None and volume.rows > AUTO_UTM_ROWS_THRESHOLD)
        )
        crs_defense = "auto_utm" if (needs_defense and utm) else None

        nodes: List[ExecutionNode] = []
        if crs_defense:
            nodes.append(ExecutionNode(
                node_id="reproject_auto_utm",
                category=NodeCategory.REPROJECT,
                operation="reproject_coordinates",
                parameters={"target_crs": utm, "defense": "auto_utm"},
                crs=CrsExpectation(output_crs=utm),
                policy=ExecutionPolicyKind.DURABLE_JOB,
                produces=PayloadKind.FEATURES,
                description="自动 UTM 防御投影（大范围/大体量输入）",
            ))
        dataset_fp = hashlib.sha1(req.dataset_ref.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        main_inputs = [nodes[-1].node_id] if nodes else []
        nodes.append(ExecutionNode(
            node_id=f"compute_{req.operation}",
            category=category,
            operation=req.operation,
            inputs=main_inputs,
            dataset_fingerprints={"dataset_ref": dataset_fp},
            parameters=dict(req.operation_params),
            crs=CrsExpectation(output_crs=utm) if utm else None,
            policy=ExecutionPolicyKind.DURABLE_JOB,
            produces=PayloadKind.FEATURES,
            accepts=[PayloadKind.FEATURES] if main_inputs else [],
            estimate=ResourceEstimate(
                rows=volume.rows,
                confidence="medium" if volume.method == "cost_hint" else "assumption",
            ),
        ))

        plan = ExecutionPlan(
            plan_id=f"gc-{uuid.uuid4().hex[:12]}",
            nodes=nodes,
            budget=ResourceBudget(
                deadline_s=req.deadline_s if req.deadline_s else 300.0,
            ),
            description=f"specialist geocompute: {req.operation}",
        )
        validate_plan(plan)  # 计划必须合法 —— 非法即编排失败，不降级派发

        self.heartbeat("plan:submit")
        submitted = dict(self._submitter(
            task_type="geocompute_specialist",
            display_name=f"GeoCompute {req.operation}",
            params={
                "plan_id": plan.plan_id,
                "operation": req.operation,
                "dataset_ref": req.dataset_ref,
            },
            task_kwargs={
                # durable 任务体消费尾节点；上游经 input_refs 交接
                "node": nodes[-1].model_dump(mode="json"),
                "session_id": req.session_id,
                "input_refs": {"input": req.dataset_ref},
                "input_keys": {"input": dataset_fp},
                "deadline_s": req.deadline_s,
                "run_id": f"run-{plan.plan_id}",
            },
            queue="geocompute",
            session_id=req.session_id,
        ))
        job_id = submitted.get("job_id")
        celery_task_id = submitted.get("task_id")
        final_fp = nodes[-1].semantic_fingerprint()
        ref = self.build_result_ref(
            plan_id=plan.plan_id,
            plan_digest=plan.graph_fingerprint(),
            ref_id=f"gc-{plan.plan_id}-{final_fp}"[:_MAX_REF_ID_LEN],
            job_id=int(job_id) if job_id is not None else None,
            celery_task_id=str(celery_task_id) if celery_task_id else None,
            crs=utm,
            crs_defense=crs_defense,
            volume=volume,
            summary=(
                f"{req.operation} on {req.dataset_ref}; "
                f"rows≈{volume.rows if volume.rows is not None else 'unknown'}; "
                f"nodes={len(nodes)} (durable)"
            ),
            notes=tuple(
                note for note in (
                    f"plan digest {plan.graph_fingerprint()}",
                    f"auto utm defense → {utm}" if crs_defense else None,
                ) if note
            ),
        )
        return ComputeSubmission(
            plan_id=plan.plan_id,
            node_count=len(nodes),
            job_id=ref.job_id,
            celery_task_id=ref.celery_task_id,
            ref=ref,
            plan_built=plan,
            plan_json=plan.model_dump(mode="json"),
        )

    # ── 提货券（D4）─────────────────────────────────────────

    def build_result_ref(
        self,
        *,
        plan_id: str,
        plan_digest: str,
        ref_id: Optional[str] = None,
        status: str = "submitted",
        job_id: Optional[int] = None,
        celery_task_id: Optional[str] = None,
        rows: Optional[int] = None,
        crs: Optional[str] = None,
        crs_defense: Optional[str] = None,
        volume: Optional[VolumeEstimate] = None,
        duration_ms: Optional[float] = None,
        summary: str = "",
        notes: tuple = (),
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SpatialProfileRef:
        """组装 SpatialProfileRef 并执行 8KB 硬闸。

        超限顺序：按「值体积」从大到小逐键丢弃 metadata（每次丢弃后
        ``truncated=True`` 诚实标注）→ metadata 耗尽仍超限 →
        ``SpatialProfileTooLargeError``（绝不静默裁剪载荷字段）。
        """
        meta = dict(metadata or {})

        def _make(truncated: bool) -> SpatialProfileRef:
            return SpatialProfileRef(
                ref_id=ref_id,
                plan_id=str(plan_id),
                plan_digest=str(plan_digest),
                status=status,  # type: ignore[arg-type]
                rows=rows,
                crs=crs,
                crs_defense=crs_defense,
                volume_estimate=volume,
                job_id=job_id,
                celery_task_id=celery_task_id,
                duration_ms=duration_ms,
                summary=str(summary)[: SpatialProfileRef._MAX_SUMMARY_LEN],
                notes=tuple(str(n)[: SpatialProfileRef._MAX_NOTE_LEN] for n in notes)
                [: SpatialProfileRef._MAX_NOTES],
                truncated=truncated,
                metadata=meta,
            )

        ref = _make(truncated=False)
        if len(ref.to_json_bytes()) < SERIALIZATION_BUDGET_BYTES:
            return ref
        drop_order = sorted(
            meta,
            key=lambda key: -len(json.dumps(meta[key], ensure_ascii=False, default=str)),
        )
        for key in drop_order:
            meta.pop(key)
            candidate = _make(truncated=True)
            if len(candidate.to_json_bytes()) < SERIALIZATION_BUDGET_BYTES:
                return candidate
        raise SpatialProfileTooLargeError(
            f"SpatialProfileRef exceeds {SERIALIZATION_BUDGET_BYTES}B budget "
            "with no truncatable metadata left; refusing to trim payload "
            "silently (Zero Big Data in Context, ADR-0188 D4)"
        )
