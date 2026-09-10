"""ModelOpsService —— agent 工具层唯一入口（ADR-0119 §2）。

聚合 registry/providers/engine/evaluation/cancel；工具层只见 typed
capabilities 与结果，不触 provider internals（Epic §N）。异步工具经
``asyncio.to_thread`` offload（引擎为同步有界执行）。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from app.lib.cancellation import CancellationToken
from app.lib.modelops.compatibility import InputProfile, qualify
from app.lib.modelops.errors import ModelOpsError
from app.lib.modelops.resources import batch_for_budget
from app.lib.geo_raster.reader import RasterReader
from app.services.modelops.config import ModelOpsSettings
from app.services.modelops.engine import InferenceEngine, InferenceRequest, InferenceResult
from app.services.modelops.evaluation_service import EvaluationRequest, EvaluationService
from app.services.modelops.package_store import ModelPackageStore
from app.services.modelops.providers.base import ProviderRegistry, resolve_device_plan
from app.services.modelops.registry import ModelRegistryStore
from app.services.modelops.reuse import ReuseStore
from app.services.modelops.seeds import seed_providers, seed_registry

logger = logging.getLogger(__name__)


def normalize_scope(
    *, session_id: Optional[str] = None, project_id: Optional[str] = None
) -> Dict[str, str]:
    if bool(session_id) == bool(project_id):
        raise ModelOpsError("owner scope requires exactly one of session_id / project_id")
    return {"session_id": session_id} if session_id else {"project_id": project_id}  # type: ignore[return-value]


class ModelOpsService:
    """进程级 ModelOps 门面（懒初始化；种子幂等）。"""

    def __init__(self, settings: Optional[ModelOpsSettings] = None) -> None:
        self._settings = settings or ModelOpsSettings.load()
        self._registry = ModelRegistryStore(self._settings)
        self._packages = ModelPackageStore(self._settings.registry_dir / "packages")
        self._providers = ProviderRegistry()
        seed_providers(self._providers)
        self._wire_dl_providers()
        self._wire_remote_providers()
        seed_registry(self._registry, self._providers)
        self._reuse = ReuseStore(
            self._settings.registry_dir / "reuse",
            max_entries=self._settings.reuse_max_entries,
            max_bytes=self._settings.reuse_max_bytes,
        )
        self._engine = InferenceEngine(
            self._registry,
            self._providers,
            self._settings,
            reuse_store=self._reuse,
        )
        self._evaluation = EvaluationService()
        self._cancel_lock = threading.Lock()
        self._cancel_tokens: Dict[str, CancellationToken] = {}

    def _wire_dl_providers(self) -> None:
        """V3 §B：ONNX Runtime / TorchScript / subprocess provider 接线。

        探测驱动：onnxruntime 缺席时 provider 仍注册（capabilities 如实
        反映 devices/tasks），load 时 typed 失败——注册面永不冒充可用，
        也不因运行时缺席而拒绝注册（honest failure，不阻塞主路径）。
        """
        from app.services.modelops.providers.onnx_adapter import OnnxRuntimeProvider
        from app.services.modelops.providers.subprocess_adapter import (
            SubprocessWorkerProvider,
            parse_worker_allowlist,
        )
        from app.services.modelops.providers.torch_adapter import TorchScriptProvider

        for provider in (
            OnnxRuntimeProvider(self._packages),
            TorchScriptProvider(self._packages),
        ):
            try:
                self._providers.register(provider)
            except Exception as exc:  # noqa: BLE001 — 单 provider 注册失败不阻断启动
                logger.warning(
                    "dl provider %s not registered: %s",
                    provider.capabilities().provider_id, exc,
                )
        try:
            allowlist = parse_worker_allowlist(self._settings.subprocess_workers)
        except Exception as exc:  # noqa: BLE001 — operator 配置错误不阻断启动
            logger.warning("subprocess worker allowlist rejected: %s", exc)
            return
        for slot, script_path in allowlist.items():
            try:
                self._providers.register(
                    SubprocessWorkerProvider(
                        slot,
                        script_path,
                        packages=self._packages,
                        deadline_s=self._settings.subprocess_deadline_s,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("subprocess provider %s not registered: %s", slot, exc)

    # ── 注册面（V3 §B：真实包的注册唯一入口）────────────────────────
    def register_model(
        self,
        descriptor: Any,
        *,
        owner_scope: Dict[str, str],
        package_bytes: Optional[bytes] = None,
        registered_by: str = "",
    ) -> Dict[str, Any]:
        """注册一个模型（真实包过 package_security 门 + 内容寻址落盘）。

        descriptor 接受 :class:`GeoModelDescriptor` 或其 JSON dict
        （工具面友好）；synthetic（无包）种子路径仍走 ``seeds.py``。
        """
        from app.lib.modelops.descriptor import GeoModelDescriptor
        from app.lib.modelops.package_security import (
            SINGLE_FILE_FORMAT_SUFFIXES,
            inspect_archive,
            inspect_model_file,
        )

        if not isinstance(descriptor, GeoModelDescriptor):
            descriptor = GeoModelDescriptor.model_validate(descriptor)
        package_report: Optional[Dict[str, Any]] = None
        if package_bytes is not None:
            single_suffix = SINGLE_FILE_FORMAT_SUFFIXES.get(descriptor.artifact_format)
            if single_suffix is not None:
                report = inspect_model_file(
                    package_bytes,
                    expected_checksum=descriptor.checksum,
                    allowed_suffix=single_suffix,
                )
            else:
                report = inspect_archive(package_bytes, expected_checksum=descriptor.checksum)
            package_report = report.as_dict()
            self._packages.persist(descriptor, package_bytes)
        record = self._registry.register(
            descriptor,
            owner_scope=owner_scope,
            registered_by=registered_by or "modelops.service.register_model",
            package_report=package_report,
            known_provider_refs=self._providers.has,
        )
        return {
            "model_id": record.descriptor.model_id,
            "model_version": record.descriptor.model_version,
            "checksum": record.descriptor.checksum,
            "owner_scope": dict(record.owner_scope),
            "package_stored": package_bytes is not None,
            "seq": record.seq,
        }

    def _wire_remote_providers(self) -> None:
        """operator allowlist 中的 endpoint → 每个注册一个 remote 实例。

        实例 id = ``remote@<endpoint>``；descriptor.provider_type 必须
        = remote_endpoint 且 provider_ref 指向本实例（C1 门照常生效）。
        """
        from app.services.modelops.providers.remote_client import (
            RemoteEndpointPolicy,
            RemoteInferenceProvider,
        )

        policy = RemoteEndpointPolicy(allowlist=tuple(self._settings.remote_allowlist))
        for endpoint in self._settings.remote_allowlist:
            try:
                self._providers.register(
                    RemoteInferenceProvider(endpoint, provider_id=f"remote@{endpoint}", policy=policy)
                )
            except Exception as exc:  # noqa: BLE001 — 非法 allowlist 条目不阻断启动
                logger.warning("remote provider for %s not registered: %s", endpoint, exc)

    # ── 查询面（Epic §N：list/inspect/estimate）─────────────────────
    def list_models(
        self,
        *,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        records = self._registry.list_models(
            session_id=session_id, project_id=project_id, task_type=task_type
        )
        return [
            {
                "model_id": r.descriptor.model_id,
                "model_version": r.descriptor.model_version,
                "task_types": list(r.descriptor.task_types),
                "provider_type": r.descriptor.provider_type,
                "checksum": r.descriptor.checksum[:12] + "…",
                "owner_scope": r.owner_scope,
                "license": r.descriptor.license,
            }
            for r in records
        ]

    def inspect_model(
        self,
        model_id: str,
        *,
        model_version: Optional[str] = None,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = self._registry.resolve(
            model_id, model_version,
            session_id=session_id, project_id=project_id,
        )
        d = record.descriptor
        caps = self._providers.get(d.provider_ref).capabilities()
        return {
            "descriptor": d.as_dict(),
            "provider_capabilities": caps.as_dict(),
            "owner_scope": record.owner_scope,
            "revision": record.revision,
            "package_report": record.package_report,
        }

    def check_compatibility(
        self,
        model_id: str,
        source_uri: str,
        *,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = self._registry.resolve(
            model_id, None, session_id=session_id, project_id=project_id
        )
        profile = self._input_profile(source_uri)
        report = qualify(record.descriptor, profile)
        return {
            "model_id": record.descriptor.model_id,
            "input_profile": profile.as_dict(),
            "compatibility": report.as_dict(),
        }

    def estimate_resources(
        self,
        model_id: str,
        source_uri: str,
        *,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = self._registry.resolve(
            model_id, None, session_id=session_id, project_id=project_id
        )
        d = record.descriptor
        provider = self._providers.get(d.provider_ref)
        caps = provider.capabilities()
        device_plan = resolve_device_plan(d, caps)
        estimate = provider.estimate_resources(d, batch=1, device=device_plan.device)
        with RasterReader.open(source_uri) as reader:
            meta = reader.metadata()
        from app.lib.modelops.planning import plan_tiles

        tile_plan = plan_tiles(d, raster_height=meta.height, raster_width=meta.width)
        batch = batch_for_budget(
            chip_hw=(tile_plan.context_h, tile_plan.context_w),
            input_channels=d.input_bands,
            output_channels=len(d.class_schema.classes) + 1 if d.class_schema else 4,
            bytes_budget=max(1024, self._settings.vram_budget_bytes),
            max_batch=caps.max_batch,
            recommended_batch=estimate.recommended_batch,
        )
        return {
            "model_id": d.model_id,
            "device": device_plan.device,
            "batch": batch,
            "tiles": len(tile_plan),
            "estimated_vram_bytes": estimate.vram_bytes * batch,
            "estimated_host_ram_bytes": estimate.host_ram_bytes * batch,
            "externally_enforced": estimate.externally_enforced,
        }

    # ── 推理面 ──────────────────────────────────────────────────────
    def run_inference(
        self,
        request: InferenceRequest,
        *,
        progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_key: Optional[str] = None,
    ) -> InferenceResult:
        # R1-M5：取消键 = 本次 run 的唯一键（run_id），绝不默认 model_id
        # （同模型并发跑会互相覆盖取消令牌）。
        import uuid as _uuid

        key = cancel_key or _uuid.uuid4().hex[:16]
        request = _replace_request(request, run_key=key)
        token = self._register_cancel(run_key=key)
        try:
            return self._engine.run(request, cancel_token=token, progress=progress)
        finally:
            self._unregister_cancel(key, token)

    async def run_inference_async(
        self, request: InferenceRequest, *, progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_key: Optional[str] = None,
    ) -> InferenceResult:
        import uuid as _uuid

        key = cancel_key or _uuid.uuid4().hex[:16]
        request = _replace_request(request, run_key=key)
        token = self._register_cancel(run_key=key)
        try:
            return await asyncio.to_thread(
                self._engine.run, request, cancel_token=token, progress=progress
            )
        finally:
            self._unregister_cancel(key, token)

    def cancel(self, key: str, *, reason: str = "cancelled by caller") -> bool:
        with self._cancel_lock:
            token = self._cancel_tokens.get(key)
        if token is None:
            return False
        return token.cancel(reason)

    def _register_cancel(self, *, run_key: str) -> CancellationToken:
        token = CancellationToken(job_id=run_key)
        with self._cancel_lock:
            if run_key in self._cancel_tokens:
                raise ModelOpsError(
                    f"cancel key {run_key!r} already in flight "
                    "(pass a distinct cancel_key or omit it)"
                )
            self._cancel_tokens[run_key] = token
        return token

    def _unregister_cancel(self, run_key: str, token: CancellationToken) -> None:
        with self._cancel_lock:
            if self._cancel_tokens.get(run_key) is token:
                self._cancel_tokens.pop(run_key, None)

    async def check_compatibility_async(self, model_id: str, source_uri: str, **kw: Any) -> Dict[str, Any]:
        """R2-M10：GDAL IO 不上事件循环。"""
        return await asyncio.to_thread(
            self.check_compatibility, model_id, source_uri, **kw
        )

    async def estimate_resources_async(self, model_id: str, source_uri: str, **kw: Any) -> Dict[str, Any]:
        """R2-M10：plan_tiles/reader 不上事件循环（C-1 同源守门）。"""
        return await asyncio.to_thread(
            self.estimate_resources, model_id, source_uri, **kw
        )

    # ── 评估 / 复用 / provenance ────────────────────────────────────
    def evaluate(self, request: EvaluationRequest) -> Dict[str, Any]:
        return self._evaluation.evaluate(request)

    async def evaluate_async(self, request: EvaluationRequest) -> Dict[str, Any]:
        return await asyncio.to_thread(self._evaluation.evaluate, request)

    def compare_results(
        self,
        manifest_a: Dict[str, Any],
        manifest_b: Dict[str, Any],
    ) -> Dict[str, Any]:
        """两个推理 manifest 的差异对比（模型/参数/性能/复用身份）。"""
        def side(manifest: Dict[str, Any]) -> Dict[str, Any]:
            perf = manifest.get("performance") or {}
            return {
                "model": (manifest.get("model") or {}).get("model_id"),
                "model_version": (manifest.get("model") or {}).get("model_version"),
                "checksum": (manifest.get("model") or {}).get("checksum", "")[:12],
                "provider": (manifest.get("provider") or {}).get("provider_ref"),
                "device": (manifest.get("device_plan") or {}).get("device"),
                "batch": (manifest.get("device_plan") or {}).get("batch"),
                "miou": None,
                "pixels_per_s": perf.get("pixels_per_s"),
                "tiles_per_s": perf.get("tiles_per_s"),
                "bytes_read": perf.get("bytes_read"),
                "cache": {"hits": perf.get("cache_hits"), "misses": perf.get("cache_misses")},
            }

        a, b = side(manifest_a), side(manifest_b)
        differences = {k: {"a": a.get(k), "b": b.get(k)} for k in a if a.get(k) != b.get(k)}
        return {"a": a, "b": b, "differences": differences}

    def inspect_provenance(
        self,
        manifest: Dict[str, Any],
        *,
        section: Optional[str] = None,
    ) -> Dict[str, Any]:
        """manifest 检视（section = model|provider|input|preprocess|tile_plan|
        postprocess|performance|outputs；默认全部——已 redact）。"""
        if section:
            if section not in manifest:
                raise ModelOpsError(f"manifest has no section {section!r}")
            return {section: manifest[section]}
        return manifest

    def reuse_stats(self, *, owner_scope: Dict[str, str]) -> Dict[str, Any]:
        return self._reuse.stats(owner_scope=owner_scope)

    # ── internals ───────────────────────────────────────────────────
    @staticmethod
    def _input_profile(source_uri: str) -> InputProfile:
        with RasterReader.open(source_uri) as reader:
            meta = reader.metadata()
            try:
                mask = reader.read_mask(
                    (0, 0, min(meta.width, 256), min(meta.height, 256))
                )
                nodata_ratio = float((mask == 0).sum()) / float(max(1, mask.size))
            except Exception:  # noqa: BLE001 — 采样失败 = 未知
                nodata_ratio = 0.0
        m_per_px = 0.0
        if meta.transform:
            px, py = abs(float(meta.transform[0])), abs(float(meta.transform[4]))
            if px and py:
                m_per_px = round((px + py) / 2.0, 6)
        return InputProfile(
            width=meta.width,
            height=meta.height,
            band_count=meta.count,
            dtype=meta.dtype,
            crs=meta.crs,
            m_per_px=m_per_px,
            nodata=meta.nodata,
            nodata_ratio=nodata_ratio,
        )


def _replace_request(request: InferenceRequest, *, run_key: str) -> InferenceRequest:
    """frozen dataclass 的 run_key 注入（取消键 = run_id 的唯一通道）。"""
    from dataclasses import replace

    return replace(request, run_key=run_key)


_SERVICE: Optional[ModelOpsService] = None
_SERVICE_LOCK = threading.Lock()


def get_modelops_service(settings: Optional[ModelOpsSettings] = None) -> ModelOpsService:
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = ModelOpsService(settings)
    return _SERVICE


def reset_modelops_service() -> None:
    """测试钩子（进程单例复位）。"""
    global _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = None
