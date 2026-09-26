"""确定性 rules-half（F15/ADR-0214 决策二）—— 像素可度量事实 → taxonomy。

复用 ``local_visual_criteria.evaluate_local_visual`` 的确定性像素判据
（零网络、零随机、同图恒同结果），把 non-pass 维度按**事实级映射**归一
为 taxonomy findings（每条携带测量值证据）。

边界纪律（W9，结构性）：组件重叠/越界等布局事实是
``render_observation.derive_component_layout_findings`` 的硬证据领地，
本模块**不做任何几何布局检查**，只回答像素可度量的问题：

- inkRatio < 0.02（画面近空白）→ ``empty_space``
- inkRatio > 0.60（墨量过载不可读）→ ``legibility``
- edgeDensity > 0.45（杂乱过密）→ ``hierarchy``
- centroidOffset > 0.25（重心失衡）→ ``hierarchy``
- distinctColorBuckets < 3（色彩不可分辨/低对比）→ ``contrast``

实体恒 ``map``（像素事实是画布级的——不猜组件定位）。
纯函数、零 I/O、有界（≤4 条）。
"""
from __future__ import annotations

from typing import List, Optional

from app.services.gis_harness.completion.unified_findings import UnifiedFinding
from app.services.gis_harness.visual_observation.taxonomy import finding_code

#: 与 local_visual_criteria 阈值同源（漂移由 corpus 测试互锁）；调参先改
#: local_criteria 再同步此处，或抽共享常量（当前两处各留注释互指）。
_INK_FLOOR = 0.02
_INK_CEIL = 0.60
_EDGE_CEIL = 0.45
_CENTROID_CEIL = 0.25
_COLOR_FLOOR = 3

_MAX_RULE_FINDINGS = 4
_SOURCE = "visual_observation_provider"


def evaluate_pixel_rules(png_bytes: Optional[bytes]) -> List[UnifiedFinding]:
    """像素事实 → taxonomy findings（无图/全过 → 空列表；fail-closed）。"""
    if not png_bytes:
        return []
    try:
        from app.lib.harness.local_visual_criteria import (
            evaluate_local_visual,
        )

        report = evaluate_local_visual(png_bytes)
    except Exception:  # noqa: BLE001 — 解码/度量失败 = 无本地判据（诚实缺席）
        return []
    if not report.get("evaluated"):
        return []
    facts: dict = report.get("facts") or {}
    dims: dict = report.get("dimensions") or {}

    findings: List[UnifiedFinding] = []

    def _emit(category: str, evidence: str) -> None:
        if len(findings) >= _MAX_RULE_FINDINGS:
            return
        code = finding_code(category)
        if not code:
            return
        findings.append(UnifiedFinding(
            domain="visual",
            code=code,
            severity="warning",
            source=_SOURCE,
            scope="map",
            affected_entity="map",
            evidence=str(evidence)[:200],
            repair_class="",
            retryable=False,
            blocks_completion=False,
            degradation_only=True,
        ))

    readability = (dims.get("readability") or {}).get("status")
    if readability == "warning":
        ink = facts.get("inkRatio")
        if isinstance(ink, (int, float)) and ink < _INK_FLOOR:
            _emit("empty_space", f"rule:ink_ratio value={ink} band=floor")
        elif isinstance(ink, (int, float)):
            _emit("legibility", f"rule:ink_ratio value={ink} band=ceil")
    density = (dims.get("information_density") or {}).get("status")
    if density == "warning":
        _emit("hierarchy",
              f"rule:edge_density value={facts.get('edgeDensity')} band=ceil")
    balance = (dims.get("composition_balance") or {}).get("status")
    if balance == "warning":
        _emit("hierarchy",
              f"rule:centroid_offset value={facts.get('centroidOffset')} "
              f"band=ceil")
    colors = (dims.get("color_discriminability") or {}).get("status")
    if colors == "warning":
        _emit("contrast",
              f"rule:color_buckets value={facts.get('distinctColorBuckets')} "
              f"band=floor")
    return findings


def pixel_facts_summary(png_bytes: Optional[bytes]) -> dict:
    """像素事实摘要（证据面；度量失败 → 空字典，诚实缺席）。"""
    if not png_bytes:
        return {}
    try:
        from app.lib.harness.local_visual_criteria import (
            measure_visual_facts,
        )

        return dict(measure_visual_facts(png_bytes))
    except Exception:  # noqa: BLE001 — 摘要是增值面
        return {}


__all__ = ["evaluate_pixel_rules", "pixel_facts_summary"]
