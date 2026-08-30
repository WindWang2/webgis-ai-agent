"""Facet ↔ Artifact ↔ MapSpec Lineage（ADR-0088 P3/P4）。

回答四个既有 ArtifactGraph 无法直接回答的产品级问题（纯派生，零 IO）：

    1. 某个 facet 由哪些 artifact 支撑？（层 → source ref → 记录）
    2. 某个 layer 来源于哪个 analysis result？（provenance / source ref）
    3. 某个 chart 能不能复用已有 statistics artifact？（ owed 产物 facet
       的存活上游 ref —— P4 最小重计算）
    4. 哪个 artifact 已 superseded / 过期？（记录状态投影 + 存活探测）

目标链（每一段都来自既有事实，不发明第二血缘）：

    Facet ── capability 行 bound_ref / MapSpec source ref / 组件 chartRef
      ↓
    Artifact（ArtifactRecord / ref descriptor）
      ↓
    MapSpec layer / component
      ↓
    Runtime observation（observation 侧不在本模块 —— 渲染证据由
    ProductFacetCompletion / runtime_repair 消费）

最小重计算（P4）：欠账的**产物** facet（chart/statistics）若存在存活的
上游 artifact，``reusable_inputs`` 返回它们 —— 动作层（action_intent）
将其作为 ``artifact_inputs`` 随建议带给 Pi：只补产物，不重跑上游分析。
确认死亡（expired/缺失）的 ref 不进 reusable —— 死 artifact 触发执行债，
不做 remount（ADR-0088 不变量）。

边界：本模块不写任何状态、不探测存储（liveness 只读调用方带来的
descriptors / records 快照）；不替代 ArtifactGraph 的 artifact 级查询，
只做 facet 级投影。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.gis_harness.product_graph import (
    KIND_ANALYSIS,
    KIND_CHART,
    KIND_MAP_LAYER,
    KIND_STATISTICS,
    S_OFF,
    S_PENDING,
    build_product_graph,
)

# chart/statistics 产物可复用的上游 artifact 语义类型（registry output
# 词表的子集 —— 表/聚合类；大要素集不作为 chart 最小重计算输入）。
_CHART_INPUT_ARTIFACT_TYPES = frozenset({
    "stats_table",
    "admin_aggregate_table",
    "od_matrix",
    "grid_aggregate",
})

_MAX_LINEAGE_REFS_PER_FACET = 8


def _liveness_of(
    ref: str,
    descriptors: Optional[Dict[str, Any]],
    records: Optional[Dict[str, Any]],
) -> str:
    """ref 存活性（只读快照；unknown ≠ alive ≠ dead —— 不虚构）。

    - records（ArtifactRecord.status）优先：valid/stale/superseded 等直接投影；
    - descriptors（ref descriptor dict）次之：在场 → alive、确认缺失 → dead；
    - 两者都缺 → unknown（不计入 reusable 也不计入 dead —— 保守不虚构）。
    """
    record = records.get(ref) if records else None
    if record is not None:
        status = str(getattr(record, "status", "") or "")
        if status == "valid":
            return "alive"
        if status in ("expired", "stale", "superseded", "failed"):
            return status
        return "unknown"
    if descriptors is not None and ref in descriptors:
        return "alive" if descriptors.get(ref) is not None else "expired"
    return "unknown"


@dataclass
class LineageRef:
    """facet 血缘里的一条 artifact 引用（bounded）。"""

    ref: str
    role: str  # input（上游分析产物）| output（facet 自身产物，如 chartRef）
    liveness: str = "unknown"  # alive | expired | stale | superseded | failed | unknown
    producer_capability: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ref": self.ref[:64],
            "role": self.role,
            "liveness": self.liveness,
            "producer_capability": self.producer_capability[:64],
        }


@dataclass
class FacetLineageEntry:
    """单 facet 的血缘投影（bounded）。"""

    facet_id: str
    kind: str
    artifact_refs: List[LineageRef] = field(default_factory=list)
    # 该 facet 若要补齐，欠哪些 capability 的（重）执行（空 = 无执行债）。
    recompute_capabilities: List[str] = field(default_factory=list)


@dataclass
class FacetArtifactLineage:
    """facet 级血缘投影（derived，不持久化）。"""

    entries: Dict[str, FacetLineageEntry] = field(default_factory=dict)

    def artifacts_for(self, facet_id: str) -> List[LineageRef]:
        entry = self.entries.get(facet_id)
        return list(entry.artifact_refs) if entry else []

    def reusable_inputs(self, facet_id: str) -> List[str]:
        """欠账产物 facet 的存活上游 ref（P4 最小重计算输入；有界）。

        确认死亡（expired/superseded/failed）的 ref 排除；unknown 不排除
        也不虚构其活性 —— 复用判定交给调用方（复用一个已被驱逐的 ref 的
        失败代价是一次普通工具重试，而错误地重跑全链是昂贵分析）。
        """
        entry = self.entries.get(facet_id)
        if not entry:
            return []
        return [
            r.ref
            for r in entry.artifact_refs
            if r.role == "input" and r.liveness not in ("expired", "superseded", "failed")
        ][:_MAX_LINEAGE_REFS_PER_FACET]

    def dead_outputs(self) -> List[str]:
        """确认死亡的产品 artifact ref（MapSpec source / chartRef 指向）。

        这些是"MapSpec 引用了死 artifact"的执行债证据（Scenario B）——
        不得 remount，只能重跑上游 capability。
        """
        out: List[str] = []
        for entry in self.entries.values():
            for r in entry.artifact_refs:
                if r.role == "output" and r.liveness in ("expired", "failed"):
                    out.append(r.ref)
        return out


def _spec_source_ref(mapspec: Optional[Dict[str, Any]], source_id: str) -> str:
    """spec source 的 ref 指针（与 product_graph._spec_source_ref 同语义，
    单处实现避免漂移 —— 直接复用该实现）。"""
    from app.services.gis_harness.product_graph import _spec_source_ref as _impl

    return _impl(mapspec, source_id)


def build_facet_lineage(
    chapter: Optional[Dict[str, Any]],
    mapspec: Optional[Dict[str, Any]] = None,
    *,
    descriptors: Optional[Dict[str, Any]] = None,
    records: Optional[Dict[str, Any]] = None,
) -> FacetArtifactLineage:
    """章节 + MapSpec + 存活快照 → facet 级血缘（纯函数，零 IO）。

    ``records``：artifact_id（= ref 字符串）→ ArtifactRecord（可选）；
    ``descriptors``：ref → ref descriptor | None（可选）。两者至少给一个
    才有确定 liveness；都不给时全部 unknown（诚实降级，不虚构）。
    """
    lineage = FacetArtifactLineage()
    if not isinstance(chapter, dict):
        return lineage

    graph = build_product_graph(chapter, mapspec)
    spec_layers = {
        str(ly.get("id") or ""): ly
        for ly in ((mapspec or {}).get("layers") or [])
        if isinstance(ly, dict)
    }
    layer_rows = {
        str(ly.get("layer_id") or ""): ly
        for ly in (chapter.get("map_layers") or [])
        if isinstance(ly, dict) and ly.get("layer_id")
    }
    # 能力行：capability → bound_ref（requirement/step 合并，与 plan_graph 同源）
    row_ref: Dict[str, str] = {}
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        cap = str(row.get("capability") or "")
        ref = str(row.get("bound_ref") or "")
        if cap and ref and cap not in row_ref:
            row_ref[cap] = ref

    def _live(ref: str) -> str:
        return _liveness_of(ref, descriptors, records)

    from app.lib.gis.capability_registry import get_capability_registry

    caps = get_capability_registry()

    for node in graph.nodes:
        entry = FacetLineageEntry(facet_id=node.node_id, kind=node.kind)
        if node.kind == KIND_ANALYSIS:
            ref = node.artifact_ref or row_ref.get(node.key, "")
            if ref:
                entry.artifact_refs.append(LineageRef(
                    ref=ref, role="output", liveness=_live(ref),
                    producer_capability=node.key,
                ))
            # 输入血缘：该能力的上游行（registry input ∩ 已绑定 ref 的行）
            desc = caps.get(node.key)
            input_types = set(getattr(desc, "input_artifact_types", None) or []) if desc else set()
            if input_types:
                for other_cap, other_ref in row_ref.items():
                    if other_cap == node.key or not other_ref:
                        continue
                    other_desc = caps.get(other_cap)
                    outs = set(getattr(other_desc, "output_artifact_types", None) or []) if other_desc else set()
                    if outs & input_types:
                        entry.artifact_refs.append(LineageRef(
                            ref=other_ref, role="input", liveness=_live(other_ref),
                            producer_capability=other_cap,
                        ))
        elif node.kind == KIND_MAP_LAYER:
            row = layer_rows.get(node.key) or {}
            cap = str(row.get("source_capability") or "")
            layer = spec_layers.get(node.key)
            if layer is not None:
                src_ref = _spec_source_ref(mapspec, str(layer.get("source") or ""))
                if src_ref:
                    entry.artifact_refs.append(LineageRef(
                        ref=src_ref, role="output", liveness=_live(src_ref),
                        producer_capability=cap,
                    ))
                    # 层的数据来源行 = source_capability 的产出
                    if cap and cap in row_ref and row_ref[cap] != src_ref:
                        entry.artifact_refs.append(LineageRef(
                            ref=row_ref[cap], role="input", liveness=_live(row_ref[cap]),
                            producer_capability=cap,
                        ))
                    # 死 source artifact → 补层的执行债（不 remount 死 ref）
                    if src_ref and _live(src_ref) in ("expired", "failed"):
                        entry.recompute_capabilities = [cap] if cap else []
            elif node.status == S_PENDING and cap:
                # 计划层未落 MapSpec：执行债 = source_capability（advisor 已
                # 报 produce_layer；这里补血缘事实）
                entry.recompute_capabilities = [cap]
        elif node.kind in (KIND_CHART, KIND_STATISTICS):
            ref = node.artifact_ref
            if ref:
                entry.artifact_refs.append(LineageRef(
                    ref=ref, role="output", liveness=_live(ref),
                ))
            # 产物欠账（pending/owed）时的最小重计算输入：存活的表/聚合类
            # 上游 artifact（chart 可从既有统计产物重生成，不重跑分析链）。
            if node.status in (S_PENDING, S_OFF) or not ref:
                for other_cap, other_ref in row_ref.items():
                    if not other_ref:
                        continue
                    other_desc = caps.get(other_cap)
                    outs = set(getattr(other_desc, "output_artifact_types", None) or []) if other_desc else set()
                    if outs & _CHART_INPUT_ARTIFACT_TYPES:
                        entry.artifact_refs.append(LineageRef(
                            ref=other_ref, role="input", liveness=_live(other_ref),
                            producer_capability=other_cap,
                        ))
        if entry.artifact_refs or entry.recompute_capabilities:
            lineage.entries[entry.facet_id] = entry
    return lineage


__all__ = [
    "LineageRef",
    "FacetLineageEntry",
    "FacetArtifactLineage",
    "build_facet_lineage",
]
