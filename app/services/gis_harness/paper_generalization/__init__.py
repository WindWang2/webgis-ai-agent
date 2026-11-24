"""纸面目标比例尺综合会话服务（GOAL 04 / WP3+WP5）。

见 ``service.py`` 模块头：inspect / preview / apply 三面 + QA fail-closed
commit 门 + CAS 链 + 回执环。
"""
from app.services.gis_harness.paper_generalization.service import (
    PaperScaleError,
    apply_paper_scale_generalization,
    inspect_paper_scale_generalization,
    preview_paper_scale_generalization,
)

__all__ = [
    "PaperScaleError",
    "apply_paper_scale_generalization",
    "inspect_paper_scale_generalization",
    "preview_paper_scale_generalization",
]
