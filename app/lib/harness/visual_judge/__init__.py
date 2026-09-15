"""VLM Visual Critic Runtime（ADR-0185）——具身多模态视觉感知与图面缺陷
结构化诊断运行时。

公开面即规格（docs/dev/vlm-visual-critic-spec.md §1）：

- 契约：VisualDimension / VisualBBox / VisualCritiqueItem /
  VisualDimensionScore / VisualJudgeReport（Pydantic 严格模式）；
- 快照：SnapshotExtractor / MapSnapshot / SnapshotError；
- provider：VLMClient 协议 + OpenAICompatVLMClient / GeminiVLMClient +
  CRITIC_OUTPUT_SCHEMA 单一事实源 + parse_critic_output；
- 引擎：VisualCriticEngine / build_critic_engine /
  visual_critic_runtime_enabled / CriticMemoCache；
- 离线 Mock：FakeVLMClient / GOLDEN_SAMPLES / render_golden_image。

本包不 import ``visual_evaluator``（接线方向单向：evaluator → judge，
无环）。
"""
from app.lib.harness.visual_judge.contracts import (
    Severity,
    VisualBBox,
    VisualCritiqueItem,
    VisualDimension,
    VisualDimensionScore,
    VisualJudgeReport,
    safe_bbox,
)
from app.lib.harness.visual_judge.critic_engine import (
    CriticMemoCache,
    VisualCriticEngine,
    build_critic_engine,
    visual_critic_runtime_enabled,
)
from app.lib.harness.visual_judge.fake_vlm import (
    FakeVLMClient,
    GoldenSample,
    GOLDEN_SAMPLES,
)
from app.lib.harness.visual_judge.golden_images import render_golden_image
from app.lib.harness.visual_judge.snapshot_extractor import (
    MapSnapshot,
    SnapshotError,
    SnapshotExtractor,
)
from app.lib.harness.visual_judge.vlm_provider import (
    CRITIC_OUTPUT_SCHEMA,
    CRITIC_SYSTEM_PROMPT,
    CriticOutputError,
    CriticProviderError,
    CriticTimeout,
    GeminiVLMClient,
    OpenAICompatVLMClient,
    PROVIDER_GEMINI,
    PROVIDER_OPENAI_COMPATIBLE,
    VLMClient,
    VLMRequest,
    build_gemini_request,
    build_openai_request,
    gemini_endpoint,
    is_placeholder_key,
    parse_critic_output,
)

__all__ = [
    "CRITIC_OUTPUT_SCHEMA",
    "CRITIC_SYSTEM_PROMPT",
    "CriticMemoCache",
    "CriticOutputError",
    "CriticProviderError",
    "CriticTimeout",
    "FakeVLMClient",
    "GOLDEN_SAMPLES",
    "GeminiVLMClient",
    "GoldenSample",
    "MapSnapshot",
    "OpenAICompatVLMClient",
    "PROVIDER_GEMINI",
    "PROVIDER_OPENAI_COMPATIBLE",
    "Severity",
    "SnapshotError",
    "SnapshotExtractor",
    "VLMClient",
    "VLMRequest",
    "VisualBBox",
    "VisualCritiqueItem",
    "VisualDimension",
    "VisualDimensionScore",
    "VisualJudgeReport",
    "VisualCriticEngine",
    "build_critic_engine",
    "build_gemini_request",
    "build_openai_request",
    "gemini_endpoint",
    "is_placeholder_key",
    "parse_critic_output",
    "render_golden_image",
    "safe_bbox",
    "visual_critic_runtime_enabled",
]
