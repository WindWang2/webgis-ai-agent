"""Quality Platform（ADR-0104）——质量基础设施的 app 侧入口。

这里是**派生层**：唯一事实源仍是各 registry 与测试套件本身。
本包提供的编译器 / 发现器只读投影，不反写任何 registry。
"""
from app.lib.quality.manifest import (
    GATE_THRESHOLDS,
    QualityFinding,
    QualityManifest,
    collect,
    compile_quality_manifest,
    gate,
)

__all__ = [
    "GATE_THRESHOLDS",
    "QualityFinding",
    "QualityManifest",
    "collect",
    "compile_quality_manifest",
    "gate",
]
