"""Visual Observation / Critique / Repair（F15，ADR-0214）。

生产级视觉观察闭环的接线包：provider-neutral 观察契约（contracts）、
封闭 taxonomy 归一（taxonomy）、跨域融合（fusion，deterministic wins）、
截图 ref-only 存储（store）、确定性像素判据（rules）、生产 provider
（provider，``GIS_VISUAL_EVALUATOR`` 指向入口）、跨运行 recurrence
硬停（recurrence）、修复翻译桥与提案存储（repair_bridge）。

边界（恒成立）：视觉 finding 恒 ``degradation_only``；视觉修复只经
``mapspec.visual_healer`` 四类呈现面微变异 + ``apply_visual_heal_patch``
事务；视觉缺席/失败 = 诚实缺席，绝不充当 correctness verifier。
"""
from app.services.gis_harness.visual_observation.contracts import (
    VisualObservationInput,
    VisualObservationResult,
    VisualScreenshotRef,
)
from app.services.gis_harness.visual_observation.taxonomy import (
    VISUAL_TAXONOMY,
    finding_code,
    normalize_to_taxonomy,
)

__all__ = [
    "VISUAL_TAXONOMY",
    "VisualObservationInput",
    "VisualObservationResult",
    "VisualScreenshotRef",
    "finding_code",
    "normalize_to_taxonomy",
]
