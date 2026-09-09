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
    TASK_CLASSIFICATION,
    TASK_EMBEDDING,
    TASK_INSTANCE_SEGMENTATION,
    TASK_OBJECT_DETECTION,
    TASK_PROMPTABLE_SEGMENTATION,
    TASK_SEMANTIC_SEGMENTATION,
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
    collect_embeddings,
    merge_detections,
    merge_instances,
)
from app.lib.modelops.temporal import TemporalStackSpec
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
    score_threshold: float = 0.5
    confidence_floor: float = 0.0
    device_override: Optional[str] = None
    seed: Optional[int] = None
    deadline_s: Optional[float] = None
    input_data_object_id: Optional[str] = None
    polygonize_instances: bool = False
    output_probabilities: bool = False
    output_dir: Optional[Path] = None


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
    ) -> None:
        self._registry = registry
        self._providers = providers
        self._settings = settings or ModelOpsSettings.load()
        self._loaded_cache = loaded_cache or LoadedModelCache(
            max_models=self._settings.max_loaded_models
        )
        self._reuse = reuse_store
        self._slots = threading.BoundedSemaphore(self._settings.max_concurrent_inferences)

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
        run_id = uuid.uuid4().hex[:16]
        perf = PerfCounters()
        queued_from = time.perf_counter()
        if not self._slots.acquire(timeout=30.0):
            raise ResourceUnavailable("concurrent inference slots exhausted (30s wait)")
        perf.note_latency(queue_wait=time.perf_counter() - queued_from)
        try:
            return self._run_guarded(request, run_id=run_id, perf=perf,
                                     cancel_token=cancel_token, progress=progress)
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
        caps = provider.capabilities()
        task = self._resolve_task(descriptor, request)

        # ── 输入身份（B1 唯一口径）+ profile ────────────────────────
        _emit(progress, stage="profile", run_id=run_id)
        input_content_sha = self._content_identity(request)
        with RasterReader.open(request.source_uri) as reader:
            meta = reader.metadata()
            m_per_px = self._meters_per_pixel(meta)
            nodata_ratio = self._sampled_nodata_ratio(reader)
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

        # ── 兼容性资格 ──────────────────────────────────────────────
        report = qualify(descriptor, profile, prompt=request.prompt, temporal=request.temporal)
        if not report.compatible:
            raise report_to_error(report)

        # ── ReprojectStage（R1-C2：显式、有界、进指纹）──────────────
        reproject_payload: Optional[Dict[str, Any]] = None
        source_path = Path(request.source_uri)
        if report.reproject:
            _emit(progress, stage="reproject", run_id=run_id)
            source_path, input_content_sha, reproject_payload = self._reproject(
                source_path, report.reproject, run_id=run_id
            )
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
        preprocess_plan = build_preprocess_plan(descriptor, source_band_count=profile.band_count)
        tile_plan = plan_tiles(descriptor, raster_height=meta.height, raster_width=meta.width)
        perf.chips_total = len(tile_plan.tiles)
        provider_payload = {
            "provider_ref": descriptor.provider_ref,
            "provider_id": caps.provider_id,
            "provider_type": caps.provider_type,
            "semantic_version": caps.semantic_version,
            "capabilities": caps.as_dict(),
        }
        postprocess_payload = {
            "score_threshold": request.score_threshold,
            "confidence_floor": request.confidence_floor,
            "output_probabilities": request.output_probabilities,
            "polygonize_instances": request.polygonize_instances,
        }
        prompt_payload = request.prompt.to_payload() if request.prompt else None
        temporal_payload = request.temporal.to_payload() if request.temporal else None
        input_payload = {
            "source_uri": str(source_path),
            "content_sha256": input_content_sha,
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

        # ── 设备与资源计划 ──────────────────────────────────────────
        device_plan = resolve_device_plan(descriptor, caps, device_override=request.device_override)
        estimate = provider.estimate_resources(descriptor, batch=caps.max_batch, device=device_plan.device)
        budget = self._settings.vram_budget_bytes
        if caps.provider_type == "extension_worker":
            budget = min(budget, EXTENSION_BATCH_BYTES_CAP)  # R1-M5 帧上限约束
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
        device_plan = DevicePlan(
            device=device_plan.device,
            batch=batch,
            vram_bytes=estimate.vram_bytes * batch,
            host_ram_bytes=estimate.host_ram_bytes * batch,
            accounting="externally_enforced" if estimate.externally_enforced else "provider_visible",
        )
        perf.note_resources(
            estimated_vram_bytes=device_plan.vram_bytes, device=device_plan.device
        )

        # ── loaded model（single-flight cache）──────────────────────
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
        perf.note_latency(load=load_latency)
        provider.warmup(model)

        if request.temporal is not None:
            ctx.extras["missing_policy"] = request.temporal.missing_policy
            ctx.extras["stack_length"] = len(request.temporal.times)
            ctx.extras["output_time_semantics"] = request.temporal.output_time_semantics
        if request.prompt is not None:
            ctx.extras["prompt"] = request.prompt.to_payload()
            ctx.extras["prompt_mask_arrays"] = request.prompt.prior_masks

        # ── 任务执行 ────────────────────────────────────────────────
        _emit(progress, stage="infer", run_id=run_id, tiles_total=len(tile_plan))
        output_dir = Path(request.output_dir or (self._settings.registry_dir / "outputs" / run_id))
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
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
            else:
                outputs = self._run_tiled(
                    request, task, descriptor, provider, model, ctx, source_path,
                    tile_plan, batch, device_plan, preprocess_plan, output_dir,
                    perf, _checkpoint, progress,
                )
        finally:
            self._loaded_cache.release(cache_key)
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

    # ── 有界 tile 循环（segmentation/detection/instance/embedding）──
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
    ) -> Dict[str, Dict[str, Any]]:
        num_classes = len(descriptor.class_schema.classes) if descriptor.class_schema else 2
        accumulator: Optional["_SegmentationAccumulator"] = None
        if task == TASK_SEMANTIC_SEGMENTATION:
            accumulator = _SegmentationAccumulator(
                tile_plan.raster_height, tile_plan.raster_width, num_classes
            )
        detections_by_tile: List[List[Dict[str, Any]]] = []
        instance_by_tile: List[np.ndarray] = []
        instance_classes_by_tile: List[Dict[int, int]] = []
        embeddings: List[np.ndarray] = []
        label_outputs: List[np.ndarray] = []
        oom_downshifts = 0
        current_batch = batch
        warm_latency: Optional[float] = None

        with RasterReader.open(str(source_path)) as reader:
            band_ids = [i + 1 for i in preprocess_plan.band_indices]
            start = 0
            while start < len(tile_plan.tiles):
                group = tile_plan.tiles[start: start + current_batch]
                checkpoint()
                windows = []
                for tile in group:
                    col, row, w, h = (tile.read_window[1], tile.read_window[0],
                                      tile.read_window[3], tile.read_window[2])
                    data = reader.read_window((col, row, w, h), bands=band_ids)
                    mask = reader.read_mask((col, row, w, h)) == 0
                    windows.append((data, mask if mask.any() else None))
                    perf.note_window(1, bytes_read=int(data.nbytes))
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
                    accumulator.add_tiles(tile_plan.tiles[start: start + len(group)],
                                          output.class_probabilities)
                elif task == TASK_OBJECT_DETECTION:
                    detections_by_tile.append(list(output.detections or []))
                elif task == TASK_INSTANCE_SEGMENTATION:
                    instance_by_tile.append(output.instance_masks[0]
                                            if output.instance_masks.ndim == 3
                                            else output.instance_masks)
                    instance_classes_by_tile.append({1: 1})
                elif task in (TASK_EMBEDDING, TASK_CLASSIFICATION):
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
            checkpoint()

        outputs: Dict[str, Dict[str, Any]] = {}
        if accumulator is not None:
            classes, confidence, valid = accumulator.finalize(
                input_nodata=None,
                confidence_floor=request.confidence_floor,
            )
            classes_path, confidence_path = self._write_seg_rasters(
                source_path, classes, confidence, output_dir, descriptor
            )
            outputs["classes"] = self._publish_raster(
                classes_path, request, role="classes", descriptor=descriptor
            )
            outputs["confidence"] = self._publish_raster(
                confidence_path, request, role="confidence", descriptor=descriptor
            )
            perf.note_merge(int(classes.size))
        elif task == TASK_OBJECT_DETECTION:
            records = merge_detections(
                tile_plan, detections_by_tile,
                score_threshold=request.score_threshold,
            )
            class_names = list(descriptor.class_schema.classes) if descriptor.class_schema else None
            with RasterReader.open(str(source_path)) as reader:
                geojson = build_geojson_from_detections(
                    [r.as_dict() for r in records],
                    crs=reader.metadata().crs,
                    transform=reader.dataset.transform,
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

    # ── promptable 单窗口路径 ───────────────────────────────────────
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
        from app.lib.modelops.preprocess import preprocess_window

        prompt = request.prompt
        if prompt is None:
            raise PreprocessError("promptable inference requires a prompt")
        checkpoint()
        # 窗口 = prompt 几何包围盒 + chip 级 margin，clamp 到栅格。
        xs: List[float] = []
        ys: List[float] = []
        for px, py in prompt.points:
            xs.append(px)
            ys.append(py)
        for bx, by, bw, bh in prompt.boxes:
            xs.extend([bx, bx + bw])
            ys.extend([by, by + bh])
        if not xs:
            # 仅 mask prompt：全幅窗口（cap：promptable 参考路径为单窗口）。
            xs = [0.0, 1.0]
            ys = [0.0, 1.0]
        chip_w, chip_h = descriptor.spatial.chip_size
        margin_x, margin_y = chip_w, chip_h
        x0 = max(0, int(min(xs)) - margin_x)
        y0 = max(0, int(min(ys)) - margin_y)
        x1 = int(max(xs)) + margin_x
        y1 = int(max(ys)) + margin_y
        with RasterReader.open(str(source_path)) as reader:
            meta = reader.metadata()
            x1 = min(meta.width, x1)
            y1 = min(meta.height, y1)
            win_w, win_h = max(4, x1 - x0), max(4, y1 - y0)
            band_ids = [i + 1 for i in range(descriptor.input_bands)]
            data = reader.read_window((x0, y0, win_w, win_h), bands=band_ids)
            nodata_mask = reader.read_mask((x0, y0, win_w, win_h)) == 0
            perf.note_window(1, bytes_read=int(data.nbytes))
            plan = build_preprocess_plan(descriptor, source_band_count=meta.count)
            chip, valid = preprocess_window(plan, descriptor, data,
                                            nodata_mask if nodata_mask.any() else None)
            # prompt 坐标平移到窗口像素坐标（prior mask 为全幅栅格尺寸数组）。
            window_prompt = PromptSpec(
                points=tuple((px - x0, py - y0) for px, py in prompt.points),
                boxes=tuple((bx - x0, by - y0, bw, bh) for bx, by, bw, bh in prompt.boxes),
                prior_masks=tuple(
                    m[y0:y1, x0:x1] for m in prompt.prior_masks
                ) if prompt.prior_masks else (),
                text=prompt.text,
                combine=prompt.combine,
                labels=prompt.labels,
            )
            ctx.extras["prompt"] = window_prompt.to_payload()
            ctx.extras["prompt_mask_arrays"] = window_prompt.prior_masks
            batch = TileBatch(pixels=chip[None], valid_mask=valid[None, None]
                              if valid.ndim == 2 and not bool(valid.all()) else None,
                              chip_hw=(win_h, win_w))
            output = provider.infer(model, batch, ctx)
            output.validate_for(batch)
            probs = output.class_probabilities[0]  # (2,H,W)
            object_mask = probs.argmax(axis=0) == 1
            if valid.ndim == 2:
                object_mask &= valid
            mask_arr = object_mask.astype(np.uint8)
        mask_path = write_raster_output(
            output_dir / "prompt_mask.tif",
            arrays=[mask_arr],
            band_names=["object"],
            template=RasterReader.open(str(source_path)),
            nodata=255.0,
        )
        perf.note_window(0, bytes_read=0)
        perf.record_batch(1)
        outputs: Dict[str, Dict[str, Any]] = {
            "prompt_mask": self._publish_raster(mask_path, request, role="prompt_mask",
                                                descriptor=descriptor)
        }
        try:
            from rasterio import features as _features

            geojson = {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {"class": 1}, "geometry": geom}
                    for geom, val in _features.shapes(
                        object_mask.astype(np.uint8), mask=object_mask, connectivity=4
                    )
                ],
            }
            poly_path = write_geojson_output(output_dir / "prompt_mask.geojson", geojson)
            outputs["prompt_mask_geojson"] = publish_json_artifact(
                poly_path, owner_scope=request.owner_scope, source_refs=[],
                producer={"capability": "modelops.promptable_inference"},
            )
        except Exception as exc:  # noqa: BLE001 — polygon 化失败不毁主产物
            logger.warning("prompt mask polygonize failed: %s", exc)
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
        if meta.transform is None:
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
            step_x = max(1, meta.width // max_side)
            step_y = max(1, meta.height // max_side)

            col = 0
            row = 0
            w = min(meta.width, step_x * max_side)
            h = min(meta.height, step_y * max_side)
            mask = reader.read_mask((col, row, w, h))
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
            if target_res:
                out_w = max(1, int(round((right - left) / target_res)))
                out_h = max(1, int(round((top - bottom) / target_res)))
                transform = rasterio.transform.from_origin(
                    left, top, target_res, target_res
                )
            else:
                transform, out_w, out_h = calculate_default_transform(
                    src.crs, target_crs, src.width, src.height, left, bottom, right, top
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
            )
            confidence_path = write_raster_output(
                output_dir / "confidence.tif",
                arrays=[confidence],
                band_names=["confidence"],
                template=reader,
                nodata=0.0,
                dtype="float32",
            )
        finally:
            reader.close()
        return classes_path, confidence_path

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

    def __init__(self, height: int, width: int, num_classes: int) -> None:
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

    def add_tiles(self, tiles: Any, probabilities: np.ndarray) -> None:

        for i, tile in enumerate(tiles):
            probs = probabilities[i]
            row, col, core_h, core_w = tile.core_window
            off_y = row - tile.read_window[0] + tile.pad[1]
            off_x = col - tile.read_window[1] + tile.pad[0]
            core_probs = probs[:, off_y: off_y + core_h, off_x: off_x + core_w]
            weights = np.ones((core_h, core_w), dtype=np.float32)
            self._acc[:, row: row + core_h, col: col + core_w] += core_probs * weights[None]
            self._weight[row: row + core_h, col: col + core_w] += weights

    def finalize(
        self,
        *,
        input_nodata: Optional[np.ndarray],
        confidence_floor: float,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        covered = np.asarray(self._weight) > 0
        safe = np.where(covered, np.asarray(self._weight), 1.0).astype(np.float32)
        mean = np.asarray(self._acc) / safe[None]
        classes = mean.argmax(axis=0).astype(np.uint8)
        confidence = mean.max(axis=0).astype(np.float32)
        valid = covered.copy()
        if confidence_floor > 0:
            low = confidence < confidence_floor
            classes[low] = 255
            valid &= ~low
        if input_nodata is not None:
            valid &= ~input_nodata
            classes[input_nodata] = 255
        self.close()
        return classes, confidence, valid

    def close(self) -> None:
        if self._memmap_dir is not None:
            import shutil

            del self._acc
            del self._weight
            shutil.rmtree(self._memmap_dir, ignore_errors=True)
            self._memmap_dir = None
