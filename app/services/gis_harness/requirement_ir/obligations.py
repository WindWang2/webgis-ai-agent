"""义务面派生（理解面 → MapRequirementSpec 的单向投影）。

独立成模块以避免 build ↔ patch 导入环：patch.apply_patch 在每次写后
调用 :func:`rederive` 保持义务与 spec 同步（义务是理解面的投影，不是
平行真相）；条目 lifecycle 状态与 user pin 按
(kind, export_format, group_by, capability) 匹配保留。
"""
from __future__ import annotations

from typing import Dict, List

from pydantic import BaseModel

from app.services.gis_harness.requirement_ir.contracts import (
    GISIntentSpec,
    MapRequirementSpec,
    Provenance,
    RequirementItem,
)
from app.services.gis_harness.requirement_ir.digest import requirement_digest


def derive_requirements(spec: GISIntentSpec) -> MapRequirementSpec:
    """理解面 → 义务面（单向；kind 词表对齐 goal_satisfaction）。"""
    items: List[RequirementItem] = []
    idx = 0

    def add(kind: str, statement: str, source_path: str, *,
            pinned: bool = False, capability: str = "",
            export_format: str = "", group_by: str = "") -> None:
        nonlocal idx
        idx += 1
        items.append(RequirementItem(
            id=f"r{idx}", kind=kind,  # type: ignore[arg-type]
            statement=statement[:200], source_path=source_path,
            provenance=Provenance(origin="rule"),
            pinned=pinned, capability=capability[:64],
            export_format=export_format[:16], group_by=group_by[:48],
            scope_name=spec.aoi.name[:64],
        ))

    if spec.task.kind in ("map", "edit"):
        add("map", f"产出{spec.aoi.name or '目标区域'}专题图（{spec.task.task_type}）",
            "task.kind")
    if spec.task.kind == "analysis" or "analysis" in spec.task.stages \
            or spec.statistics.dimension != "none":
        add("analysis", f"完成{spec.task.task_type}分析", "task.kind",
            capability=spec.task.task_type[:64])
    if spec.statistics.dimension != "none" or spec.measures:
        measure_label = spec.measures[0].statistic if spec.measures else "未定指标"
        add("statistics",
            f"按 {spec.statistics.group_by or '未定分组'} 聚合 {measure_label}",
            "statistics", group_by=spec.statistics.group_by)
    for fmt in spec.output.formats:
        add("export", f"导出 {fmt}", "output.formats", export_format=fmt,
            pinned=any(lock.scope == "export_format" and lock.value == fmt
                       for lock in spec.locks))
    for lock in spec.locks:
        if lock.scope == "layer_visibility" and lock.value:
            add("map", f"保持图层隐藏: {lock.value}", "locks", pinned=True)
    return MapRequirementSpec(items=items[:64])


def rederive(spec: GISIntentSpec, old_items: List[RequirementItem],
             digest_source: BaseModel) -> MapRequirementSpec:
    """从 spec 重派生义务并按匹配保留旧条目状态/pin。

    ``digest_source`` 提供 derived_from 语义（requirement_digest 只依赖
    语义核，与义务面无关，故可用更新后 spec 的探测文档计算）。
    """
    old_index: Dict[tuple, RequirementItem] = {}
    for item in old_items:
        old_index.setdefault(
            (item.kind, item.export_format, item.group_by, item.capability),
            item)
    fresh = derive_requirements(spec)
    for item in fresh.items:
        old = old_index.get(
            (item.kind, item.export_format, item.group_by, item.capability))
        if old is not None:
            item.state = old.state
            item.pinned = item.pinned or old.pinned
            if old.provenance.is_user():
                item.provenance = old.provenance
    fresh.derived_from = requirement_digest(digest_source)  # type: ignore[arg-type]
    return fresh
