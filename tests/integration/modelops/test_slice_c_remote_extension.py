"""Slice C：remote / extension provider 路径（可取消、资源计划、评估、provenance）。

平台门（基线 m3）：本文件全部为 in-process adapter/HTTP 语义，无 RLIMIT
依赖；worker stderr 断言不存在（win32 兼容）。
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from app.services.modelops.engine import InferenceRequest
from app.lib.modelops.errors import (
    ProviderError,
    RemoteEndpointPolicyError,
    RemoteInferenceError,
)
from app.services.modelops.providers.fake_remote import FakeRemoteInferenceServer
from app.services.modelops.providers.remote_client import (
    RemoteEndpointPolicy,
    RemoteInferenceProvider,
)

from .conftest import register_remote_model


def test_slice_c_remote_end_to_end(service, isolated_blobs, synthetic_raster):
    server = FakeRemoteInferenceServer()
    endpoint = server.start()
    try:
        service._settings.remote_allowlist.append(endpoint)
        from app.services.modelops.providers.remote_client import (
            RemoteEndpointPolicy,
            RemoteInferenceProvider,
        )

        policy = RemoteEndpointPolicy(allowlist=(endpoint,))
        service._providers.register(
            RemoteInferenceProvider(endpoint, provider_id=f"remote@{endpoint}", policy=policy)
        )
        register_remote_model(service, endpoint)
        result = service.run_inference(
            InferenceRequest(
                model_id="remote-seg",
                source_uri=str(synthetic_raster),
                owner_scope={"session_id": "s1"},
                task_type="semantic_segmentation",
            )
        )
        assert result.status in ("completed", "reused")
        assert result.manifest["provider"]["provider_type"] == "remote_endpoint"
        assert result.manifest["device_plan"]["accounting"] == "externally_enforced"
        assert result.outputs["classes"].get("data_object_id")
    finally:
        server.stop()


def test_slice_c_remote_health_and_warmup(service):
    server = FakeRemoteInferenceServer()
    endpoint = server.start()
    try:
        policy = RemoteEndpointPolicy(allowlist=(endpoint,))
        provider = RemoteInferenceProvider(endpoint, provider_id="remote@x", policy=policy)
        from app.lib.modelops.descriptor import GeoModelDescriptor

        descriptor = GeoModelDescriptor.model_validate(
            {
                "model_id": "remote-m", "model_version": "1",
                "checksum": "a" * 64, "provider_type": "remote_endpoint",
                "provider_ref": "remote@x", "provider_semantic_version": "v1",
                "task_types": ["semantic_segmentation"],
                "input_modalities": ["optical_rgb"], "input_bands": 3,
                "output_types": ["class_raster"],
                "class_schema": {"classes": ("a", "b", "c")},
                "spatial": {"chip_size": (32, 32), "context_size": (32, 32)},
                "license": "t",
            }
        )
        model = provider.load(descriptor, device="cpu")
        assert provider.warmup(model)["warmed"]
    finally:
        server.stop()


def test_slice_c_remote_healthy_endpoint_required_for_warmup():
    server = FakeRemoteInferenceServer()
    endpoint = server.start()
    try:
        policy = RemoteEndpointPolicy(allowlist=(endpoint,))
        provider = RemoteInferenceProvider(
            endpoint, provider_id="remote@y", policy=policy
        )
        provider._health_status = 500
        server._health_status = 500
        from app.lib.modelops.descriptor import GeoModelDescriptor

        descriptor = GeoModelDescriptor.model_validate(
            {
                "model_id": "remote-m", "model_version": "1",
                "checksum": "a" * 64, "provider_type": "remote_endpoint",
                "provider_ref": "remote@y", "provider_semantic_version": "v1",
                "task_types": ["semantic_segmentation"],
                "input_modalities": ["optical_rgb"], "input_bands": 3,
                "output_types": ["class_raster"],
                "class_schema": {"classes": ("a", "b", "c")},
                "spatial": {"chip_size": (32, 32), "context_size": (32, 32)},
                "license": "t",
            }
        )
        model = provider.load(descriptor, device="cpu")
        with pytest.raises(RemoteInferenceError):
            provider.warmup(model)
    finally:
        server.stop()


def test_ssrf_default_deny_blocks_loopback():
    policy = RemoteEndpointPolicy(allowlist=())
    with pytest.raises(RemoteEndpointPolicyError):
        policy.check("http://127.0.0.1:9999/infer")
    with pytest.raises(RemoteEndpointPolicyError):
        policy.check("http://169.254.169.254/latest/meta-data")
    with pytest.raises(RemoteEndpointPolicyError):
        policy.check("ftp://example.com/x")


def test_ssrf_allowlist_is_definitional_power():
    endpoint = "http://127.0.0.1:8901"
    policy = RemoteEndpointPolicy(allowlist=(endpoint,))
    assert policy.check(endpoint) == endpoint
    with pytest.raises(RemoteEndpointPolicyError):
        policy.check("http://127.0.0.1:8902")  # 未列出端口仍拒绝


def test_extension_adapter_end_to_end(service, synthetic_raster):
    """extension provider（in-process 假 invoke）→ 可取消推理 → 产物。"""
    from app.services.modelops.providers.extension_adapter import ExtensionProviderAdapter

    def fake_invoke(request):
        n = request["batch"]
        h, w = request["height"], request["width"]
        probs = []
        for _ in range(n):
            chip = []
            for c_i in range(3):
                band = [[0.1 if c_i else 0.8 for _ in range(w)] for _ in range(h)]
                chip.append(band)
            probs.append(chip)
        return {"class_probabilities": probs}

    adapter = ExtensionProviderAdapter("ext-test", fake_invoke, tasks=("semantic_segmentation",))
    service._providers.register(adapter)
    from app.lib.data.fingerprints import canonical_dumps, sha256_hex
    from app.lib.modelops.descriptor import (
        ClassSchema,
        DeviceRequirements,
        GeoModelDescriptor,
        NormalizationSpec,
        SpatialRequirements,
    )

    payload = {
        "model_id": "ext-model", "model_version": "1.0.0",
        "provider_type": "extension_worker", "provider_ref": "ext-test",
        "provider_semantic_version": "extension/1.0.0",
        "task_types": ["semantic_segmentation"],
        "input_modalities": ["optical_rgb"], "input_bands": 3,
        "normalization": NormalizationSpec(kind="none").as_dict(),
        "output_types": ["class_raster"],
        "class_schema": ClassSchema(classes=("background", "bright", "mid")).as_dict(),
        "spatial": SpatialRequirements(chip_size=(32, 32), context_size=(32, 32)).as_dict(),
        "device_requirements": DeviceRequirements(required="cpu").as_dict(),
        "license": "t", "random_seed_policy": "deterministic",
    }
    descriptor = GeoModelDescriptor.model_validate(
        {**payload, "checksum": sha256_hex(canonical_dumps(payload))}
    )
    service._registry.register(
        descriptor, owner_scope={"global": "builtin"}, registered_by="test",
        package_report={"synthetic": True}, known_provider_refs=service._providers.has,
    )
    result = service.run_inference(
        InferenceRequest(
            model_id="ext-model",
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s1"},
            task_type="semantic_segmentation",
        )
    )
    assert result.status == "completed"
    assert result.manifest["provider"]["provider_type"] == "extension_worker"


def test_extension_adapter_error_is_typed(service):
    from app.services.modelops.providers.extension_adapter import ExtensionProviderAdapter
    from app.services.modelops.providers.base import TileBatch, InferenceContext

    def bad_invoke(request):
        return {"error": "boom inside extension"}

    adapter = ExtensionProviderAdapter("ext-bad", bad_invoke)
    service._providers.register(adapter)
    from app.lib.modelops.descriptor import GeoModelDescriptor

    descriptor = GeoModelDescriptor.model_validate(
        {
            "model_id": "remote-m", "model_version": "1", "checksum": "a" * 64,
            "provider_type": "extension_worker", "provider_ref": "ext-bad",
            "provider_semantic_version": "v1",
            "task_types": ["semantic_segmentation"],
            "input_modalities": ["optical_rgb"], "input_bands": 1,
            "output_types": ["class_raster"],
            "class_schema": {"classes": ("a", "b")},
            "spatial": {"chip_size": (4, 4), "context_size": (4, 4)},
            "license": "t",
        }
    )
    model = adapter.load(descriptor, device="cpu")
    batch = TileBatch(pixels=np.zeros((1, 1, 4, 4), dtype=np.float32), valid_mask=None)
    with pytest.raises(ProviderError):
        adapter.infer(model, batch, InferenceContext(run_id="r1"))


def test_extension_adapter_unregistered_provider_ref_rejected(service, synthetic_raster):
    from app.lib.data.fingerprints import canonical_dumps, sha256_hex
    from app.lib.modelops.descriptor import (
        ClassSchema,
        GeoModelDescriptor,
        NormalizationSpec,
        SpatialRequirements,
    )
    from app.lib.modelops.errors import ProviderError

    payload = {
        "model_id": "ghost-model", "model_version": "1.0.0",
        "provider_type": "extension_worker", "provider_ref": "never-registered",
        "provider_semantic_version": "v1",
        "task_types": ["semantic_segmentation"],
        "input_modalities": ["optical_rgb"], "input_bands": 3,
        "normalization": NormalizationSpec(kind="none").as_dict(),
        "output_types": ["class_raster"],
        "class_schema": ClassSchema(classes=("a", "b")).as_dict(),
        "spatial": SpatialRequirements(chip_size=(16, 16), context_size=(16, 16)).as_dict(),
        "license": "t",
    }
    descriptor = GeoModelDescriptor.model_validate(
        {**payload, "checksum": sha256_hex(canonical_dumps(payload))}
    )
    with pytest.raises(ProviderError):
        service._registry.register(
            descriptor, owner_scope={"global": "builtin"},
            known_provider_refs=service._providers.has,
        )


def test_cancellable_remote_run_deadline(service, synthetic_raster):
    """remote（无协作取消）+ 墙钟 deadline：超时 typed（取消语义兜底）。"""
    def slow_hook(payload):
        time.sleep(2.0)
        return 200, {"class_probabilities": [[[[0.5, 0.5, 0.0]]]]}

    server = FakeRemoteInferenceServer(infer_hook=slow_hook)
    endpoint = server.start()
    try:
        service._settings.remote_allowlist.append(endpoint)
        from app.services.modelops.providers.remote_client import (
            RemoteEndpointPolicy,
            RemoteInferenceProvider,
        )

        policy = RemoteEndpointPolicy(allowlist=(endpoint,))
        provider = RemoteInferenceProvider(
            endpoint, provider_id=f"remote@{endpoint}", policy=policy, read_timeout_s=10.0
        )
        service._providers.register(provider)
        register_remote_model(service, endpoint)
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                service._engine.run,
                InferenceRequest(
                    model_id="remote-seg",
                    source_uri=str(synthetic_raster),
                    owner_scope={"session_id": "s-deadline"},
                    task_type="semantic_segmentation",
                    deadline_s=1.0,
                ),
            )
            # deadline 在首响应前到期 → typed 超时（或首检查点取消）。
            try:
                future.result(timeout=30)
                raised = False
            except Exception as exc:  # noqa: BLE001
                raised = True
                assert type(exc).__name__ in {
                    "InferenceTimeout", "InferenceCancelled", "RemoteInferenceError"
                }, type(exc).__name__
            assert raised
    finally:
        server.stop()
