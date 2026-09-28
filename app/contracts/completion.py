"""渲染完成态 finding 契约（自 services/gis_harness/completion/contracts.py 下沉，ADR-0216）。

跨层消费者：``app/services/gis_harness``（完成态评估权威）与
``app/lib/cartography/render_apply_ack``（apply ACK 观察投影）。
本模块只收 render-apply 归因所需的词表与 bounded finding 载体；
完成态评估的其余 F_* 词表与 ``MapCompletionResult`` 留在 services
权威模块（``app/services/gis_harness/completion/contracts.py``）re-export
本模块符号，既有 import path 全兼容。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

#: finding detail 截断上界（与 services 侧 MapCompletionResult 语义一致）。
MAX_FINDING_DETAIL = 160

# F13（ADR-0214 D3）：结构化 apply ACK 的失败归因披露 —— per-layer
# applied↔failed 的机器可读 reason（此前只能从 layer 在场性反推）。
# transient/可自愈语义与 P9 渲染族一致：warning，不推翻 status。
F_RENDER_APPLY_FAILED = "render_apply_failed"


@dataclass
class MapCompletionFinding:
    """单条机器可读发现（bounded：detail 截断）。"""

    code: str
    severity: str  # "error" | "warning"
    target: str = ""
    detail: str = ""
    repair: Optional[str] = None  # 适用/已应用的 repair action code
    # 组件族（slot 的 allowed_component_types）—— family-aware 修复用。
    family: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "target": str(self.target)[:64],
            "detail": self.detail[:MAX_FINDING_DETAIL],
            "repair": self.repair,
        }


__all__ = [
    "MAX_FINDING_DETAIL",
    "F_RENDER_APPLY_FAILED",
    "MapCompletionFinding",
]
