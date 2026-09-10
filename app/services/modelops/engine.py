"""InferenceEngine —— 推理编排（ADR-0119 §3.4/§3.5；Epic D/E/G/H）。

流程（垂直切片的 production 主路径）::

    resolve model → provider 解析（C1 门）→ input identity（B1 口径）
    → qualify → ReprojectStage（R1-C2，显式）
    → tile plan（R1-C3 core/read）→ device plan + batch 预算
    → loaded cache acquire（single-flight）→ 有界批循环（取消/超时/
    OOM 降批/进度/计数）→ 流式融合（memmap 兜底）
    → 后处理 → artifact 发布 → manifest → reuse store

资源有界：批字节预算、批元素上限、merge 内存上限（超限 memmap）、
墙钟 deadline、并发信号量。禁止整幅加载（唯一读通道 read_window）。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from app.lib.cancellation import CancellationToken
from app.lib.data.fingerprints import sha256_of_file
from app.lib.modelops.capabilities import (
    TASK_CHANGE_DETECTION,
    TASK_CLASSIFICATION,
    TASK_EMBEDDING,
    TASK_INSTANCE_SEGMENTATION,
    TASK_OBJECT_DETECTION,
    TASK_PROMPTABLE_SEGMENTATION,
    TASK_SAR_OPTICAL_FUSION,
    TASK_SEMANTIC_SEGMENTATION,
    TASK_SUPER_RESOLUTION,
    TASK_TEMPORAL_CLASSIFICATION,
    TASK_TEMPORAL_FORECAST,
)
from app.lib.modelops.compatibility import (
    InputProfile,
    qualify,
    report_to_error,
)
from app.lib.modelops.descriptor import GeoModelDescriptor, REUSE_ELIGIBLE_SEED_POLICIES
from app.lib.modelops.errors import (
    InferenceCancelled,
    InferenceTimeout,
    ModelOpsError,
    PlanningError,
    PreprocessError,
    ProviderOOM,
    ResourceUnavailable,
)
from app.lib.modelops.fingerprint import (
    build_reuse_key,
    software_env_fingerprint,
)
from app.lib.modelops.metrics import PerfCounters
from app.lib.modelops.planning import TilePlan, plan_tiles
from app.lib.modelops.preprocess import build_plan as build_preprocess_plan
from app.lib.modelops.preprocess import preprocess_batch
from app.lib.modelops.promptable import PromptSpec
from app.lib.modelops.resources import DevicePlan, batch_for_budget
from app.lib.modelops.stitching import (
    SegmentationMergePolicy,
    collect_embeddings,
    merge_detections,
    merge_instances,
)
from app.lib.modelops.temporal import TemporalStackSpec
from app.lib.modelops.vectorize import VectorizeParams, vectorize_class_raster
from app.lib.geo_raster.reader import RasterReader
from app.services.modelops.artifacts import (
    build_geojson_from_detections,
    publish_json_artifact,
    publish_raster_artifact,
    write_geojson_output,
    write_raster_output,
)
from app.services.modelops.config import ModelOpsSettings
from app.services.modelops.loaded_cache import LoadedModelCache, load_key
from app.services.modelops.manifest import build_inference_manifest
from app.services.modelops.providers.base import (
    InferenceContext,
    ProviderRegistry,
    TileBatch,
    resolve_device_plan,
)
from app.services.modelops.registry import ModelRegistryStore

logger = logging.getLogger(__name__)

#: merge 缓冲的 RAM 上限（bytes）；超出 → memmap 兜底（磁盘，有界）。
MERGE_RAM_BUDGET_BYTES = 256 * 1024 * 1024
#: memmap 兜底的硬上限（bytes；超过 = typed 拒绝，不无界落盘）。
MERGE_DISK_HARD_CAP_BYTES = 64 * 1024**3
#: extension worker 单帧输出上限（R1-M5：FRAME_MAX_BYTES 68MiB 留余量）。
EXTENSION_BATCH_BYTES_CAP = 64 * 1024 * 1024
MAX_OOM_DOWNSHIFTS = 2
#: C-2：accumulator 的 finally 可达通道（一次一个 run，引擎串行于线程）。
_RUN_LOCAL: Dict[str, Any] = {}
#: 单次 run 的 tile 数硬上限（C-1：TileSpec 物化 + 指纹都是有界的）。
MAX_TILES_PER_RUN = 65536


def _clip_plan_to_roi(
    descriptor: GeoModelDescriptor,
    roi_bbox: Tuple[int, int, int, int],
    *,
    raster_width: int,
    raster_height: int,
) -> Tuple[TilePlan, Tuple[int, int]]:
    """把 tile 计划裁剪到 ROI 窗口（V3 §D）。

    ROI = 像素框 (x0, y0, x1, y1)（左上原点，半开区间）。在 ROI 尺寸上
    重排 tile 网格（planner 纯函数，确定性）；**窗口保持 ROI 本地坐标**
    ——引擎在读取时统一平移 ``roi_origin``（绝对窗口从完整栅格取数），
    融合累加器/产物数组都在 ROI 本地坐标上工作，georef 由产物写出的
    ``window_origin`` 平移恢复。context halo 不越过 ROI 边界（ROI =
    分析窗口，语义如实写入 manifest）。返回 (ROI plan, (x0, y0))。
    """
    x0, y0, x1, y1 = (int(v) for v in roi_bbox)
    # 乱序/退化 ROI = 调用方错误 → typed 拒绝（绝不静默改写成小框）。
    if x1 <= x0 or y1 <= y0:
        raise PlanningError(
            f"invalid roi_bbox {list(roi_bbox)}: x1 must exceed x0 and y1 must "
            "exceed y0 (pixel coords, top-left origin)",
            correction_hint="pass [x0, y0, x1, y1] with x0<x1 and y0<y1",
        )
    x0 = max(0, min(x0, raster_width - 4))
    y0 = max(0, min(y0, raster_height - 4))
    x1 = max(x0 + 4, min(x1, raster_width))
    y1 = max(y0 + 4, min(y1, raster_height))
    roi_plan = plan_tiles(
        descriptor, raster_height=y1 - y0, raster_width=x1 - x0
    )
    return roi_plan, (x0, y0)


@dataclass(frozen=True)
class InferenceRequest:
    """一次推理请求（typed；owner scope 恰好一维）。"""

    model_id: str
    source_uri: str
    owner_scope: Dict[str, str]                 # {"session_id": ..} / {"project_id": ..}
    model_version: Optional[str] = None
    task_type: Optional[str] = None             # 多任务 descriptor 时必填
    prompt: Optional[PromptSpec] = None
    temporal: Optional[TemporalStackSpec] = None
    #: V3 §C：双时相变化检测的后时相栅格（change_detection 任务必填；
    #: 网格（尺寸/CRS/transform）必须与 source_uri 严格一致）。
    source_uri_b: Optional[str] = None
    #: V3 §D：ROI 像素框 (x0, y0, x1, y1)（左上原点，半开区间）；None =
    #: 全幅。tile 计划在 ROI 窗口上执行，产物 georef 平移回原栅格位置。
    roi_bbox: Optional[Tuple[int, int, int, int]] = None
    #: V3 §D：分割/变化/融合的类别栅格 → 矢量多边形（GeoJSON 产物）。
    vectorize_classes: bool = False
    #: V3 §D：类别多边形同步发布到 PostGIS 表（前置缺失 = honest skip）。
    postgis_table: Optional[str] = None
    #: V3 §H：prompt 坐标为地理坐标（需仿射变换到像素；False = 已是像素）。
    prompt_crs: bool = False
    score_threshold: float = 0.5
    confidence_floor: float = 0.0
    device_override: Optional[str] = None
    seed: Optional[int] = None
    deadline_s: Optional[float] = None
    input_data_object_id: Optional[str] = None
    polygonize_instances: bool = False
    output_probabilities: bool = False
    output_dir: Optional[Path] = None
    #: 调用方生成的 run 键（取消键 = run_id；R1-M5：禁用 model_id 默认键，
    #  避免同模型并发跑互相覆盖取消令牌）。
    run_key: Optional[str] = None


@dataclass
class InferenceResult:
    run_id: str
    status: str                                  # completed | reused
    reused: bool
    task_type: str
    manifest: Dict[str, Any]
    outputs: Dict[str, Dict[str, Any]]           # role → {path, data_object_id?, ...}
    perf: Dict[str, Any]
    reuse_key: Optional[str]


class InferenceEngine:
    """有界推理引擎（同步执行；调用方负责线程池 offload）。"""

    def __init__(
        self,
        registry: ModelRegistryStore,
        providers: ProviderRegistry,
        settings: Optional[ModelOpsSettings] = None,
        *,
        loaded_cache: Optional[LoadedModelCache] = None,
        reuse_store: Optional[Any] = None,
        cancel_registry: Optional[Any] = None,
        vram_ledger: Optional[Any] = None,
        gpu_devices: Tuple[Any, ...] = (),
    ) -> None:
        self._registry = registry
        self._providers = providers
        self._settings = settings or ModelOpsSettings.load()
        self._loaded_cache = loaded_cache or LoadedModelCache(
            max_models=self._settings.max_loaded_models
        )
        self._reuse = reuse_store
        self._slots = threading.BoundedSemaphore(self._settings.max_concurrent_inferences)
        # V3 §E：VRAM 账本（进程内预订；None = 只观测不记账——直构引擎的
        # 测试路径保持零依赖）。
        self._ledger = vram_ledger
        self._gpu_devices = gpu_devices

    @property
    def loaded_cache(self) -> LoadedModelCache:
        """loaded cache 只读视图（warm pool 等调度组件消费）。"""
        return self._loaded_cache

    # ── public ──────────────────────────────────────────────────────
    def run(
        self,
        request: InferenceRequest,
        *,
        cancel_token: Optional[CancellationToken] = None,
        progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> InferenceResult:
        if set(request.owner_scope) - {"session_id", "project_id"} or len(request.owner_scope) != 1:
            raise ModelOpsError("owner_scope must be exactly one of session_id/project_id")
        run_id = request.run_key or uuid.uuid4().hex[:16]
        perf = PerfCounters()
        run_started = time.perf_counter()
        queued_from = time.perf_counter()
        if not self._slots.acquire(timeout=30.0):
            raise ResourceUnavailable("concurrent inference slots exhausted (30s wait)")
        perf.note_latency(queue_wait=time.perf_counter() - queued_from)
        try:
            return self._run_guarded(request, run_id=run_id, perf=perf,
                                     cancel_token=cancel_token, progress=progress)
        except InferenceCancelled:
            # R2-M8：取消延迟真实入账 + 协议 cancel 钩子（引擎串行化通知）。
            perf.note_latency(cancel=time.perf_counter() - run_started)
            try:
                provider_ref = getattr(self, "_last_provider", None)
                if provider_ref is not None:
                    provider_ref.cancel(None, run_id)  # best-effort 通知
            except Exception:  # noqa: BLE001 — 通知失败不改变取消语义
                pass
            raise
        finally:
            self._slots.release()

    # ── 编排 ────────────────────────────────────────────────────────
    def _run_guarded(
        self,
        request: InferenceRequest,
        *,
        run_id: str,
        perf: PerfCounters,
        cancel_token: Optional[CancellationToken],
        progress: Optional[Callable[[Dict[str, Any]], None]],
    ) -> InferenceResult:
        reproject_source: Optional[Path] = None  # R2 m-4：精确临时文件登记
        deadline = time.monotonic() + (
            request.deadline_s if request.deadline_s else self._settings.inference_deadline_s
        )
        ctx = InferenceContext(
            run_id=run_id,
            seed=request.seed,
            cancelled=(lambda: bool(cancel_token.cancelled)) if cancel_token else None,
        )

        def _checkpoint() -> None:
            if ctx.is_cancelled():
                raise InferenceCancelled(f"run {run_id} cancelled by caller")
            if time.monotonic() > deadline:
                raise InferenceTimeout(f"run {run_id} exceeded deadline")

        _emit(progress, stage="resolve", run_id=run_id)
        record = self._registry.resolve(
            request.model_id,
            request.model_version,
            session_id=request.owner_scope.get("session_id"),
            project_id=request.owner_scope.get("project_id"),
        )
        descriptor = record.descriptor
        provider = self._providers.get(descriptor.provider_ref)  # R1-C1 门
        self._last_provider = provider  # R2-M8：取消通知通道
        caps = provider.capabilities()
        task = self._resolve_task(descriptor, request)

        # ── 输入身份（B1 唯一口径）+ profile ────────────────────────
        _emit(progress, stage="profile", run_id=run_id)
        input_content_sha = self._content_identity(request)
        source_path_b: Optional[Path] = None
        content_b_sha: Optional[str] = None
        if task == TASK_CHANGE_DETECTION or request.source_uri_b is not None:
            if request.source_uri_b is None:
                raise ModelOpsError(
                    "change_detection inference requires source_uri_b (after image)",
                    correction_hint="pass the bitemporal pair: source_uri (before) "
                    "+ source_uri_b (after)",
                )
            source_path_b = Path(request.source_uri_b)
            content_b_sha = sha256_of_file(str(source_path_b))
        with RasterReader.open(request.source_uri) as reader:
            meta = reader.metadata()
            m_per_px = self._meters_per_pixel(meta)
            nodata_ratio = self._sampled_nodata_ratio(reader)
            try:
                descriptions = tuple(
                    d for d in (reader.dataset.descriptions or ()) if d
                )
            except Exception:  # noqa: BLE001 — 波段名缺失按无名处理
                descriptions = ()
            profile = InputProfile(
                width=meta.width,
                height=meta.height,
                band_count=meta.count,
                dtype=meta.dtype,
                crs=meta.crs,
                m_per_px=m_per_px,
                nodata=meta.nodata,
                nodata_ratio=nodata_ratio,
                temporal_length=request.temporal and len(request.temporal.times) or 1,
            )
            profile = InputProfile(
                **{**profile.__dict__, "band_names": descriptions}
            ) if descriptions else profile

        # ── 兼容性资格 ──────────────────────────────────────────────
        report = qualify(descriptor, profile, prompt=request.prompt, temporal=request.temporal)
        if not report.compatible:
            raise report_to_error(report)
        if source_path_b is not None:
            # 双时相网格一致性（typed 拒绝错位比较——变化语义要求逐像素对齐）。
            with RasterReader.open(str(source_path_b)) as rb:
                meta_b = rb.metadata()
            mismatch = []
            if (meta_b.width, meta_b.height) != (meta.width, meta.height):
                mismatch.append(
                    f"shape A {meta.width}x{meta.height} vs B {meta_b.width}x{meta_b.height}"
                )
            if (meta.crs or None) != (meta_b.crs or None):
                mismatch.append(f"crs A {meta.crs!r} vs B {meta_b.crs!r}")
            if meta.transform is not None and meta_b.transform is not None:
                if max(abs(a - b) for a, b in zip(tuple(meta.transform)[:6],
                                                  tuple(meta_b.transform)[:6])) > 1e-9:
                    mismatch.append("transform differs")
            if mismatch:
                raise PlanningError(
                    "bitemporal rasters are not grid-aligned: " + "; ".join(mismatch),
                    correction_hint="co-register both rasters (same grid) before "
                    "change detection",
                )
        # R2-M7：prompt 模式必须 ⊆ provider caps（qualifier 只看 descriptor
        # 任务语义；provider 能力是第二道门——否则静默丢弃 prompt 出错结果）。
        if request.prompt is not None:
            missing = request.prompt.required_prompt_modes() - caps.prompt_modes
            if missing:
                from app.lib.modelops.errors import CompatibilityError

                raise CompatibilityError(
                    f"provider {caps.provider_id!r} does not declare prompt "
                    f"modes {sorted(missing)}",
                    failures=[{"code": "PROMPT_MODE",
                               "detail": "provider capability gate",
                               "fix_hint": "choose a provider declaring these prompt modes"}],
                )

        # ── ReprojectStage（R1-C2：显式、有界、进指纹）──────────────
        reproject_payload: Optional[Dict[str, Any]] = None
        source_path = Path(request.source_uri)
        if report.reproject:
            _emit(progress, stage="reproject", run_id=run_id)
            source_path, input_content_sha, reproject_payload = self._reproject(
                source_path, report.reproject, run_id=run_id
            )
            reproject_source = source_path
            with RasterReader.open(str(source_path)) as rr:
                meta = rr.metadata()
            profile = InputProfile(
                width=meta.width, height=meta.height, band_count=meta.count,
                dtype=meta.dtype, crs=meta.crs,
                m_per_px=self._meters_per_pixel(meta),
                nodata=meta.nodata, nodata_ratio=profile.nodata_ratio,
                temporal_length=profile.temporal_length,
            )

        # ── 计划 ────────────────────────────────────────────────────
        # C-1：tile 数守门在物化之前（纯算术）——超限 typed 拒绝，
        # 绝不先构造 GB 级 TileSpec 列表。ROI 语义先于守门（小 ROI 于
        # 大栅格是 ROI 的主要用途，不得按全幅 tile 数拒绝）。
        from app.lib.modelops.planning import estimated_tile_count

        guard_h, guard_w = meta.height, meta.width
        if request.roi_bbox is not None and task not in (
            TASK_PROMPTABLE_SEGMENTATION, TASK_TEMPORAL_FORECAST,
            TASK_TEMPORAL_CLASSIFICATION,
        ):
            rx0, ry0, rx1, ry1 = (int(v) for v in request.roi_bbox)
            guard_w = max(4, min(rx1, meta.width) - max(0, rx0))
            guard_h = max(4, min(ry1, meta.height) - max(0, ry0))
        est_tiles = estimated_tile_count(
            descriptor, raster_height=guard_h, raster_width=guard_w
        )
        if est_tiles > MAX_TILES_PER_RUN:
            raise ResourceUnavailable(
                f"tile plan needs {est_tiles} tiles > per-run cap {MAX_TILES_PER_RUN}; "
                "increase chip size / stride or tile the request externally"
            )
        per_image_bands = (
            descriptor.input_bands // 2 if task == TASK_CHANGE_DETECTION else None
        )
        preprocess_plan = build_preprocess_plan(
            descriptor,
            source_band_count=profile.band_count,
            source_band_names=profile.band_names,
            expected_bands=per_image_bands,
        )
        tile_plan = plan_tiles(descriptor, raster_height=meta.height, raster_width=meta.width)
        roi_origin: Optional[Tuple[int, int]] = None
        if request.roi_bbox is not None:
            if task in (TASK_PROMPTABLE_SEGMENTATION, TASK_TEMPORAL_FORECAST,
                        TASK_TEMPORAL_CLASSIFICATION):
                raise ModelOpsError(
                    f"roi_bbox is not supported for task {task!r} "
                    "(single-window path); omit roi_bbox or use a tiled task",
                    correction_hint="run promptable/temporal tasks on the full raster",
                )
            tile_plan, roi_origin = _clip_plan_to_roi(
                descriptor,
                request.roi_bbox,
                raster_width=meta.width,
                raster_height=meta.height,
            )
        perf.chips_total = len(tile_plan.tiles)
        provider_payload = {
            "provider_ref": descriptor.provider_ref,
            "provider_id": caps.provider_id,
            "provider_type": caps.provider_type,
            "semantic_version": caps.semantic_version,
            "capabilities": caps.as_dict(),
        }
        prompt_payload = request.prompt.to_payload() if request.prompt else None
        temporal_payload = request.temporal.to_payload() if request.temporal else None
        postprocess_payload = {
            "score_threshold": request.score_threshold,
            "confidence_floor": request.confidence_floor,
            "output_probabilities": request.output_probabilities,
            "polygonize_instances": request.polygonize_instances,
            # R1-C2：prompt 几何 / 时序声明是有效输入参数 —— 不进 key 会
            # 造成「同 key 不同结果」（不同 prompt 命中同一缓存）。
            "prompt_geometry": (
                request.prompt.geometry_payload() if request.prompt else None
            ),
            "temporal": temporal_payload,
            # V3 §H：prompt 坐标系是结果语义（同数字不同坐标系 = 不同结果，
            # 必须区分复用键，否则地理 prompt 会命中像素 prompt 的缓存）。
            "prompt_crs": bool(request.prompt_crs) if request.prompt else None,
            # V3 §D：ROI 与矢量化参数是结果语义的一部分（进指纹）。
            "roi": (
                [int(v) for v in request.roi_bbox] if request.roi_bbox is not None else None
            ),
            "postgis_table": request.postgis_table,
            "vectorize": {
                "enabled": bool(request.vectorize_classes),
                "params": VectorizeParams().fingerprint_payload(),
            },
        }
        input_payload = {
            "source_uri": str(source_path),
            "content_sha256": input_content_sha,
            "source_b_uri": str(source_path_b) if source_path_b else None,
            "content_b_sha256": content_b_sha,
            "data_object_id": request.input_data_object_id,
            "width": profile.width,
            "height": profile.height,
            "bands": profile.band_count,
            "crs": profile.crs,
            "m_per_px": profile.m_per_px,
        }
        reuse_key = build_reuse_key(
            descriptor_payload=descriptor.fingerprint_payload(),
            provider_payload=provider_payload,
            input_content_sha256=input_content_sha,
            input_b_content_sha256=content_b_sha,
            preprocess_payload=preprocess_plan.fingerprint_payload(),
            tile_plan_payload=tile_plan.fingerprint_payload(),
            postprocess_payload=postprocess_payload,
            owner_scope=request.owner_scope,
        )

        # ── reuse 精确匹配（Epic §M）────────────────────────────────
        if self._reuse is not None and descriptor.random_seed_policy in REUSE_ELIGIBLE_SEED_POLICIES:
            entry = self._reuse.lookup(reuse_key, owner_scope=request.owner_scope)
            if entry is not None:
                perf.note_cache(hit=True)
                perf.finish()
                manifest = dict(entry["manifest"])
                manifest["reused"] = True
                manifest["reuse_run_id"] = run_id
                outputs = {
                    art["role"]: {
                        "path": str(Path(entry["entry_dir"]) / art["file"]),
                        "data_object_id": art.get("data_object_id"),
                    }
                    for art in entry["artifacts"]
                }
                return InferenceResult(
                    run_id=run_id, status="reused", reused=True, task_type=task,
                    manifest=manifest, outputs=outputs, perf=perf.export(),
                    reuse_key=reuse_key,
                )
            perf.note_cache(hit=False)

        # V3 §B/P2：引擎解析出的任务注入 ctx（多任务 descriptor 的 DL
        # provider 按「请求任务」映射输出，而非 task_types[0]）。
        ctx.extras["task"] = task
        # ── 设备与资源计划 ──────────────────────────────────────────
        device_plan = resolve_device_plan(descriptor, caps, device_override=request.device_override)
        estimate = provider.estimate_resources(descriptor, batch=caps.max_batch, device=device_plan.device)
        budget = self._settings.vram_budget_bytes
        if caps.provider_type == "extension_worker":
            # R1-M5 帧上限 + R2-M3 JSON 线格式膨胀（~8×）：预算按折算值。
            budget = min(budget, EXTENSION_BATCH_BYTES_CAP // 8)
        batch = batch_for_budget(
            chip_hw=(tile_plan.context_h, tile_plan.context_w),
            input_channels=descriptor.input_bands,
            output_channels=len(descriptor.class_schema.classes) + 1
            if descriptor.class_schema
            else 4,
            bytes_budget=max(1024, budget),
            max_batch=caps.max_batch,
            recommended_batch=estimate.recommended_batch,
        )
        # V3 §E：多 GPU 亲和（确定性 hash → 设备号；单卡/无卡 = 0）。
        from app.services.modelops.scheduling import model_affinity_index

        device_index = 0
        if device_plan.device == "cuda":
            device_index = model_affinity_index(
                descriptor.model_id, max(1, len(self._gpu_devices))
            )
        device_plan = DevicePlan(
            device=device_plan.device,
            batch=batch,
            vram_bytes=estimate.vram_bytes * batch,
            host_ram_bytes=estimate.host_ram_bytes * batch,
            accounting="externally_enforced" if estimate.externally_enforced else "provider_visible",
            device_index=device_index,
        )
        perf.note_resources(
            estimated_vram_bytes=device_plan.vram_bytes, device=device_plan.device
        )
        # R2-M8：host 峰值内存观测（POSIX ru_maxrss / win32 GetProcessMemoryInfo；
        # 不可得 = 0，manifest 如实呈现，不虚标）。
        perf.note_resources(peak_host_memory_bytes=_process_peak_rss_bytes())
        # V3 §E：VRAM 预订（load + 推理全程持有；无账本 = 只观测）。
        reservation = None
        if self._ledger is not None:
            reservation = self._ledger.acquire(
                device_plan.device,
                device_plan.device_index,
                bytes_needed=max(0, device_plan.vram_bytes),
                run_id=run_id,
            )
            ctx.extras["vram_reservation"] = {
                "device": device_plan.device,
                "device_index": device_plan.device_index,
                "bytes": reservation.bytes_reserved,
            }
        # P1：多 GPU 亲和的设备号对 provider 可见（torch adapter 消费）。
        ctx.extras["device_index"] = device_plan.device_index

        # ── loaded model（single-flight cache）──────────────────────
        # V3 §E：acquire 阶段（load_key/loaded_cache.acquire）失败也必须
        # 归还 VRAM 预订——否则 provider load 永久失败会耗尽账本。
        try:
            _emit(progress, stage="load", run_id=run_id)
            cache_key = load_key(
                descriptor,
                provider_ref=descriptor.provider_ref,
                device=device_plan.device,
                runtime_fingerprint=software_env_fingerprint(),
            )
            model, load_latency = self._loaded_cache.acquire(
                cache_key,
                descriptor,
                provider_ref=descriptor.provider_ref,
                device=device_plan.device,
                load_fn=lambda: provider.load(descriptor, device=device_plan.device),
                unload_fn=provider.unload,
            )
        except BaseException:
            if reservation is not None and self._ledger is not None:
                self._ledger.release(reservation)
            raise
        # R1-C5：acquire 之后的一切都纳入 finally —— warmup/mkdir 抛错
        # 不得泄漏 refcount（否则该 key 永久不可驱逐）。
        try:
            perf.note_latency(load=load_latency)
            provider.warmup(model)

            if request.temporal is not None:
                ctx.extras["missing_policy"] = request.temporal.missing_policy
                ctx.extras["stack_length"] = len(request.temporal.times)
                ctx.extras["output_time_semantics"] = request.temporal.output_time_semantics
            if request.prompt is not None:
                ctx.extras["prompt"] = request.prompt.to_payload()
                ctx.extras["prompt_mask_arrays"] = request.prompt.prior_masks

            # ── 任务执行 ────────────────────────────────────────────
            _emit(progress, stage="infer", run_id=run_id, tiles_total=len(tile_plan))
            output_dir = Path(
                request.output_dir or (self._settings.registry_dir / "outputs" / run_id)
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            if task == TASK_PROMPTABLE_SEGMENTATION:
                outputs = self._run_promptable(
                    request, descriptor, provider, model, ctx, source_path,
                    output_dir, perf, _checkpoint,
                )
            elif task == TASK_TEMPORAL_FORECAST:
                outputs = self._run_temporal(
                    request, descriptor, provider, model, ctx, source_path,
                    output_dir, perf, _checkpoint,
                )
            elif task == TASK_SUPER_RESOLUTION:
                outputs = self._run_superres(
                    request, descriptor, provider, model, ctx, source_path,
                    tile_plan, batch, device_plan, preprocess_plan, output_dir,
                    perf, _checkpoint, progress,
                    roi_origin=roi_origin,
                )
            elif task == TASK_TEMPORAL_CLASSIFICATION:
                outputs = self._run_temporal_classification(
                    request, descriptor, provider, model, ctx, source_path,
                    output_dir, perf, _checkpoint,
                )
            else:
                outputs = self._run_tiled(
                    request, task, descriptor, provider, model, ctx, source_path,
                    tile_plan, batch, device_plan, preprocess_plan, output_dir,
                    perf, _checkpoint, progress,
                    source_path_b=source_path_b,
                    roi_origin=roi_origin,
                )
        except BaseException:
            # C-2：accumulator 的 memmap/临时目录在任何异常路径都释放。
            seg_acc = _RUN_LOCAL.get("accumulator")
            if seg_acc is not None:
                seg_acc.close()
            raise
        finally:
            self._loaded_cache.release(cache_key)
            # V3 §E：释放 VRAM 预订（成功/失败路径都要归还）。
            if reservation is not None and self._ledger is not None:
                self._ledger.release(reservation)
            # R2-M8：provider 侧观测 VRAM（如 mock_gpu 的 vram_observed_peak）
            # 如实回传；协议成员 provider.cancel 在取消路径被调用（此前零调用方）。
            state = getattr(model, "state", None)
            if isinstance(state, dict) and state.get("vram_observed_peak"):
                perf.note_resources(observed_vram_bytes=int(state["vram_observed_peak"]))
            # R1 m-4：重投影中间产物不进 reuse（reuse 只存 outputs）——
            # run 结束即清理，防磁盘无界增长。
            if reproject_payload and reproject_source is not None and reproject_source.exists():
                # R2 m-4：精确登记的临时文件清理（不依赖目录名耦合）。
                try:
                    reproject_source.unlink()
                except OSError:
                    pass
        perf.finish()

        # ── manifest + reuse 发布 ───────────────────────────────────
        manifest = build_inference_manifest(
            descriptor_payload=descriptor.fingerprint_payload(),
            provider_payload=provider_payload,
            input_payload=input_payload,
            preprocess_payload=preprocess_plan.fingerprint_payload(),
            tile_plan_payload=tile_plan.fingerprint_payload(),
            postprocess_payload=postprocess_payload,
            reproject_payload=reproject_payload,
            owner_scope=request.owner_scope,
            task_type=task,
            outputs=[{"role": role, **{k: v for k, v in payload.items() if k != "path"}}
                     for role, payload in outputs.items()],
            perf=perf.export(),
            reuse_key=reuse_key,
            reused=False,
            compatibility=report.as_dict(),
            run_id=run_id,
            device_plan=device_plan.as_dict(),
            prompt_payload=prompt_payload,
            temporal_payload=temporal_payload,
        )
        manifest_path = write_geojson_output(
            output_dir / "inference_manifest.json", manifest
        )
        try:
            manifest_publish = publish_json_artifact(
                manifest_path,
                owner_scope=request.owner_scope,
                source_refs=[],
                producer={"capability": "modelops.inference", "run_id": run_id},
            )
            outputs["manifest"] = manifest_publish
        except Exception as exc:  # noqa: BLE001 — manifest 发布失败不毁推理结果
            logger.warning("manifest publish failed: %s", exc)
            outputs["manifest"] = {"published": False, "path": str(manifest_path)}

        if self._reuse is not None and descriptor.random_seed_policy in REUSE_ELIGIBLE_SEED_POLICIES:
            artifacts = [
                {"role": role, "file": payload.get("path"),
                 "data_object_id": payload.get("data_object_id")}
                for role, payload in outputs.items()
                if payload.get("path")
            ]
            try:
                self._reuse.store(
                    reuse_key, owner_scope=request.owner_scope,
                    manifest=manifest, artifacts=artifacts,
                )
            except Exception as exc:  # noqa: BLE001 — reuse 存储失败不影响结果
                logger.warning("reuse store failed: %s", exc)

        return InferenceResult(
            run_id=run_id, status="completed", reused=False, task_type=task,
            manifest=manifest, outputs=outputs, perf=perf.export(), reuse_key=reuse_key,
        )

    # ── 有界 tile 循环（segmentation/change/fusion/detection/…）─────
    def _run_tiled(
        self,
        request: InferenceRequest,
        task: str,
        descriptor: GeoModelDescriptor,
        provider: Any,
        model: Any,
        ctx: InferenceContext,
        source_path: Path,
        tile_plan: TilePlan,
        batch: int,
        device_plan: DevicePlan,
        preprocess_plan: Any,
        output_dir: Path,
        perf: PerfCounters,
        checkpoint: Callable[[], None],
        progress: Optional[Callable[[Dict[str, Any]], None]],
        *,
        source_path_b: Optional[Path] = None,
        roi_origin: Optional[Tuple[int, int]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        num_classes = len(descriptor.class_schema.classes) if descriptor.class_schema else 2
        merge_policy = SegmentationMergePolicy(
            output_probabilities=request.output_probabilities
        )
        accumulator: Optional["_SegmentationAccumulator"] = None
        if task in (TASK_SEMANTIC_SEGMENTATION, TASK_CHANGE_DETECTION,
                    TASK_SAR_OPTICAL_FUSION):
            accumulator = _SegmentationAccumulator(
                tile_plan.raster_height,
                tile_plan.raster_width,
                num_classes,
                policy=merge_policy,
            )
            _RUN_LOCAL["accumulator"] = accumulator
        detections_by_tile: Dict[int, List[Dict[str, Any]]] = {}
        instance_by_tile: List[np.ndarray] = []
        instance_classes_by_tile: List[Dict[int, int]] = []
        embeddings: List[np.ndarray] = []
        label_outputs: List[np.ndarray] = []
        oom_downshifts = 0
        current_batch = batch
        warm_latency: Optional[float] = None
        is_bitemporal = task == TASK_CHANGE_DETECTION
        # ROI 本地坐标 → 绝对读取窗口的平移量（非 ROI = (0,0)）。
        roi_dx, roi_dy = roi_origin if roi_origin is not None else (0, 0)

        with RasterReader.open(str(source_path)) as reader:
            reader_b: Optional[RasterReader] = (
                RasterReader.open(str(source_path_b)) if is_bitemporal and source_path_b
                else None
            )
            band_ids = [i + 1 for i in preprocess_plan.band_indices]
            start = 0
            try:
                while start < len(tile_plan.tiles):
                    group = tile_plan.tiles[start: start + current_batch]
                    checkpoint()
                    windows = []
                    for tile in group:
                        col = tile.read_window[1] + roi_dx
                        row = tile.read_window[0] + roi_dy
                        w, h = tile.read_window[3], tile.read_window[2]
                        data = reader.read_window((col, row, w, h), bands=band_ids)
                        mask = reader.read_mask((col, row, w, h)) == 0
                        windows.append((data, mask if mask.any() else None))
                        perf.note_window(1, bytes_read=int(data.nbytes))
                    if is_bitemporal and reader_b is not None:
                        # 双时相：B 栅格同窗口读取 + 同一 preprocess plan
                        # （band_indices 已是单栅格 C 口径），通道维拼接为
                        # (2C,H,W)（provider 输入契约）。
                        b_windows = []
                        for tile in group:
                            col = tile.read_window[1] + roi_dx
                            row = tile.read_window[0] + roi_dy
                            w, h = tile.read_window[3], tile.read_window[2]
                            data_b = reader_b.read_window((col, row, w, h), bands=band_ids)
                            mask_b = reader_b.read_mask((col, row, w, h)) == 0
                            b_windows.append(
                                (data_b, mask_b if mask_b.any() else None)
                            )
                            perf.note_window(1, bytes_read=int(data_b.nbytes))
                        pixels_b, _ = preprocess_batch(
                            preprocess_plan, descriptor, b_windows,
                            tiles=[t for t in group],
                        )
                        pixels_a, valid_mask = preprocess_batch(
                            preprocess_plan, descriptor, windows,
                            tiles=[t for t in group],
                        )
                        pixels = np.concatenate([pixels_a, pixels_b], axis=1)
                    else:
                        pixels, valid_mask = preprocess_batch(
                            preprocess_plan, descriptor, windows,
                            tiles=[t for t in group],
                        )
                    batch_obj = TileBatch(
                        pixels=pixels, valid_mask=valid_mask,
                        chip_hw=(group[0].chip_hw[0], group[0].chip_hw[1]),
                        batch_index=start // max(1, batch),
                    )
                    infer_started = time.perf_counter()
                    try:
                        output = provider.infer(model, batch_obj, ctx)
                    except ProviderOOM:
                        if self._ledger is not None:
                            self._ledger.report_oom(device_plan.device, device_plan.device_index)
                        if oom_downshifts >= MAX_OOM_DOWNSHIFTS or current_batch <= 1:
                            raise
                        oom_downshifts += 1
                        current_batch = max(1, current_batch // 2)
                        perf.note_oom_downshift()
                        continue  # 不推进 start：当前批降批重跑（有界重试）
                    latency = time.perf_counter() - infer_started
                    warm_latency = (
                        latency if warm_latency is None
                        else 0.7 * warm_latency + 0.3 * latency
                    )
                    perf.note_latency(warm=warm_latency, provider_rtt=latency)
                    perf.record_batch(len(group))
                    perf.pixels_done += int(pixels.size)
                    output.validate_for(batch_obj)

                    if accumulator is not None:
                        accumulator.add_tiles(
                            tile_plan.tiles[start: start + len(group)],
                            output.class_probabilities,
                            valid_masks=valid_mask,
                        )
                    elif task == TASK_OBJECT_DETECTION:
                        # R1-C4：按 provider 报告的 batch_index 展开到全局 tile 槽。
                        for det in output.detections or []:
                            tile_idx = start + int(det.get("batch_index", 0))
                            detections_by_tile.setdefault(tile_idx, []).append(det)
                    elif task == TASK_INSTANCE_SEGMENTATION:
                        masks_out = output.instance_masks
                        if masks_out.ndim == 2:
                            masks_out = masks_out[None]
                        for i in range(masks_out.shape[0]):
                            instance_by_tile.append(masks_out[i])
                            instance_classes_by_tile.append({1: 1})
                    elif task in (TASK_EMBEDDING, TASK_CLASSIFICATION):
                        if len(embeddings) + len(label_outputs) + len(group) > MAX_TILES_PER_RUN:
                            from app.lib.modelops.errors import ResourceUnavailable

                            raise ResourceUnavailable(
                                "per-chip output collection exceeds tile budget"
                            )
                        if output.embeddings is not None:
                            for i in range(output.embeddings.shape[0]):
                                embeddings.append(output.embeddings[i])
                        if output.label_probabilities is not None:
                            for i in range(output.label_probabilities.shape[0]):
                                label_outputs.append(output.label_probabilities[i])
                    start += len(group)
                    _emit(progress, stage="infer", run_id=ctx.run_id,
                          tiles_done=min(start, len(tile_plan.tiles)),
                          tiles_total=len(tile_plan.tiles))
            finally:
                if reader_b is not None:
                    reader_b.close()
            checkpoint()

        outputs: Dict[str, Dict[str, Any]] = {}
        if accumulator is not None:
            classes, confidence, valid, probs = accumulator.finalize(
                input_nodata=None,
                confidence_floor=request.confidence_floor,
            )
            classes_path, confidence_path = self._write_seg_rasters(
                source_path, classes, confidence, output_dir, descriptor,
                window_origin=roi_origin,
            )
            if request.vectorize_classes:
                # V3 §D：类别栅格 → 地理多边形（拓扑修复 + 简化 + 置信度）。
                outputs.update(
                    self._vectorize_and_publish(
                        request, classes, confidence, output_dir, descriptor,
                        roi_origin=roi_origin,
                    )
                )
            outputs["classes"] = self._publish_raster(
                classes_path, request, role="classes", descriptor=descriptor
            )
            outputs["confidence"] = self._publish_raster(
                confidence_path, request, role="confidence", descriptor=descriptor
            )
            if probs is not None:
                prob_path = write_raster_output(
                    output_dir / "probabilities.tif",
                    arrays=[probs[k] for k in range(probs.shape[0])],
                    band_names=[f"class_{k}" for k in range(probs.shape[0])],
                    template=RasterReader.open(str(source_path)),
                    dtype="float32",
                    nodata=0.0,
                )
                outputs["probabilities"] = self._publish_raster(
                    prob_path, request, role="probabilities", descriptor=descriptor
                )
            perf.note_merge(int(classes.size))
        elif task == TASK_OBJECT_DETECTION:
            per_tile = [detections_by_tile.get(tile.index, []) for tile in tile_plan.tiles]
            records = merge_detections(
                tile_plan, per_tile,
                score_threshold=request.score_threshold,
            )
            class_names = list(descriptor.class_schema.classes) if descriptor.class_schema else None
            with RasterReader.open(str(source_path)) as reader:
                det_transform = reader.dataset.transform
                if roi_origin is not None:
                    from affine import Affine as _Affine

                    det_transform = det_transform * _Affine.translation(*roi_origin)
                geojson = build_geojson_from_detections(
                    [r.as_dict() for r in records],
                    crs=reader.metadata().crs,
                    transform=det_transform,
                    class_names=class_names,
                )
            det_path = write_geojson_output(output_dir / "detections.geojson", geojson)
            outputs["detections"] = publish_json_artifact(
                det_path, owner_scope=request.owner_scope, source_refs=[],
                producer={"capability": "modelops.inference", "task": task},
            )
        elif task == TASK_INSTANCE_SEGMENTATION:
            merged = merge_instances(
                tile_plan, instance_by_tile, instance_classes_by_tile,
                polygonize=request.polygonize_instances,
            )
            inst_path = write_raster_output(
                output_dir / "instances.tif",
                arrays=[merged.instance_ids],
                band_names=["instance_id"],
                template=RasterReader.open(str(source_path)),
                nodata=0.0,
                dtype="int32",
                window_origin=roi_origin,
            )
            outputs["instances"] = self._publish_raster(
                inst_path, request, role="instances", descriptor=descriptor
            )
            if merged.polygon_geojson is not None:
                poly_path = write_geojson_output(
                    output_dir / "instances.geojson", merged.polygon_geojson
                )
                outputs["instance_polygons"] = publish_json_artifact(
                    poly_path, owner_scope=request.owner_scope, source_refs=[],
                    producer={"capability": "modelops.inference", "task": task},
                )
        else:  # embedding / classification
            items = collect_embeddings(
                tile_plan,
                embeddings or label_outputs,
                label_outputs if task == TASK_CLASSIFICATION and label_outputs else None,
            )
            if roi_origin is not None:
                # ROI 模式：空间锚定窗口平移回原栅格绝对坐标。
                from dataclasses import replace as _dc_replace

                dx, dy = roi_origin
                items = [
                    _dc_replace(
                        item,
                        core_window=(item.core_window[0] + dy,
                                     item.core_window[1] + dx,
                                     item.core_window[2],
                                     item.core_window[3]),
                    )
                    for item in items
                ]
            import json as _json

            emb_path = output_dir / "embeddings.json"
            emb_path.write_text(
                _json.dumps([i.as_dict() for i in items], ensure_ascii=False),
                encoding="utf-8",
            )
            outputs["embeddings"] = publish_json_artifact(
                emb_path, owner_scope=request.owner_scope, source_refs=[],
                producer={"capability": "modelops.inference", "task": task},
            )
        return outputs

    # ── promptable 路径（V3 §H：地理 prompt 变换 + tile 策略）────────
    def _run_promptable(
        self,
        request: InferenceRequest,
        descriptor: GeoModelDescriptor,
        provider: Any,
        model: Any,
        ctx: InferenceContext,
        source_path: Path,
        output_dir: Path,
        perf: PerfCounters,
        checkpoint: Callable[[], None],
    ) -> Dict[str, Dict[str, Any]]:
        from affine import Affine
        from rasterio import features as _features
        from shapely.geometry import mapping as _mapping
        from shapely.geometry import shape as _shape

        from app.lib.modelops.preprocess import preprocess_window
        from app.lib.modelops.foundation import (
            georeference_polygon,
            prompt_windows,
            prompts_to_pixel,
            window_local_prompts,
        )
        from app.lib.modelops.preprocess import preprocess_window

        prompt = request.prompt
        if prompt is None:
            raise PreprocessError("promptable inference requires a prompt")
        checkpoint()
        outputs: Dict[str, Dict[str, Any]] = {}
        with RasterReader.open(str(source_path)) as reader:
            meta = reader.metadata()
            base_transform = reader.dataset.transform
            if request.prompt_crs:
                # V3 §H：地理坐标 prompt → 像素坐标（box 同变换）。
                prompt = prompts_to_pixel(prompt, transform=base_transform)
            band_ids = [i + 1 for i in range(descriptor.input_bands)]
            windows = prompt_windows(
                prompt,
                raster_height=meta.height,
                raster_width=meta.width,
                chip_hw=descriptor.spatial.chip_size,
            )
            plan = build_preprocess_plan(descriptor, source_band_count=meta.count)
            features_out: List[Dict[str, Any]] = []
            canvas: Optional[np.ndarray] = None
            # 掩膜画布（可负担时）：整幅发布；超大栅格只发 GeoJSON（诚实降级）。
            if meta.height * meta.width <= 256 * 1024 * 1024:
                canvas = np.zeros((meta.height, meta.width), dtype=np.uint8)
            for win_row, win_col, win_h, win_w in windows:
                checkpoint()
                data = reader.read_window((win_col, win_row, win_w, win_h),
                                          bands=band_ids)
                nodata_mask = reader.read_mask((win_col, win_row, win_w, win_h)) == 0
                perf.note_window(1, bytes_read=int(data.nbytes))
                chip, valid = preprocess_window(
                    plan, descriptor, data,
                    nodata_mask if nodata_mask.any() else None,
                )
                window_prompt = window_local_prompts(
                    prompt, row=win_row, col=win_col
                )
                ctx.extras["prompt"] = window_prompt.to_payload()
                ctx.extras["prompt_mask_arrays"] = (
                    tuple(
                        m[win_row: win_row + win_h, win_col: win_col + win_w]
                        for m in window_prompt.prior_masks
                    ) if window_prompt.prior_masks else ()
                )
                batch = TileBatch(
                    pixels=chip[None],
                    valid_mask=valid[None, None]
                    if valid.ndim == 2 and not bool(valid.all()) else None,
                    chip_hw=(win_h, win_w),
                )
                output = provider.infer(model, batch, ctx)
                output.validate_for(batch)
                probs = output.class_probabilities[0]  # (2,H,W)
                object_mask = probs.argmax(axis=0) == 1
                if valid.ndim == 2:
                    object_mask &= valid
                win_transform = base_transform * Affine.translation(win_col, win_row)
                for geom, _val in _features.shapes(
                    object_mask.astype(np.uint8), mask=object_mask, connectivity=4
                ):
                    # 多边形地理参考：像素几何 × 窗口仿射（V3 §H 输出 georef）。
                    geometry = georeference_polygon(_shape(geom), win_transform)
                    features_out.append(
                        {
                            "type": "Feature",
                            "properties": {
                                "class": 1,
                                "window": [win_row, win_col, win_h, win_w],
                            },
                            "geometry": _mapping(geometry),
                        }
                    )
                if canvas is not None:
                    canvas[win_row: win_row + win_h,
                           win_col: win_col + win_w] |= object_mask.astype(np.uint8)
            perf.record_batch(len(windows))
        # 产物：可整幅缓存时发布掩膜栅格（全画布 uint8）；否则只发 GeoJSON
        # （诚实降级，不物化超大画布）。多窗口 GeoJSON 每窗口独立仿射，
        # 无跨窗口伪影。
        if canvas is not None:
            mask_path = write_raster_output(
                output_dir / "prompt_mask.tif",
                arrays=[canvas],
                band_names=["object"],
                template=RasterReader.open(str(source_path)),
                nodata=255.0,
                dtype="uint8",
            )
            outputs["prompt_mask"] = self._publish_raster(
                mask_path, request, role="prompt_mask", descriptor=descriptor
            )
        geojson = {
            "type": "FeatureCollection",
            "features": features_out,
        }
        poly_path = write_geojson_output(output_dir / "prompt_mask.geojson", geojson)
        outputs["prompt_mask_geojson"] = publish_json_artifact(
            poly_path, owner_scope=request.owner_scope, source_refs=[],
            producer={"capability": "modelops.promptable_inference"},
        )
        return outputs

    # ── temporal 单窗口路径 ─────────────────────────────────────────
    def _run_temporal(
        self,
        request: InferenceRequest,
        descriptor: GeoModelDescriptor,
        provider: Any,
        model: Any,
        ctx: InferenceContext,
        source_path: Path,
        output_dir: Path,
        perf: PerfCounters,
        checkpoint: Callable[[], None],
    ) -> Dict[str, Dict[str, Any]]:
        temporal = request.temporal
        if temporal is None:
            raise PlanningError("temporal inference requires a TemporalStackSpec")
        from app.lib.modelops.preprocess import preprocess_window as _tcls_pwin

        checkpoint()
        t = len(temporal.times)
        c = descriptor.input_bands
        with RasterReader.open(str(source_path)) as reader:
            meta = reader.metadata()
            if meta.width > descriptor.spatial.chip_size[0] or \
                    meta.height > descriptor.spatial.chip_size[1]:
                raise PlanningError(
                    "temporal reference path is single-window; raster exceeds chip "
                    "(tile-by-time not supported in v1)"
                )
            stack_channels: List[np.ndarray] = []
            for ti in range(t):
                checkpoint()
                band_ids = [ti * c + j + 1 for j in range(c)]
                if max(band_ids) > meta.count:
                    raise PlanningError(
                        f"source has {meta.count} bands; temporal stack needs {max(band_ids)} "
                        "(time-major C*T band layout)"
                    )
                data = reader.read_window((0, 0, meta.width, meta.height), bands=band_ids)
                stack_channels.append(data)
                perf.note_window(1, bytes_read=int(data.nbytes))
            pixels = np.concatenate(stack_channels, axis=0)[None].astype(np.float32)
            if temporal.missing_policy == "flag":
                flags = np.zeros((1, t, meta.height, meta.width), dtype=np.float32)
                pixels = np.concatenate([pixels, flags.reshape(1, t, meta.height, meta.width)], axis=1)
            batch = TileBatch(pixels=pixels, valid_mask=None,
                              chip_hw=(meta.height, meta.width))
            output = provider.infer(model, batch, ctx)
            output.validate_for(batch)
            forecast = output.class_probabilities[0]  # (C,H,W)
        # 输出：C 波段预测栈（temporal_stack 语义：波段=变量）。
        out_path = write_raster_output(
            output_dir / "temporal_forecast.tif",
            arrays=[forecast[i] for i in range(forecast.shape[0])],
            band_names=[f"band_{i + 1}" for i in range(forecast.shape[0])],
            template=RasterReader.open(str(source_path)),
            dtype="float32",
            nodata=-9999.0,  # 有限 nodata（NaN 不可指纹化，publish 会拒）
        )
        perf.record_batch(1)
        return {
            "temporal_forecast": self._publish_raster(
                out_path, request, role="temporal_forecast", descriptor=descriptor
            )
        }

    # ── super-resolution：逐 chip 上采样 + 全图重建（stride=chip）────
    def _run_superres(
        self,
        request: InferenceRequest,
        descriptor: GeoModelDescriptor,
        provider: Any,
        model: Any,
        ctx: InferenceContext,
        source_path: Path,
        tile_plan: TilePlan,
        batch: int,
        device_plan: DevicePlan,
        preprocess_plan: Any,
        output_dir: Path,
        perf: PerfCounters,
        checkpoint: Callable[[], None],
        progress: Optional[Callable[[Dict[str, Any]], None]],
        *,
        roi_origin: Optional[Tuple[int, int]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        stride_y, stride_x = tile_plan.stride_y, tile_plan.stride_x
        if stride_y != tile_plan.chip_h or stride_x != tile_plan.chip_w:
            raise PlanningError(
                "super_resolution requires stride == chip (no-overlap tiling); "
                f"got stride {stride_x}x{stride_y} vs chip {tile_plan.chip_w}x{tile_plan.chip_h}",
                correction_hint="register the model without stride overlap "
                "(SR 重叠会产生鬼影)",
            )
        scale = descriptor.output_transform.output_scale
        full_h, full_w = tile_plan.raster_height * scale, tile_plan.raster_width * scale
        channels = descriptor.input_bands
        accumulator = _StackAccumulator(full_h, full_w, channels)
        _RUN_LOCAL["accumulator"] = accumulator
        oom_downshifts = 0
        current_batch = max(1, batch)
        try:
            with RasterReader.open(str(source_path)) as reader:
                band_ids = [i + 1 for i in preprocess_plan.band_indices]
                roi_dx, roi_dy = roi_origin if roi_origin is not None else (0, 0)
                start = 0
                while start < len(tile_plan.tiles):
                    group = tile_plan.tiles[start: start + current_batch]
                    checkpoint()
                    windows = []
                    for tile in group:
                        col = tile.read_window[1] + roi_dx
                        row = tile.read_window[0] + roi_dy
                        w, h = tile.read_window[3], tile.read_window[2]
                        data = reader.read_window((col, row, w, h), bands=band_ids)
                        mask = reader.read_mask((col, row, w, h)) == 0
                        windows.append((data, mask if mask.any() else None))
                        perf.note_window(1, bytes_read=int(data.nbytes))
                    pixels, valid_mask = preprocess_batch(
                        preprocess_plan, descriptor, windows, tiles=[t for t in group]
                    )
                    batch_obj = TileBatch(
                        pixels=pixels, valid_mask=valid_mask,
                        chip_hw=(group[0].chip_hw[0], group[0].chip_hw[1]),
                    )
                    try:
                        output = provider.infer(model, batch_obj, ctx)
                    except ProviderOOM:
                        if self._ledger is not None:
                            self._ledger.report_oom(device_plan.device, device_plan.device_index)
                        if oom_downshifts >= MAX_OOM_DOWNSHIFTS or current_batch <= 1:
                            raise
                        oom_downshifts += 1
                        current_batch = max(1, current_batch // 2)
                        perf.note_oom_downshift()
                        continue
                    output.validate_for(batch_obj)
                    perf.record_batch(len(group))
                    accumulator.add_tiles(
                        tile_plan.tiles[start: start + len(group)],
                        output.raster_stack,
                        scale=scale,
                        valid_masks=valid_mask,
                    )
                    start += len(group)
                    _emit(progress, stage="infer", run_id=ctx.run_id,
                          tiles_done=min(start, len(tile_plan.tiles)),
                          tiles_total=len(tile_plan.tiles))
                checkpoint()
            canvas = accumulator.finalize()
        except BaseException:
            accumulator.close()
            raise
        finally:
            _RUN_LOCAL.pop("accumulator", None)
        perf.note_merge(int(canvas.size))
        # 输出 georef：同一地理范围，分辨率 = 源 / scale（R1-M2 同源纪律）。
        sr_path = output_dir / "superres.tif"
        import rasterio
        from affine import Affine

        reader = RasterReader.open(str(source_path))
        try:
            src = reader.dataset
            new_transform = src.transform
            if roi_origin is not None:
                new_transform = new_transform * Affine.translation(*roi_origin)
            new_transform = new_transform * Affine.scale(1.0 / scale, 1.0 / scale)
            profile = {
                "driver": "GTiff", "height": full_h, "width": full_w,
                "count": channels, "dtype": "float32",
                "crs": src.crs, "transform": new_transform,
                "nodata": 0.0, "tiled": True,
                "blockxsize": 256, "blockysize": 256, "compress": "deflate",
            }
            sr_path.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(sr_path, "w", **profile) as dst:
                for c in range(channels):
                    dst.write(canvas[c].astype("float32"), c + 1)
                    dst.set_band_description(c + 1, f"band_{c + 1}")
        finally:
            reader.close()
        return {
            "superres": self._publish_raster(
                sr_path, request, role="superres", descriptor=descriptor
            )
        }

    # ── temporal classification：单窗口逐时相分类（v1 与 forecast 同界）─
    def _run_temporal_classification(
        self,
        request: InferenceRequest,
        descriptor: GeoModelDescriptor,
        provider: Any,
        model: Any,
        ctx: InferenceContext,
        source_path: Path,
        output_dir: Path,
        perf: PerfCounters,
        checkpoint: Callable[[], None],
    ) -> Dict[str, Dict[str, Any]]:
        temporal = request.temporal
        if temporal is None:
            raise PlanningError("temporal classification requires a TemporalStackSpec")
        from app.lib.modelops.preprocess import preprocess_window as _tcls_pwin

        checkpoint()
        t = len(temporal.times)
        c = descriptor.input_bands
        with RasterReader.open(str(source_path)) as reader:
            meta = reader.metadata()
            if meta.width > descriptor.spatial.chip_size[0] or \
                    meta.height > descriptor.spatial.chip_size[1]:
                raise PlanningError(
                    "temporal classification reference path is single-window; "
                    "raster exceeds chip (tile-by-time not supported in v1)"
                )
            plan = build_preprocess_plan(descriptor, source_band_count=c)
            stack_channels: List[np.ndarray] = []
            for ti in range(t):
                checkpoint()
                band_ids = [ti * c + j + 1 for j in range(c)]
                if max(band_ids) > meta.count:
                    raise PlanningError(
                        f"source has {meta.count} bands; temporal stack needs {max(band_ids)} "
                        "(time-major C*T band layout)"
                    )
                data = reader.read_window((0, 0, meta.width, meta.height), bands=band_ids)
                nodata_mask = reader.read_mask((0, 0, meta.width, meta.height)) == 0
                chip, _valid = _tcls_pwin(
                    plan, descriptor, data,
                    nodata_mask if nodata_mask.any() else None,
                )
                stack_channels.append(chip)
                perf.note_window(1, bytes_read=int(data.nbytes))
            pixels = np.concatenate(stack_channels, axis=0)[None].astype(np.float32)
            batch = TileBatch(pixels=pixels, valid_mask=None,
                              chip_hw=(meta.height, meta.width))
            ctx.extras["stack_length"] = t
            output = provider.infer(model, batch, ctx)
            output.validate_for(batch)
            seq = output.label_sequence[0]  # (T,K)
        classes = seq.argmax(axis=1).astype(np.uint8)  # (T,)
        conf = seq.max(axis=1).astype(np.float32)
        class_names = (
            list(descriptor.class_schema.classes) if descriptor.class_schema else []
        )
        payload = {
            "times": list(temporal.times),
            "classes": [
                {
                    "time": temporal.times[i],
                    "label": int(classes[i]),
                    "label_name": (
                        class_names[int(classes[i])]
                        if 0 <= int(classes[i]) < len(class_names) else str(int(classes[i]))
                    ),
                    "confidence": round(float(conf[i]), 6),
                }
                for i in range(t)
            ],
        }
        import json as _json

        seq_path = output_dir / "temporal_classification.json"
        seq_path.write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        perf.record_batch(1)
        outputs = {
            "temporal_classification": publish_json_artifact(
                seq_path, owner_scope=request.owner_scope, source_refs=[],
                producer={"capability": "modelops.inference", "task": "temporal_classification"},
            )
        }
        return outputs

    # ── helpers ─────────────────────────────────────────────────────
    def _resolve_task(self, descriptor: GeoModelDescriptor, request: InferenceRequest) -> str:
        if request.task_type:
            if request.task_type not in descriptor.task_types:
                raise ModelOpsError(
                    f"task {request.task_type!r} not offered by model "
                    f"(offers {list(descriptor.task_types)})"
                )
            return request.task_type
        if len(descriptor.task_types) == 1:
            return descriptor.task_types[0]
        raise ModelOpsError(
            f"model offers multiple tasks {list(descriptor.task_types)}; "
            "specify task_type explicitly"
        )

    def _content_identity(self, request: InferenceRequest) -> str:
        """输入内容身份（R1-B1 唯一合法构造点）。

        DataObject 输入 → manifest merkle ``content_sha256``；否则全内容
        流式 sha256（chunk_digest 口径）。**禁止** RasterMetadata.fingerprint。
        """
        if request.input_data_object_id:
            from app.services.lakehouse.data_object import resolve_data_object

            manifest = resolve_data_object(request.input_data_object_id)
            payload = manifest.get("payload") or {}
            content_sha = payload.get("content_sha256") or manifest.get("content_sha256")
            if content_sha:
                return str(content_sha)
        return sha256_of_file(request.source_uri)

    @staticmethod
    def _meters_per_pixel(meta: Any) -> float:
        """米/像素；地理 CRS（度）返回 0 = 未知（R1-C3：不得把度当米，
        否则会误触发分辨率重采样路径）。"""
        if meta.transform is None:
            return 0.0
        if meta.crs:
            try:
                from rasterio.crs import CRS

                if CRS.from_string(meta.crs).is_geographic:
                    return 0.0
            except Exception:  # noqa: BLE001 — 无法解析的 CRS 按未知处理
                return 0.0
        px = float(meta.transform[0])
        py = float(meta.transform[4])
        if px == 0 or py == 0:
            return 0.0
        return round((abs(px) + abs(py)) / 2.0, 6)

    @staticmethod
    def _sampled_nodata_ratio(reader: RasterReader, *, max_side: int = 64) -> float:
        try:
            meta = reader.metadata()
            w = min(meta.width, max_side)
            h = min(meta.height, max_side)
            mask = reader.read_mask((0, 0, w, h))
            if mask.size == 0:
                return 0.0
            return float((mask == 0).sum()) / float(mask.size)
        except Exception:  # noqa: BLE001 — 采样失败 = 未知（0，不阻断）
            return 0.0

    def _reproject(
        self, source: Path, spec: Dict[str, Any], *, run_id: str
    ) -> Tuple[Path, str, Dict[str, Any]]:
        """显式重投影（R1-C2）：有界像素上限；产物 sha256 成为内容身份。"""
        import rasterio
        from rasterio.warp import calculate_default_transform, reproject, Resampling

        resampling_name = spec.get("resampling", "nearest")
        resampling = getattr(Resampling, resampling_name, Resampling.nearest)
        with rasterio.open(source) as src:
            target_crs = spec.get("target_crs") or src.crs
            if target_crs is None:
                raise ResourceUnavailable("cannot reproject without a resolvable target CRS")
            target_res = spec.get("target_m_per_px")
            left, bottom, right, top = src.bounds
            # R1-C3：目标几何必须先在**目标 CRS** 中求解（源 bounds 单位 =
            # 源 CRS；地理→投影时直接用源 bounds 除以米分辨率会得到 ≈1 像素）。
            transform, out_w, out_h = calculate_default_transform(
                src.crs, target_crs, src.width, src.height, left, bottom, right, top
            )
            if target_res:
                # 在目标 CRS 的 bounds 上按目标分辨率重算网格。
                tleft, tbottom, tright, ttop = rasterio.transform.array_bounds(
                    out_h, out_w, transform
                )
                out_w = max(1, int(round((tright - tleft) / target_res)))
                out_h = max(1, int(round((ttop - tbottom) / target_res)))
                transform = rasterio.transform.from_origin(
                    tleft, ttop, target_res, target_res
                )
            if out_w * out_h > 512 * 1024 * 1024:
                raise ResourceUnavailable(
                    f"reprojected raster {out_w}x{out_h} exceeds pixel cap "
                    "(bounded warp policy)"
                )
            out_dir = self._settings.registry_dir / "reprojected"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{run_id}_reprojected.tif"
            profile = {
                "driver": "GTiff", "height": out_h, "width": out_w,
                "count": src.count, "dtype": src.dtypes[0],
                "crs": target_crs, "transform": transform,
                "nodata": src.nodata, "tiled": True,
                "blockxsize": 256, "blockysize": 256, "compress": "deflate",
            }
            with rasterio.open(out_path, "w", **profile) as dst:
                for band in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, band),
                        destination=rasterio.band(dst, band),
                        src_transform=src.transform, src_crs=src.crs,
                        dst_transform=transform, dst_crs=target_crs,
                        resampling=resampling,
                    )
        digest = sha256_of_file(out_path)
        payload = {
            "target_crs": str(target_crs),
            "target_m_per_px": target_res,
            "resampling": resampling_name,
            "out_shape": [out_h, out_w],
            "content_sha256": digest,
        }
        return out_path, digest, payload

    def _write_seg_rasters(
        self,
        source_path: Path,
        classes: np.ndarray,
        confidence: np.ndarray,
        output_dir: Path,
        descriptor: GeoModelDescriptor,
        *,
        window_origin: Optional[Tuple[int, int]] = None,
    ) -> Tuple[Path, Path]:
        reader = RasterReader.open(str(source_path))
        try:
            classes_path = write_raster_output(
                output_dir / "classes.tif",
                arrays=[classes],
                band_names=["class"],
                template=reader,
                nodata=255.0,
                dtype="uint8",
                window_origin=window_origin,
            )
            confidence_path = write_raster_output(
                output_dir / "confidence.tif",
                arrays=[confidence],
                band_names=["confidence"],
                template=reader,
                nodata=0.0,
                dtype="float32",
                window_origin=window_origin,
            )
        finally:
            reader.close()
        return classes_path, confidence_path

    def _vectorize_and_publish(
        self,
        request: InferenceRequest,
        classes: np.ndarray,
        confidence: np.ndarray,
        output_dir: Path,
        descriptor: GeoModelDescriptor,
        *,
        roi_origin: Optional[Tuple[int, int]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """类别栅格 → GeoJSON 多边形（→ 可选 PostGIS 同步发布）。"""
        from affine import Affine

        class_names = (
            list(descriptor.class_schema.classes) if descriptor.class_schema else None
        )
        reader = RasterReader.open(str(request.source_uri))
        try:
            transform = reader.dataset.transform
        finally:
            reader.close()
        if roi_origin is not None:
            transform = transform * Affine.translation(*roi_origin)
        feature_collection = vectorize_class_raster(
            classes,
            transform=transform,
            class_names=class_names,
            confidence=confidence,
            params=VectorizeParams(),
        )
        poly_path = write_geojson_output(
            output_dir / "class_polygons.geojson", feature_collection
        )
        published = publish_json_artifact(
            poly_path,
            owner_scope=request.owner_scope,
            source_refs=[request.input_data_object_id] if request.input_data_object_id else [],
            producer={"capability": "modelops.inference", "role": "class_polygons"},
        )
        result: Dict[str, Dict[str, Any]] = {"class_polygons": published}
        if request.postgis_table:
            from app.services.modelops.geo_output import publish_geojson_to_postgis

            # PostGIS 是增量通道：失败 honest skip（GeoJSON 文件已兜底）。
            result["class_polygons"]["postgis"] = publish_geojson_to_postgis(
                feature_collection,
                table=request.postgis_table,
            )
        return result

    def _publish_raster(
        self,
        path: Path,
        request: InferenceRequest,
        *,
        role: str,
        descriptor: GeoModelDescriptor,
    ) -> Dict[str, Any]:
        result = publish_raster_artifact(
            path,
            owner_scope=request.owner_scope,
            source_refs=[request.input_data_object_id] if request.input_data_object_id else [],
            producer={
                "capability": "modelops.inference",
                "model_id": descriptor.model_id,
                "model_version": descriptor.model_version,
                "role": role,
            },
        )
        result.setdefault("path", str(path))
        return result


class _StackAccumulator:
    """super-resolution 全图重建缓冲（float32 栈；RAM/memmap 两级有界）。

    语义：stride=chip 无重叠 → 每 chip 的输出直接写入对应放大窗口
    （crop 权重语义）；nodata chip 写 0 并在 weight 上记 0（finalize
    可输出有效掩膜语义——SR 栅格本身以 0=nodata 发布）。
    """

    def __init__(self, height: int, width: int, channels: int) -> None:
        self._h = height
        self._w = width
        self._c = channels
        need = channels * height * width * 4
        if need <= MERGE_RAM_BUDGET_BYTES:
            self._canvas = np.zeros((channels, height, width), dtype=np.float32)
            self._memmap_dir: Optional[Path] = None
        else:
            if need > MERGE_DISK_HARD_CAP_BYTES:
                raise ResourceUnavailable(
                    f"super-resolution canvas needs {need} bytes > disk cap "
                    f"{MERGE_DISK_HARD_CAP_BYTES}"
                )
            import tempfile

            self._memmap_dir = Path(tempfile.mkdtemp(prefix="modelops-sr-"))
            self._canvas = np.memmap(
                self._memmap_dir / "sr.npy", dtype=np.float32, mode="w+",
                shape=(channels, height, width),
            )
            self._canvas[:] = 0

    def add_tiles(
        self,
        tiles: Any,
        stacks: np.ndarray,
        *,
        scale: int,
        valid_masks: Optional[np.ndarray] = None,
    ) -> None:
        for i, tile in enumerate(tiles):
            row, col, core_h, core_w = tile.core_window
            out = stacks[i]  # (C, core_h*scale, core_w*scale)
            if out.shape[1] != core_h * scale or out.shape[2] != core_w * scale:
                raise PreprocessError(
                    f"super_resolution chip output {out.shape[1:]} != expected "
                    f"({core_h * scale}, {core_w * scale})"
                )
            if valid_masks is not None:
                vm = valid_masks[i]
                if vm.ndim == 3:
                    vm = vm[0]
                core_vm = vm[:core_h, :core_w]
                if not bool(core_vm.all()):
                    # 部分 nodata 的 chip：无效像元输出置 0（nodata 语义）。
                    vm_up = np.kron(core_vm, np.ones((scale, scale), dtype=bool))
                    out = out * vm_up[None].astype(np.float32)
            self._canvas[
                :,
                row * scale: (row + core_h) * scale,
                col * scale: (col + core_w) * scale,
            ] = out

    def finalize(self) -> np.ndarray:
        # 拷出（memmap 路径视图会锁住映射 → Windows rmtree 失败泄漏）。
        canvas = np.array(self._canvas, dtype=np.float32, copy=True)
        self.close()
        return canvas

    def close(self) -> None:
        if self._memmap_dir is not None:
            import shutil

            mm = getattr(self, "_canvas", None)
            if mm is not None:
                handle = getattr(mm, "_mmap", None)
                if handle is not None:
                    handle.close()  # 显式 unmap（Windows 删除映射文件会失败）
                self._canvas = None
            shutil.rmtree(self._memmap_dir, ignore_errors=True)
            self._memmap_dir = None


def _process_peak_rss_bytes() -> int:
    """进程峰值 RSS（R2-M8）：POSIX resource / win32 ctypes；失败 = 0。"""
    try:
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    except Exception:  # noqa: BLE001
        pass
    try:
        import ctypes

        class _PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        if ctypes.windll.kernel32.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(pmc), pmc.cb
        ):
            return int(pmc.PeakWorkingSetSize)
    except Exception:  # noqa: BLE001
        pass
    return 0


def _emit(
    progress: Optional[Callable[[Dict[str, Any]], None]],
    **payload: Any,
) -> None:
    if progress is None:
        return
    try:
        progress(payload)
    except Exception:  # noqa: BLE001 — 观测回调失败不阻断推理
        logger.debug("progress callback raised", exc_info=True)


class _SegmentationAccumulator:
    """流式概率融合（小栅格 RAM；大栅格 memmap 兜底——均有界）。"""

    def __init__(
        self,
        height: int,
        width: int,
        num_classes: int,
        *,
        policy: Optional[Any] = None,
    ) -> None:
        self._policy = policy or SegmentationMergePolicy()
        self._h = height
        self._w = width
        self._k = num_classes
        need = (num_classes + 1) * height * width * 4
        if need <= MERGE_RAM_BUDGET_BYTES:
            self._acc = np.zeros((num_classes, height, width), dtype=np.float32)
            self._weight = np.zeros((height, width), dtype=np.float32)
            self._memmap_dir: Optional[Path] = None
        else:
            if need > MERGE_DISK_HARD_CAP_BYTES:
                raise ResourceUnavailable(
                    f"merge buffers need {need} bytes > disk cap {MERGE_DISK_HARD_CAP_BYTES}"
                )
            import tempfile

            self._memmap_dir = Path(tempfile.mkdtemp(prefix="modelops-merge-"))
            self._acc = np.memmap(
                self._memmap_dir / "acc.npy", dtype=np.float32, mode="w+",
                shape=(num_classes, height, width),
            )
            self._weight = np.memmap(
                self._memmap_dir / "weight.npy", dtype=np.float32, mode="w+",
                shape=(height, width),
            )
            self._acc[:] = 0
            self._weight[:] = 0

    def add_tiles(
        self,
        tiles: Any,
        probabilities: np.ndarray,
        *,
        valid_masks: Optional[np.ndarray] = None,
    ) -> None:
        """累加一批 tile 的概率；无效像元（nodata/pad）权重置零（R1-m5）。

        ``valid_masks``: (N,1,H,W) bool，None=全有效。权重实现与
        stitching.merge_segmentation 共享（``_core_weights``，R1-M4）。
        """
        from app.lib.modelops.stitching import _core_weights

        for i, tile in enumerate(tiles):
            probs = probabilities[i]
            row, col, core_h, core_w = tile.core_window
            off_y = row - tile.read_window[0] + tile.pad[1]
            off_x = col - tile.read_window[1] + tile.pad[0]
            core_probs = probs[:, off_y: off_y + core_h, off_x: off_x + core_w]
            weights = _core_weights(self._policy, core_h, core_w)
            if valid_masks is not None:
                vm = valid_masks[i]
                if vm.ndim == 3:
                    vm = vm[0]
                vm_core = vm[off_y: off_y + core_h, off_x: off_x + core_w]
                weights = weights * vm_core.astype(np.float32)
            self._acc[:, row: row + core_h, col: col + core_w] += core_probs * weights[None]
            self._weight[row: row + core_h, col: col + core_w] += weights

    def finalize(
        self,
        *,
        input_nodata: Optional[np.ndarray],
        confidence_floor: float,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Optional[np.ndarray]]:
        want_probs = bool(getattr(self._policy, "output_probabilities", False))
        if want_probs:
            need = self._k * self._h * self._w * 4
            if need > MERGE_RAM_BUDGET_BYTES:
                raise ResourceUnavailable(
                    f"probabilities export needs {need} bytes > merge RAM budget; "
                    "retry without output_probabilities"
                )
            probs_out: Optional[np.ndarray] = np.zeros(
                (self._k, self._h, self._w), dtype=np.float32
            )
        else:
            probs_out = None
        # C-2：行带处理——全尺寸 (K,H,W) 中间量永不物化（memmap 路径
        # 的 finalize 峰值 RAM 与带宽成正比，而非 K×H×W）。
        classes = np.empty((self._h, self._w), dtype=np.uint8)
        confidence = np.empty((self._h, self._w), dtype=np.float32)
        valid = np.empty((self._h, self._w), dtype=bool)
        acc_arr = np.asarray(self._acc)
        weight_arr = np.asarray(self._weight)
        band = 4096
        for y0 in range(0, self._h, band):
            y1 = min(y0 + band, self._h)
            w_band = weight_arr[y0:y1]
            cov_band = w_band > 0
            safe = np.where(cov_band, w_band, 1.0).astype(np.float32)
            mean_band = acc_arr[:, y0:y1] / safe[None]
            cls_band = mean_band.argmax(axis=0).astype(np.uint8)
            cls_band[~cov_band] = 255  # 未覆盖（含 nodata/pad）不是类别 0
            classes[y0:y1] = cls_band
            confidence[y0:y1] = mean_band.max(axis=0).astype(np.float32)
            valid[y0:y1] = cov_band
            if probs_out is not None:
                probs_out[:, y0:y1] = mean_band.astype(np.float32)
        # 视图用尽后再 unmap（Windows：映射句柄存活时 rmtree 会失败泄漏）。
        del acc_arr, weight_arr
        self.close()
        return classes, confidence, valid, probs_out

    def close(self) -> None:
        if self._memmap_dir is not None:
            import shutil

            for attr in ("_acc", "_weight"):
                mm = getattr(self, attr, None)
                if mm is not None:
                    handle = getattr(mm, "_mmap", None)
                    if handle is not None:
                        handle.close()  # 显式 unmap（否则 Windows 删目录失败）
                    setattr(self, attr, None)
            shutil.rmtree(self._memmap_dir, ignore_errors=True)
            self._memmap_dir = None
