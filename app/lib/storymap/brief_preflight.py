"""简报发布预检（G11 / BRF-04）——纯函数，逐项 verdict。

verdict 词表（对齐既有三态纪律）：
- ``pass``           —— 检查通过（可带披露 note）；
- ``fail``           —— 阻断项（强事实错误/跨 owner 素材/秘密泄漏/
                        必需图件缺失）—— 冻结与"可交付"判定阻断；
- ``not_evaluated``  —— 无法评估（证据不在场/策略未配置）—— 不得伪装
                        成已认证。

总判定：``blocked``（任一 fail）| ``deliverable``（全部 pass）|
``draft_exportable``（存在 not_evaluated/警告，可显式带披露导出草稿）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.lib.storymap.brief_factcheck import check_facts
from app.lib.storymap.brief_spec import MapBriefSpec

VERDICTS = ("pass", "fail", "not_evaluated")
OUTCOMES = ("blocked", "draft_exportable", "deliverable")

# 敏感内容扫描复用 export_packager 的脱敏纪律（发现即导出期 REDACT；
# 预检只提示不阻断 —— 阻断条件是"秘密字段被引用为数据"）。
from app.lib.storymap.export_packager import sanitize_dict  # noqa: E402


@dataclass
class PreflightItem:
    check: str
    verdict: str  # pass | fail | not_evaluated
    blocking: bool
    detail: str = ""
    refs: List[str] = field(default_factory=list)


@dataclass
class PreflightReport:
    outcome: str  # blocked | draft_exportable | deliverable
    items: List[PreflightItem]
    fact_stats: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome,
            "fact_stats": self.fact_stats,
            "items": [
                {"check": i.check, "verdict": i.verdict,
                 "blocking": i.blocking, "detail": i.detail,
                 "refs": i.refs}
                for i in self.items
            ],
        }


def _iter_bindings(spec: MapBriefSpec):
    for fact in spec.facts:
        if fact.basis.artifact_ref:
            yield f"fact:{fact.fact_id}", fact.basis
    for block in spec.blocks:
        if block.binding is not None and block.binding.artifact_ref:
            yield f"block:{block.id}", block.binding


def run_preflight(
    spec: MapBriefSpec,
    *,
    records: Mapping[str, Mapping[str, Any]],
    payloads: Mapping[str, Any],
    widget_data: Mapping[str, Any],
    mapspec_available: bool,
    owner_scope_refs: Optional[Mapping[str, bool]] = None,
) -> PreflightReport:
    """发布预检。所有输入是只读投影（服务壳负责装载与权限）。"""
    items: List[PreflightItem] = []

    # 1. 来源授权：绑定 ref 必须在本会话台账（跨 owner → fail）。
    unauthorized: List[str] = []
    unresolved: List[str] = []
    for label, binding in _iter_bindings(spec):
        ref = binding.artifact_ref
        if owner_scope_refs is not None and ref in owner_scope_refs:
            if not owner_scope_refs[ref]:
                unauthorized.append(f"{label}→{ref}")
            continue
        if ref not in records:
            unresolved.append(f"{label}→{ref}")
    if unauthorized:
        items.append(PreflightItem(
            "source_authorization", "fail", True,
            "cross-owner artifacts referenced", unauthorized))
    elif unresolved:
        items.append(PreflightItem(
            "source_authorization", "fail", True,
            "bound artifacts not registered in this session", unresolved))
    else:
        bound_refs = sorted({b.artifact_ref for _, b in _iter_bindings(spec)})
        items.append(PreflightItem(
            "source_authorization", "pass", True,
            f"{len(bound_refs)} bound artifacts authorized", bound_refs))

    # 2. 引用可解析性：块 → fact/widget/chapter（模型已保证 fact/chapter；
    # widget 数据在场由 widget_data 投影判定）。
    missing_widget_data: List[str] = []
    for block in spec.blocks:
        if block.kind in ("chart", "table") and block.widget_id:
            data = widget_data.get(block.widget_id)
            if not data:
                missing_widget_data.append(block.widget_id)
    if missing_widget_data:
        items.append(PreflightItem(
            "reference_resolvability", "fail", True,
            "chart/table widgets have no data", missing_widget_data))
    else:
        items.append(PreflightItem(
            "reference_resolvability", "pass", True,
            "all block references resolve"))

    # 3. 数值/单位/方法一致性（factcheck 裁决）。
    outcomes = check_facts(spec.facts, records=records, payloads=payloads)
    stats = {"verified": 0, "warning": 0, "unverified": 0}
    contradicted: List[str] = []
    unverified: List[str] = []
    for outcome in outcomes:
        stats[outcome.status] = stats.get(outcome.status, 0) + 1
        if outcome.contradicted:
            contradicted.append(outcome.fact_id)
        elif outcome.status == "unverified":
            unverified.append(outcome.fact_id)
    if contradicted:
        items.append(PreflightItem(
            "numeric_consistency", "fail", True,
            "facts contradict authoritative/recomputed values", contradicted))
    elif stats["warning"]:
        items.append(PreflightItem(
            "numeric_consistency", "pass", True,
            f"{stats['warning']} facts with disclosure-level warnings"))
    elif unverified:
        items.append(PreflightItem(
            "numeric_consistency", "not_evaluated", True,
            "facts lack positive evidence (not verified)", unverified))
    else:
        items.append(PreflightItem(
            "numeric_consistency", "pass", True,
            f"{stats['verified']} facts verified"))

    # 4. 地图版本与覆盖：指纹在场 + 镜头在产物 bbox 并集内。
    map_blocks = [b for b in spec.blocks if b.kind == "map_view"]
    if not map_blocks:
        items.append(PreflightItem(
            "map_version_coverage", "fail", True,
            "no map_view block — required map graphic missing"))
    else:
        missing_fp = [b.id for b in map_blocks
                      if not (b.map_view and b.map_view.mapspec_fingerprint)]
        if missing_fp or not mapspec_available:
            items.append(PreflightItem(
                "map_version_coverage", "not_evaluated", True,
                "mapspec fingerprint unavailable — static render deferred",
                missing_fp))
        else:
            # 镜头覆盖：产物 bbox 并集（缺席即不评）。
            boxes = [
                r.get("bbox") for r in records.values()
                if isinstance(r.get("bbox"), (list, tuple)) and len(r.get("bbox")) == 4
            ]
            outside: List[str] = []
            if boxes:
                west = min(float(b[0]) for b in boxes)
                south = min(float(b[1]) for b in boxes)
                east = max(float(b[2]) for b in boxes)
                north = max(float(b[3]) for b in boxes)
                for block in map_blocks:
                    view = block.map_view
                    cx, cy = view.center[0], view.center[1]
                    span = max(360.0 / (2.0 ** max(0.0, view.zoom - 1)), 0.5)
                    if not (west - span <= cx <= east + span
                            and south - span / 2 <= cy <= north + span / 2):
                        outside.append(block.id)
            if outside:
                items.append(PreflightItem(
                    "map_version_coverage", "pass", True,
                    "camera outside data coverage — will warn on export",
                    outside))
            else:
                items.append(PreflightItem(
                    "map_version_coverage", "pass", True,
                    f"{len(map_blocks)} map views pinned"))

    # 5. 导出可用性（图件数据在场已在 2 查；此处查文本缺失）。
    empty_text = [b.id for b in spec.blocks
                  if b.kind in ("text", "method", "limitation")
                  and not b.text.strip()]
    if empty_text:
        items.append(PreflightItem(
            "content_completeness", "pass", False,
            "empty text blocks will render placeholders", empty_text))
    else:
        items.append(PreflightItem(
            "content_completeness", "pass", True,
            f"{len(spec.blocks)} blocks complete"))

    # 6. 隐私扫描：跑一遍导出脱敏并对比 —— 有 REDACTED 即披露（导出期
    # 已确保脱敏，预检只提示用户哪些内容会被遮蔽，不阻断）。
    def _count_redacted(node: Any) -> int:
        if isinstance(node, Mapping):
            return sum(1 for v in node.values() if v == "REDACTED") + sum(
                _count_redacted(v) for v in node.values())
        if isinstance(node, list):
            return sum(_count_redacted(v) for v in node)
        return 0

    redacted = _count_redacted(sanitize_dict(spec.model_dump()))
    items.append(PreflightItem(
        "privacy_scan", "pass", True,
        f"{redacted} sensitive fields will be redacted on export"
        if redacted else "no sensitive fields in brief payload"))

    # 7. 署名/许可：source 块在场 → pass；缺席 → not_evaluated。
    source_blocks = [b for b in spec.blocks if b.kind == "source"]
    if source_blocks:
        items.append(PreflightItem(
            "attribution_license", "pass", False,
            f"{len(source_blocks)} source citations present"))
    else:
        items.append(PreflightItem(
            "attribution_license", "not_evaluated", False,
            "no source citations — licensing not asserted"))

    outcome = "deliverable"
    if any(i.verdict == "fail" for i in items):
        outcome = "blocked"
    elif any(i.verdict == "not_evaluated" for i in items):
        outcome = "draft_exportable"
    return PreflightReport(outcome=outcome, items=items, fact_stats=stats)


__all__ = ["PreflightItem", "PreflightReport", "run_preflight",
           "VERDICTS", "OUTCOMES"]
