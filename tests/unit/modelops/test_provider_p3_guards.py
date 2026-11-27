"""P3 review 回归：reference provider 的输出完整性与确定性守卫。

覆盖：
- temporal_reference：flag 缺失策略下整栈全缺失 → 不得把 NaN 写进产出；
- tiny_detection：NaN 像元不得产出 score=1.0 的假检测；
- promptable_reference：mask-only 先验 shape 不符 → typed 拒绝（不静默放宽）；
- tiny_reference：handle_id 确定性（同 descriptor 多次 load 相同）。
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.lib.modelops.descriptor import (
    ClassSchema,
    DeviceRequirements,
    GeoModelDescriptor,
    NormalizationSpec,
    SpatialRequirements,
)
from app.lib.modelops.errors import ProviderError
from app.services.modelops.providers.base import InferenceContext, TileBatch


def _descriptor(*, task: str, bands: int = 1, chip=(8, 8), classes=("background", "object")):
    return GeoModelDescriptor.model_validate(
        {
            "model_id": "p3-guard-model",
            "model_version": "1.0.0",
            "checksum": "c" * 64,
            "provider_type": "local_reference",
            "provider_ref": "tiny-reference",
            "provider_semantic_version": "tiny/1.0.0",
            "task_types": (task,),
            "input_modalities": ("optical_rgb",),
            "input_bands": bands,
            "normalization": NormalizationSpec(kind="none"),
            "output_types": ("class_raster",),
            "class_schema": ClassSchema(classes=classes),
            "spatial": SpatialRequirements(chip_size=chip, context_size=chip),
            "device_requirements": DeviceRequirements(required="cpu"),
            "license": "test",
            "temporal": {"max_length": 2, "missing_policy": "flag"},
        }
    )


def test_temporal_all_missing_stack_never_emits_nan():
    from app.services.modelops.providers.temporal_reference import (
        TemporalReferenceProvider,
    )

    provider = TemporalReferenceProvider()
    desc = _descriptor(task="temporal_forecast", bands=1, chip=(4, 4))
    model = SimpleNamespace(descriptor=desc)
    # (1, C*T + T, H, W)：数据全 0，flags 全 1（全部时相缺失）。
    pixels = np.zeros((1, 2 + 2, 4, 4), dtype=np.float32)
    pixels[:, 2:] = 1.0
    ctx = InferenceContext(
        run_id="p3-temporal", extras={"missing_policy": "flag", "stack_length": 2}
    )
    out = provider.infer(model, TileBatch(pixels=pixels, chip_hw=(4, 4)), ctx)
    assert np.isfinite(out.class_probabilities).all()


def test_detection_nan_cell_does_not_emit_fake_detection():
    from app.services.modelops.providers.tiny_detection import TinyDetectionProvider

    provider = TinyDetectionProvider(cell=4, score_threshold=0.6)
    pixels = np.full((1, 1, 4, 4), np.nan, dtype=np.float32)
    ctx = InferenceContext(run_id="p3-detect")
    out = provider.infer(None, TileBatch(pixels=pixels, chip_hw=(4, 4)), ctx)
    assert out.detections == []


def test_mask_only_prompt_with_mismatched_prior_is_typed():
    from app.services.modelops.providers.promptable_reference import (
        PromptableReferenceProvider,
    )

    provider = PromptableReferenceProvider()
    desc = _descriptor(task="promptable_segmentation", bands=1, chip=(4, 4))
    model = SimpleNamespace(descriptor=desc)
    ctx = InferenceContext(
        run_id="p3-prompt",
        extras={
            "prompt": {},
            "prompt_mask_arrays": (np.zeros((2, 2), dtype=bool),),
        },
    )
    batch = TileBatch(
        pixels=np.full((1, 1, 4, 4), 0.5, dtype=np.float32), chip_hw=(4, 4)
    )
    with pytest.raises(ProviderError, match="prior mask shape"):
        provider.infer(model, batch, ctx)


def test_tiny_reference_handle_id_is_deterministic():
    from app.services.modelops.providers.tiny_reference import TinyReferenceProvider

    provider = TinyReferenceProvider()
    desc = _descriptor(task="semantic_segmentation")
    first = provider.load(desc, device="cpu")
    second = provider.load(desc, device="cpu")
    assert first.handle_id == second.handle_id == "p3-guard-model@1.0.0#tiny"
