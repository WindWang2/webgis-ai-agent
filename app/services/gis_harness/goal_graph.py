"""SpatialGoalGraph —— 可序列化/可验证/可增量更新的空间目标图（V4 Wave 3）。

与 AnalysisGraph（ADR-0097，会话检视投影）的分工：

- AnalysisGraph 回答「这个会话**正在做**什么」（执行/产品节点的事实投影）；
- SpatialGoalGraph 回答「这个目标**方法学上需要**什么」——从章节事实
  确定性展开的方法骨架：goal → acquire → inspect → validate → transform
  → analyze/aggregate/model/compare → verify → cartography → observe →
  deliver（+ disclose 披露节点）。

红线：

- **确定性展开**：节点/边全部派生自章节事实（行、方法学警告、workflow
  契约、capability registry 元数据）；同输入同图（fingerprint 稳定）。
  LLM 可以*建议* candidate graph，但收敛由 ``validate_candidate_graph``
  确定性裁决（词表/规模/环/能力对账）——绝不由语言直接造节点。
- **不创建第二 workflow truth**：图的「事实」仍是 SessionPlan 章节 +
  WorkflowProfile + methodology warnings；本模块零持久化、可随时重建
  （删缓存立即重建，值不变——与 analysis_graph 同一不变式）。节点状态
  是章节行的只读投影。
- **有界**：≤48 节点、每节点 ≤6 依赖、label ≤80。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

# ── 词汇表 ───────────────────────────────────────────────────────────────

_MAX_NODES = 48
_MAX_DEPS = 6
_MAX_LABEL = 80
_MAX_WARNINGS_NODES = 4


class GoalNodeKind(str, Enum):
    GOAL = "goal"
    ACQUIRE = "acquire"          # 数据获取（capability 行 / 角色）
    INSPECT = "inspect"          # 数据检视（profile / 质量）
    VALIDATE = "validate"        # 科学有效性验证（CRS / 几何 / 采样）
    TRANSFORM = "transform"      # 前置变换（投影 / 定标 / 清洗）
    ANALYZE = "analyze"          # 空间分析
    AGGREGATE = "aggregate"      # 聚合 / 分区统计
    MODEL = "model"              # 建模 / 插值 / 回归
    COMPARE = "compare"          # 比较 / 变化检测
    VERIFY = "verify"            # 结果 / 完成验证
    CARTOGRAPHY = "cartography"  # 制图（图层 / 组件）
    OBSERVE = "observe"          # 渲染观察
    DELIVER = "deliver"          # 用户输出
    DISCLOSE = "disclose"        # 方法论 / 不确定性披露


class DependencyKind(str, Enum):
    DATA = "data"                # 数据依赖（acquire/inspect/validate 链）
    SCIENCE = "science"          # 科学依赖（方法成立前提）
    ARTIFACT = "artifact"        # 产物依赖（capability DAG）
    MAP = "map"                  # 地图依赖（图层/组件）
    DISCLOSURE = "disclosure"    # 披露依赖（结论输出前必须披露）


class GoalNodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"
    STALE = "stale"
    SKIPPED = "skipped"
    FAILED = "failed"


#: capability registry category → 节点 kind（确定性分类；未命中 → ANALYZE）。
_CATEGORY_TO_KIND: Dict[str, GoalNodeKind] = {
    "aggregate": GoalNodeKind.AGGREGATE,
    "aggregation": GoalNodeKind.AGGREGATE,
    "statistics": GoalNodeKind.AGGREGATE,
    "interpolation": GoalNodeKind.MODEL,
    "regression": GoalNodeKind.MODEL,
    "geostatistics": GoalNodeKind.MODEL,
    "model": GoalNodeKind.MODEL,
    "comparison": GoalNodeKind.COMPARE,
    "change_detection": GoalNodeKind.COMPARE,
}

#: 方法学警告码 → 方法骨架节点（分母红线：缺分母必须出现获取+归一化节点，
#: 让「教育公平/人均/率」类目标的科学欠账在图上可见，而不是藏在文案里）。
_DENOMINATOR_MARKERS = ("DENOMINATOR",)


def _row_kind(capability: str, purpose: str) -> Optional[GoalNodeKind]:
    """capability → 分析节点 kind（registry category 确定性映射）。

    返回 None = 该行不产生分析节点（data_access 行已有 acquire 链，
    重复造 analyze 节点是语义噪声）。
    """
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        d = get_capability_registry().get(capability)
        category = str(getattr(d, "category", "") or "").lower()
        if category == "data_access":
            return None
        if category in _CATEGORY_TO_KIND:
            return _CATEGORY_TO_KIND[category]
    except Exception:  # noqa: BLE001 — registry 缺席退化为 ANALYZE
        pass
    text = f"{capability} {purpose}".lower()
    for marker, kind in (
        # 「aggregat」同时命中 aggregate/aggregation（「aggregation」并不
        # 包含完整「aggregate」子串）；中文目的词同样参与确定性分类。
        ("aggregat", GoalNodeKind.AGGREGATE), ("density", GoalNodeKind.AGGREGATE),
        ("聚合", GoalNodeKind.AGGREGATE), ("密度", GoalNodeKind.AGGREGATE),
        ("interpol", GoalNodeKind.MODEL), ("krig", GoalNodeKind.MODEL),
        ("regress", GoalNodeKind.MODEL), ("插值", GoalNodeKind.MODEL),
        ("回归", GoalNodeKind.MODEL),
        ("compare", GoalNodeKind.COMPARE), ("change", GoalNodeKind.COMPARE),
        ("对比", GoalNodeKind.COMPARE), ("变化检测", GoalNodeKind.COMPARE),
    ):
        if marker in text:
            return kind
    return GoalNodeKind.ANALYZE


# ── 模型 ─────────────────────────────────────────────────────────────────

class GoalNode(BaseModel):
    """目标图节点（kind + 确定性来源 + 只读状态投影）。"""

    id: str
    kind: str                          # GoalNodeKind 值
    label: str = ""
    capability: str = ""               # 关联章节行 capability（可空）
    source: str = ""                   # row | warning:<CODE> | role:<role> | synthetic
    status: str = GoalNodeStatus.PENDING.value
    depends_on: List[str] = []
    dep_kinds: List[str] = []          # 与 depends_on 一一对应（DependencyKind 值）

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id[:64],
            "kind": self.kind,
            "label": self.label[:_MAX_LABEL],
            "capability": self.capability[:64],
            "source": self.source[:64],
            "status": self.status,
            "depends_on": [d[:64] for d in self.depends_on[:_MAX_DEPS]],
            "dep_kinds": [k for k in self.dep_kinds[:_MAX_DEPS]],
        }


class SpatialGoalGraph(BaseModel):
    """空间目标图（可序列化、可 diff、可重建）。"""

    goal: str = ""
    plan_id: str = ""
    recipe_id: str = ""
    nodes: List[GoalNode] = Field(default_factory=list)

    def fingerprint(self) -> str:
        from app.services.gis_harness.workflow_instance import canonical_fingerprint

        return canonical_fingerprint(
            [n.to_bounded_dict() for n in self.nodes]
        )

    def node(self, node_id: str) -> Optional[GoalNode]:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "schema": "goal_graph.v1",
            "goal": self.goal[:200],
            "plan_id": self.plan_id[:64],
            "recipe_id": self.recipe_id[:64],
            "fingerprint": self.fingerprint(),
            "node_count": len(self.nodes),
            "nodes": [n.to_bounded_dict() for n in self.nodes[:_MAX_NODES]],
        }

    def summary_line(self) -> str:
        """单行有界投影（LLM 面）。"""
        if not self.nodes:
            return ""
        kinds: Dict[str, int] = {}
        for n in self.nodes:
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        blocked = [n.id for n in self.nodes if n.status == GoalNodeStatus.BLOCKED.value]
        line = "[GIS GoalGraph] " + " ".join(f"{k}={v}" for k, v in kinds.items())
        if blocked:
            line += " blocked:" + ",".join(blocked[:3])
        return line[:400]

    def diff(self, other: "SpatialGoalGraph") -> Dict[str, List[str]]:
        """结构 diff：added / removed / changed（id + kind 变化；状态不算
        结构变化——那是 WorkflowInstance 的职责）。"""
        a = {n.id: n for n in self.nodes}
        b = {n.id: n for n in other.nodes}
        added = sorted(set(b) - set(a))
        removed = sorted(set(a) - set(b))
        changed = sorted(
            nid for nid in set(a) & set(b)
            if a[nid].kind != b[nid].kind
            or a[nid].depends_on != b[nid].depends_on
        )
        return {"added": added, "removed": removed, "changed": changed}


# ── 确定性展开 ───────────────────────────────────────────────────────────

def _status_from_row(status: str) -> str:
    """行状态 → 节点状态（只读投影，与 plan_graph 同映射）。"""
    return {
        "pending": GoalNodeStatus.PENDING.value,
        "available": GoalNodeStatus.SATISFIED.value,
        "done": GoalNodeStatus.SATISFIED.value,
        "complete": GoalNodeStatus.SATISFIED.value,
        "unavailable": GoalNodeStatus.BLOCKED.value,
        "skipped": GoalNodeStatus.SKIPPED.value,
        "failed": GoalNodeStatus.FAILED.value,
    }.get(str(status or ""), GoalNodeStatus.PENDING.value)


def build_goal_graph(chapter: Dict[str, Any]) -> SpatialGoalGraph:
    """章节事实 → 确定性方法骨架（零 LLM、零 IO、可重建）。"""
    graph = SpatialGoalGraph(
        goal=str(chapter.get("query") or ""),
        plan_id=str(chapter.get("plan_id") or ""),
        recipe_id=str(chapter.get("recipe_id") or ""),
    )
    goal_node = GoalNode(
        id="goal",
        kind=GoalNodeKind.GOAL.value,
        label=str(chapter.get("query") or "")[:_MAX_LABEL],
        source="row",
        status=GoalNodeStatus.READY.value,
    )
    graph.nodes.append(goal_node)

    requirements = [r for r in chapter.get("data_requirements") or [] if isinstance(r, dict)]
    steps = [s for s in chapter.get("analysis_steps") or [] if isinstance(s, dict)]

    # acquire/inspect/validate：每个数据需求行一条科学链。
    analyze_deps: Dict[str, List[Tuple[str, DependencyKind]]] = {}
    for row in requirements[:12]:
        cap = str(row.get("capability") or "")
        if not cap:
            continue
        acq_id = f"acquire:{cap}"
        insp_id = f"inspect:{cap}"
        val_id = f"validate:{cap}"
        row_status = _status_from_row(row.get("status"))
        graph.nodes.append(GoalNode(
            id=acq_id, kind=GoalNodeKind.ACQUIRE.value, label=cap,
            capability=cap, source="row", status=row_status,
            depends_on=["goal"], dep_kinds=[DependencyKind.SCIENCE.value],
        ))
        graph.nodes.append(GoalNode(
            id=insp_id, kind=GoalNodeKind.INSPECT.value, label=cap,
            capability=cap, source="synthetic",
            status=row_status if row_status == GoalNodeStatus.SATISFIED.value
            else GoalNodeStatus.PENDING.value,
            depends_on=[acq_id], dep_kinds=[DependencyKind.DATA.value],
        ))
        graph.nodes.append(GoalNode(
            id=val_id, kind=GoalNodeKind.VALIDATE.value, label=cap,
            capability=cap, source="synthetic",
            status=GoalNodeStatus.PENDING.value,
            depends_on=[insp_id], dep_kinds=[DependencyKind.SCIENCE.value],
        ))
        analyze_deps.setdefault(cap, []).append(
            (val_id, DependencyKind.SCIENCE))

    # analyze/aggregate/model/compare：每个分析步骤行一个节点；
    # 依赖 = 上游 capability 的 validate/analyze 节点（ARTIFACT 语义）。
    step_node_by_cap: Dict[str, str] = {}
    for row in steps[:16]:
        cap = str(row.get("capability") or "")
        if not cap:
            continue
        kind = _row_kind(cap, str(row.get("purpose") or ""))
        if kind is None:
            continue  # data_access 行：acquire 链已表达，不造分析节点
        node_id = f"{kind.value}:{cap}"
        deps: List[Tuple[str, DependencyKind]] = []
        for dep_cap in list(row.get("depends_on") or [])[:_MAX_DEPS]:
            upstream = step_node_by_cap.get(str(dep_cap))
            if upstream:
                deps.append((upstream, DependencyKind.ARTIFACT))
            else:
                for vid, dkind in analyze_deps.get(str(dep_cap), []):
                    deps.append((vid, dkind))
        if not deps:
            deps = [("goal", DependencyKind.SCIENCE)]
        row_status = _status_from_row(row.get("status"))
        graph.nodes.append(GoalNode(
            id=node_id, kind=kind.value,
            label=str(row.get("purpose") or cap)[:_MAX_LABEL],
            capability=cap, source="row", status=row_status,
            depends_on=[d for d, _ in deps[:_MAX_DEPS]],
            dep_kinds=[k.value for _, k in deps[:_MAX_DEPS]],
        ))
        step_node_by_cap[cap] = node_id

    # 方法学欠账 → 方法骨架节点（分母红线 + 披露义务）。
    warnings = [
        w for w in chapter.get("methodology_warnings") or [] if isinstance(w, dict)
    ]
    has_denominator_debt = any(
        any(m in str(w.get("code") or "") for m in _DENOMINATOR_MARKERS)
        for w in warnings
    )
    if has_denominator_debt:
        graph.nodes.append(GoalNode(
            id="acquire:denominator", kind=GoalNodeKind.ACQUIRE.value,
            label="分母数据（人口/单元总数）", source="role:denominator",
            status=GoalNodeStatus.BLOCKED.value,
            depends_on=["goal"], dep_kinds=[DependencyKind.SCIENCE.value],
        ))
        normalization_deps = [
            nid for nid in (
                step_node_by_cap.get("admin_aggregation"),
                *[v for k, v in step_node_by_cap.items() if k.endswith("aggregation")],
            ) if nid
        ]
        graph.nodes.append(GoalNode(
            id="analyze:normalization", kind=GoalNodeKind.ANALYZE.value,
            label="人均/率归一化", source="role:denominator",
            status=GoalNodeStatus.BLOCKED.value,
            depends_on=[normalization_deps[0] if normalization_deps else "goal",
                        "acquire:denominator"],
            dep_kinds=[DependencyKind.ARTIFACT.value, DependencyKind.SCIENCE.value],
        ))
    for w in warnings[:_MAX_WARNINGS_NODES]:
        code = str(w.get("code") or "")
        if not code:
            continue
        graph.nodes.append(GoalNode(
            id=f"disclose:{code[:48]}", kind=GoalNodeKind.DISCLOSE.value,
            label=code[:_MAX_LABEL], source=f"warning:{code[:48]}",
            status=GoalNodeStatus.PENDING.value,
            depends_on=["goal"], dep_kinds=[DependencyKind.SCIENCE.value],
        ))

    # cartography/observe/verify/deliver：产品骨架（图层角色 → cartography）。
    layers = chapter.get("map_layers") or []
    if isinstance(layers, list) and layers:
        roles = sorted({
            str(layer.get("role") or "secondary")
            for layer in layers[:8] if isinstance(layer, dict)
        })
    else:
        roles = []
    cart_ids: List[str] = []
    for role in roles[:3]:
        cart_ids.append(f"cartography:{role}")
        graph.nodes.append(GoalNode(
            id=cart_ids[-1], kind=GoalNodeKind.CARTOGRAPHY.value,
            label=f"layer role: {role}", source="row",
            status=GoalNodeStatus.PENDING.value,
            depends_on=["goal"], dep_kinds=[DependencyKind.MAP.value],
        ))
    if not cart_ids:
        cart_ids = ["cartography:primary"]
        graph.nodes.append(GoalNode(
            id=cart_ids[0], kind=GoalNodeKind.CARTOGRAPHY.value,
            label="primary cartography", source="synthetic",
            status=GoalNodeStatus.PENDING.value,
            depends_on=["goal"], dep_kinds=[DependencyKind.MAP.value],
        ))
    analysis_ids = [n.id for n in graph.nodes if n.kind in (
        GoalNodeKind.ANALYZE.value, GoalNodeKind.AGGREGATE.value,
        GoalNodeKind.MODEL.value, GoalNodeKind.COMPARE.value,
    )]
    graph.nodes.append(GoalNode(
        id="verify", kind=GoalNodeKind.VERIFY.value, label="结果/完成验证",
        source="synthetic", status=GoalNodeStatus.PENDING.value,
        depends_on=(analysis_ids[:_MAX_DEPS - 1] or ["goal"]) + cart_ids[:1],
        dep_kinds=[DependencyKind.SCIENCE.value] * (len(analysis_ids[:_MAX_DEPS - 1]) or 1)
        + [DependencyKind.MAP.value],
    ))
    graph.nodes.append(GoalNode(
        id="observe", kind=GoalNodeKind.OBSERVE.value, label="渲染观察",
        source="synthetic", status=GoalNodeStatus.PENDING.value,
        depends_on=cart_ids[:_MAX_DEPS], dep_kinds=[DependencyKind.MAP.value] * len(cart_ids[:_MAX_DEPS]),
    ))
    deliver_deps = ["verify", "observe"] + [
        n.id for n in graph.nodes if n.kind == GoalNodeKind.DISCLOSE.value
    ][: _MAX_DEPS - 2]
    graph.nodes.append(GoalNode(
        id="deliver", kind=GoalNodeKind.DELIVER.value, label="用户输出",
        source="synthetic", status=GoalNodeStatus.PENDING.value,
        depends_on=deliver_deps[:_MAX_DEPS],
        dep_kinds=[DependencyKind.SCIENCE.value, DependencyKind.MAP.value]
        + [DependencyKind.DISCLOSURE.value] * (len(deliver_deps) - 2),
    ))
    return graph


# ── candidate 校验（LLM 建议 → 确定性收敛）──────────────────────────────

def validate_candidate_graph(
    candidate: Dict[str, Any],
    chapter: Dict[str, Any],
) -> Dict[str, Any]:
    """校验 LLM 建议的 goal graph（确定性裁决；返回 valid/errors/normalized）。

    规则（全部机器可断言）：
    - 节点 kind ⊆ GoalNodeKind 词表；
    - 节点数 ≤_MAX_NODES、每节点依赖 ≤_MAX_DEPS；
    - 依赖引用存在的节点且无环；
    - capability 引用必须出现在章节行（不许虚构能力）。
    ``normalized`` 是修复了引用缺失/依赖超限后的有界图（可拒绝项不静默
    修复——errors 非空时 valid=False，调用方应回退到 build_goal_graph）。
    """
    errors: List[str] = []
    raw_nodes = candidate.get("nodes") if isinstance(candidate, dict) else None
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return {"valid": False, "errors": ["nodes missing or empty"], "normalized": None}
    if len(raw_nodes) > _MAX_NODES:
        errors.append(f"too many nodes: {len(raw_nodes)} > {_MAX_NODES}")
    valid_kinds = {k.value for k in GoalNodeKind}
    chapter_caps = {
        str(r.get("capability"))
        for r in list(chapter.get("data_requirements") or [])
        + list(chapter.get("analysis_steps") or [])
        if isinstance(r, dict) and r.get("capability")
    }
    ids: List[str] = []
    cleaned: List[Dict[str, Any]] = []
    for n in raw_nodes:
        if not isinstance(n, dict):
            errors.append("non-dict node")
            continue
        nid = str(n.get("id") or "")
        kind = str(n.get("kind") or "")
        if not nid or kind not in valid_kinds:
            errors.append(f"invalid node id/kind: {nid!r}/{kind!r}")
            continue
        cap = str(n.get("capability") or "")
        if cap and cap not in chapter_caps:
            errors.append(f"unknown capability: {cap}")
            continue
        ids.append(nid)
        cleaned.append({
            "id": nid, "kind": kind,
            "label": str(n.get("label") or "")[:_MAX_LABEL],
            "capability": cap[:64],
            "depends_on": [str(d) for d in (n.get("depends_on") or [])[:_MAX_DEPS]],
        })
    id_set = set(ids)
    for n in cleaned:
        n["depends_on"] = [d for d in n["depends_on"] if d in id_set]
    # 环检测（DFS）
    adj = {n["id"]: n["depends_on"] for n in cleaned}
    state: Dict[str, int] = {}

    def _has_cycle(v: str) -> bool:
        state[v] = 1
        for w in adj.get(v, []):
            if state.get(w) == 1 or (state.get(w, 0) == 0 and _has_cycle(w)):
                return True
        state[v] = 2
        return False

    for v in adj:
        if state.get(v, 0) == 0 and _has_cycle(v):
            errors.append(f"cycle through {v}")
            break
    if errors:
        return {"valid": False, "errors": errors[:8], "normalized": None}
    return {
        "valid": True,
        "errors": [],
        "normalized": {
            "schema": "goal_graph.v1",
            "goal": str(candidate.get("goal") or chapter.get("query") or "")[:200],
            "nodes": cleaned,
        },
    }


__all__ = [
    "GoalNodeKind",
    "DependencyKind",
    "GoalNodeStatus",
    "GoalNode",
    "SpatialGoalGraph",
    "build_goal_graph",
    "validate_candidate_graph",
]
