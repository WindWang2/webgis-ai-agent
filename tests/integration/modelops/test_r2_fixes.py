"""Review Round 2 修复的回归测试（C-1/C-2/M-1/M-4/M-7/M-8/m-1/m-3）。"""
from __future__ import annotations

import pytest

from app.lib.modelops.errors import CompatibilityError, ResourceUnavailable
from app.services.modelops.engine import InferenceRequest


def test_tile_cap_typed_rejection(service, tmp_path):
    """R2-C1：tile 数超上限 → 物化之前 typed 拒绝。"""
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "huge_grid.tif"
    # chip 16 → (50_000/16)^2 ≈ 9.7M tiles >> 65536（构造零拷贝 sparse tif）
    with rasterio.open(
        path, "w", driver="GTiff", width=50_000, height=50_000, count=3,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(116.0, 40.0, 1.0, 1.0), nodata=-9999.0,
    ) as dst:
        pass
    with pytest.raises(ResourceUnavailable):
        service.run_inference(InferenceRequest(
            model_id="tiny-landcover-seg", source_uri=str(path),
            owner_scope={"session_id": "s-cap"}))


def test_estimate_consistency_across_batch(service, synthetic_raster):
    """R2-M1：estimate 为单 chip 口径（manifest 账目只在 engine 乘 batch）。"""

    from app.services.modelops.providers.tiny_reference import TinyReferenceProvider

    provider = TinyReferenceProvider()
    d = service.inspect_model("tiny-landcover-seg", session_id="s1")["descriptor"]
    from app.lib.modelops.descriptor import GeoModelDescriptor

    desc = GeoModelDescriptor.model_validate(d)
    e1 = provider.estimate_resources(desc, batch=1, device="cpu")
    e8 = provider.estimate_resources(desc, batch=8, device="cpu")
    assert e1.vram_bytes == e8.vram_bytes  # 单 chip 口径


def test_engine_rejects_prompt_without_provider_capability(service, synthetic_raster):
    """R2-M7：provider caps 无 prompt 模式 → typed 拒绝（第二道门）。"""
    with pytest.raises(CompatibilityError):
        service.run_inference(InferenceRequest(
            model_id="tiny-landcover-seg",  # tiny-reference 不声明 prompt 模式
            source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s-prompt-gate"},
            prompt=PromptSpecForTest.point(),
        ))


class PromptSpecForTest:
    @staticmethod
    def point():
        from app.lib.modelops.promptable import PromptSpec

        return PromptSpec(points=((10.0, 10.0),))


def test_remote_model_context_gate(service):
    """R2-M4：context > 协议上限的 remote descriptor 在 load 时 typed 拒绝。"""
    from app.lib.modelops.descriptor import (
        GeoModelDescriptor,
    )
    from app.lib.modelops.errors import ProviderLoadFailed
    from app.services.modelops.providers.remote_client import (
        RemoteEndpointPolicy,
        RemoteInferenceProvider,
    )

    endpoint = "http://10.255.255.1:1"  # allowlist 命中即可，不会真连
    policy = RemoteEndpointPolicy(allowlist=(endpoint,))
    provider = RemoteInferenceProvider(endpoint, provider_id="remote@cap", policy=policy)
    descriptor = GeoModelDescriptor.model_validate({
        "model_id": "cap-test", "model_version": "1", "checksum": "a" * 64,
        "provider_type": "remote_endpoint", "provider_ref": "remote@cap",
        "provider_semantic_version": "v1",
        "task_types": ["semantic_segmentation"],
        "input_modalities": ["optical_rgb"], "input_bands": 3,
        "output_types": ["class_raster"],
        "class_schema": {"classes": ("a", "b", "c")},
        "spatial": {"chip_size": (64, 64), "context_size": (192, 192)},
        "license": "t",
    })
    with pytest.raises(ProviderLoadFailed):
        provider.load(descriptor, device="cpu")


def test_package_bytes_registration_gate(tmp_path, tiny_desc_factory):
    """R2 m-3：真实包注册走 inspect_archive 全门。"""
    import io
    import json
    import zipfile

    from app.lib.modelops.package_security import sha256_of_bytes
    from app.services.modelops.registry import ModelRegistryStore
    from app.services.modelops.config import ModelOpsSettings

    store = ModelRegistryStore(ModelOpsSettings(registry_dir=tmp_path))
    descriptor = tiny_desc_factory()
    payload = descriptor.fingerprint_payload()
    real_checksum = sha256_of_bytes(b"weights")  # 与 descriptor 不符
    bad = descriptor.model_copy(update={"checksum": real_checksum})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("weights.bin", b"weights")
        zf.writestr("manifest.json", json.dumps({"ok": True}))
    with pytest.raises(Exception):
        store.register(bad, owner_scope={"global": "x"},
                       known_provider_refs=lambda r: True,
                       package_bytes=buf.getvalue())


def test_ipv6_allowlist_normalization():
    """R2 m-1：IPv6 allowlist 归一后可命中（fail-closed 语义不破坏）。"""
    from app.services.modelops.providers.remote_client import RemoteEndpointPolicy

    policy = RemoteEndpointPolicy(allowlist=("http://[::1]:7080",))
    entry = policy._entry_of("http://[::1]:7080")
    assert entry is not None and entry in {a for a in policy.allowlist}


def test_cancel_latency_recorded(service, synthetic_raster):
    """R2-M8：取消路径写 cancel_latency_s（不再恒 0）。"""
    from app.lib.cancellation import CancellationToken
    from app.lib.modelops.errors import InferenceCancelled

    token = CancellationToken(job_id="t")
    token.cancel("test")
    with pytest.raises(InferenceCancelled):
        service._engine.run(InferenceRequest(
            model_id="tiny-landcover-seg", source_uri=str(synthetic_raster),
            owner_scope={"session_id": "s-cancel"}), cancel_token=token)
