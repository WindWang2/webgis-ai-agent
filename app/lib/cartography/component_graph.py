"""Component Graph — MapSpec 组件实例的图投影（V7, Goal 08 Phase B）.

``Template = Component Graph + Constraint Set + Style Tokens`` 的「图」一侧：
MapSpec 的 ``layout.components`` 是**存储形态**（扁平列表，round-trip 保真，
layout_solver / render 链继续消费列表），本模块把它投影为**运行时图**：

- 节点 = 组件实例 + registry 语义投影（semantic_role / collision_class /
  size_range / placement mode）；
- 类型化边 = ``binds_to``（组件→layer/source 数据绑定）、``requires``
  （组件→组件依赖）、``groups``（容器→子组件）、``annotates``（注记→对象）、
  ``under``（z 序：src 渲染在 dst 之下）。

边来源三通道（单一事实仍是 MapSpec，本模块不建第二存储）：

1. **derived** —— 既有语义推导：``options.layerId`` → binds_to(layer)；
   subtitle → annotates(title)；图例族与 chart 绑定语义同前；
2. **explicit** —— ``layout.component_links``（schema 1.2 additive，有界）；
   推导规则覆盖不了的组合关系由模板/Harness 显式声明；
3. **descriptor** —— registry ``dependencies`` 投影为缺失依赖披露（不造边：
   descriptor 依赖是类型级知识，实例边必须落在实际在场节点上）。

纯函数、确定性、有界披露。本模块不做布局计算（layout_solver /
layout_geometry 职责），不做渲染；QA（semantic_checks 的重复绑定/越界
检查）与 Harness finalization 消费本模块的结构化输出。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

#: 边类型词表（与 mapspec_schema.COMPONENT_LINK_TYPES 同表 —— schema 侧
#: 管存储校验，本侧管语义；改表必须双侧同步）。
LINK_TYPES = (
    "binds_to",
    "requires",
    "groups",
    "annotates",
    "under",
)

LinkType = Literal["binds_to", "requires", "groups", "annotates", "under"]
TargetKind = Literal["component", "layer", "source"]
LinkOrigin = Literal["explicit", "derived"]

#: 图披露上限（与 schema 披露封顶同词汇；防退化输入放大）。
MAX_GRAPH_DISCLOSURES = 64

#: options 里承载 layer 绑定的键（与 component_composer 写入面同表）。
_LAYER_BINDING_KEYS = ("layerId", "layer_id")

#: derived 通道：subtitle 依附 title（标题层级语义）。
_TITLE_HIERARCHY = (("subtitle", "title"),)


class ComponentNode(BaseModel):
    """图节点：一个组件实例 + registry 语义投影。"""

    id: str
    type: str
    enabled: bool = True
    semantic_role: str = ""
    collision_class: str = "panel"
    priority: int = 50
    variant: str = ""
    placement_mode: str = "anchor"       # anchor | floating
    layer_binding: str = ""              # options.layerId（若有）
    registry_known: bool = False         # descriptor 是否可解析


class ComponentLink(BaseModel):
    """一条类型化边。dst 按 dst_kind 解释：组件 id / layer id / source id。"""

    src: str
    dst: str
    dst_kind: TargetKind = "component"
    type: LinkType = "binds_to"
    origin: LinkOrigin = "derived"


class GraphIssue(BaseModel):
    """图级结构问题（可序列化；severity ∈ warning|error）。"""

    code: str
    severity: str
    message: str
    ids: List[str] = Field(default_factory=list)


class ComponentGraph(BaseModel):
    """组件图（纯投影，不改写 MapSpec）。"""

    nodes: List[ComponentNode] = Field(default_factory=list)
    links: List[ComponentLink] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)

    def node(self, component_id: str) -> Optional[ComponentNode]:
        for n in self.nodes:
            if n.id == component_id:
                return n
        return None

    def node_ids(self) -> List[str]:
        return [n.id for n in self.nodes]

    def links_of(self, component_id: str, link_type: Optional[str] = None) -> List[ComponentLink]:
        return [
            lk for lk in self.links
            if lk.src == component_id and (link_type is None or lk.type == link_type)
        ]

    def links_to(self, target: str, link_type: Optional[str] = None) -> List[ComponentLink]:
        return [
            lk for lk in self.links
            if lk.dst == target and (link_type is None or lk.type == link_type)
        ]

    def components_bound_to_layer(self, layer_id: str, *, component_type: str = "") -> List[str]:
        """绑定到某图层的组件 id（确定性序）；component_type 可再过滤。"""
        out: List[str] = []
        for lk in self.links:
            if lk.type != "binds_to" or lk.dst_kind != "layer" or lk.dst != layer_id:
                continue
            node = self.node(lk.src)
            if node is None:
                continue
            if component_type and node.type != component_type:
                continue
            out.append(lk.src)
        return sorted(out)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [
                {
                    "id": n.id[:48], "type": n.type[:32],
                    "role": n.semantic_role[:24], "floating": n.placement_mode == "floating",
                }
                for n in self.nodes[:24]
            ],
            "links": [
                {
                    "src": lk.src[:48], "dst": lk.dst[:48],
                    "kind": lk.dst_kind, "type": lk.type, "origin": lk.origin,
                }
                for lk in self.links[:32]
            ],
            "disclosures": [d[:160] for d in self.disclosures[:8]],
        }


# ── 构建（MapSpec dict → 图）─────────────────────────────────────────────


def _visible_enabled(component: Dict[str, Any]) -> bool:
    """enabled 缺省 True；显式 False 才下线（与 resolve_component 同口径）。"""
    enabled = component.get("enabled")
    return enabled is not False


def _placement_mode(component: Dict[str, Any]) -> str:
    placement = component.get("placement")
    if isinstance(placement, dict) and placement.get("mode") == "floating":
        return "floating"
    return "anchor"


def _layer_binding_of(component: Dict[str, Any]) -> str:
    options = component.get("options")
    if not isinstance(options, dict):
        return ""
    for key in _LAYER_BINDING_KEYS:
        val = options.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _project_node(component: Dict[str, Any], comp_reg: Any) -> ComponentNode:
    ctype = str(component.get("type") or "")
    desc = comp_reg.get(ctype) if hasattr(comp_reg, "get") else None
    if desc is None and hasattr(comp_reg, "get_by_type"):
        desc = comp_reg.get_by_type(ctype)
    raw_priority = component.get("priority")
    if isinstance(raw_priority, (int, float)) and not isinstance(raw_priority, bool):
        priority = int(raw_priority)
    else:
        priority = int(desc.priority) if desc is not None else 50
    return ComponentNode(
        id=str(component.get("id") or ""),
        type=ctype,
        enabled=_visible_enabled(component),
        semantic_role=(desc.semantic_role if desc is not None else ""),
        collision_class=(desc.collision_class if desc is not None else "panel"),
        priority=priority,
        variant=str(component.get("variant") or ""),
        placement_mode=_placement_mode(component),
        layer_binding=_layer_binding_of(component),
        registry_known=desc is not None,
    )


def build_component_graph(spec: Optional[Dict[str, Any]]) -> ComponentGraph:
    """MapSpec → 组件图。从不抛异常；结构问题进 disclosures。"""
    from app.lib.cartography.component_registry import get_component_registry

    comp_reg = get_component_registry()
    layout = (spec or {}).get("layout")
    if not isinstance(layout, dict):
        return ComponentGraph()
    raw_components = layout.get("components")
    if not isinstance(raw_components, list):
        raw_components = []

    disclosures: List[str] = []
    nodes: List[ComponentNode] = []
    by_id: Dict[str, Dict[str, Any]] = {}
    for component in raw_components:
        if not isinstance(component, dict):
            continue
        cid = str(component.get("id") or "")
        if not cid:
            disclosures.append("component missing id：节点丢弃（不可寻址）")
            continue
        if cid in by_id:
            disclosures.append(f"component id 重复：{cid[:48]}（保先）")
            continue
        by_id[cid] = component
        nodes.append(_project_node(component, comp_reg))

    known_layers, known_sources = _collect_layer_and_source_ids(spec)
    links: List[ComponentLink] = []

    # ── derived 通道 ─────────────────────────────────────────────────
    for node in nodes:
        component = by_id.get(node.id, {})
        if node.layer_binding:
            if node.layer_binding in known_layers:
                links.append(ComponentLink(
                    src=node.id, dst=node.layer_binding,
                    dst_kind="layer", type="binds_to", origin="derived"))
            else:
                disclosures.append(
                    f"binds_to 悬空：{node.id[:48]} → layer {node.layer_binding[:48]}")
        # 标题层级：subtitle 依附 title（双方在场才成边）
        for (src_type, dst_type) in _TITLE_HIERARCHY:
            if node.type == src_type:
                target = _first_enabled_of_type(nodes, dst_type)
                if target:
                    links.append(ComponentLink(
                        src=node.id, dst=target,
                        dst_kind="component", type="annotates", origin="derived"))
        # descriptor 声明的类型级依赖：实例缺失 → 披露（不造悬空边）
        desc = comp_reg.get(node.type) or comp_reg.get_by_type(node.type)
        if desc is not None:
            for dep_type in list(desc.dependencies)[:4]:
                if not _any_of_type(nodes, dep_type):
                    disclosures.append(
                        f"依赖缺失：{node.id[:48]} 需要 {dep_type[:32]} 在场")

    # ── explicit 通道（layout.component_links，schema 1.2）────────────
    explicit_raw = layout.get("component_links")
    if isinstance(explicit_raw, list):
        for raw in explicit_raw[:64]:
            if not isinstance(raw, dict):
                continue
            src = str(raw.get("src") or "")
            dst = str(raw.get("dst") or "")
            ltype = str(raw.get("type") or "")
            if ltype not in LINK_TYPES:
                disclosures.append(f"component_link 未知类型：{ltype[:24]}")
                continue
            if src not in by_id:
                disclosures.append(f"component_link 悬空 src：{src[:48]}")
                continue
            dst_kind = str(raw.get("dst_kind") or "component")
            if dst_kind not in ("component", "layer", "source"):
                dst_kind = "component"
            # 命名空间各自校验：layer 指向 layers、source 指向 sources，
            # 互不混用（混指是模板笔误，如实披露而非静默放行）。
            if dst_kind == "component" and dst not in by_id:
                disclosures.append(f"component_link 悬空 dst：{dst[:48]}")
                continue
            if dst_kind == "layer" and dst not in known_layers:
                disclosures.append(f"component_link 悬空 layer：{dst[:48]}")
                continue
            if dst_kind == "source" and dst not in known_sources:
                disclosures.append(f"component_link 悬空 source：{dst[:48]}")
                continue
            links.append(ComponentLink(
                src=src, dst=dst, dst_kind=dst_kind,  # type: ignore[arg-type]
                type=ltype, origin="explicit"))  # type: ignore[arg-type])

    if len(disclosures) > MAX_GRAPH_DISCLOSURES:
        disclosures = disclosures[:MAX_GRAPH_DISCLOSURES] + ["…disclosures truncated"]

    return ComponentGraph(nodes=nodes, links=links, disclosures=disclosures)


def _collect_layer_and_source_ids(spec: Optional[Dict[str, Any]]) -> Tuple[set, set]:
    """(layer ids, source ids) —— 两个命名空间分开维护。"""
    layer_ids: set = set()
    layers = (spec or {}).get("layers")
    if isinstance(layers, list):
        for layer in layers:
            if isinstance(layer, dict) and isinstance(layer.get("id"), str):
                layer_ids.add(layer["id"])
    source_ids: set = set()
    sources = (spec or {}).get("sources")
    if isinstance(sources, dict):
        source_ids.update(k for k in sources.keys() if isinstance(k, str))
    return layer_ids, source_ids


def _first_enabled_of_type(nodes: List[ComponentNode], ctype: str) -> str:
    for node in nodes:
        if node.type == ctype and node.enabled:
            return node.id
    return ""


def _any_of_type(nodes: List[ComponentNode], ctype: str) -> bool:
    return any(node.type == ctype for node in nodes)


# ── 校验（QA / harness finalization 消费）───────────────────────────────


#: duplicate_binding 检测的语义域：图例族同层重复才是冲突根语义
#: （QA 规则 DUPLICATE_LEGEND_BINDING 的域）。chart_panel 等
#: cardinality=multiple 组件同层多实例是合法构成（双图表产品），不在
#: 此列 —— 它们的重复由 composition binding 语义另行裁决。
LEGEND_FAMILY_TYPES = frozenset({"legend", "continuous_colorbar", "categorical_legend"})


def validate_component_graph(graph: ComponentGraph) -> List[GraphIssue]:
    """图级结构校验。码表（QA 与测试锁词表）：

    - ``duplicate_binding``：同型**图例族**组件绑定同一图层（图例重复的
      根语义；其余类型同层多实例合法，不在此列）
    - ``cycle``：requires/under 边成环（z 序/依赖不可满足）
    - ``unknown_component_type``：registry 无该类型 descriptor
    - ``orphan_binding``：binds_to 的组件无 layer_binding 语义却被显式声明
      （显式边与 options.layerId 冲突）
    """
    issues: List[GraphIssue] = []

    # duplicate binding：同型图例族组件 binds_to 同一 layer
    seen_binding: Dict[Tuple[str, str], List[str]] = {}
    for lk in graph.links:
        if lk.type != "binds_to" or lk.dst_kind != "layer":
            continue
        node = graph.node(lk.src)
        if node is None or node.type not in LEGEND_FAMILY_TYPES:
            continue
        seen_binding.setdefault((node.type, lk.dst), []).append(lk.src)
    for (ctype, layer_id), ids in sorted(seen_binding.items()):
        if len(ids) > 1:
            issues.append(GraphIssue(
                code="duplicate_binding", severity="warning",
                message=f"{len(ids)} 个 {ctype} 组件绑定同一图层 {layer_id}",
                ids=sorted(ids)[:8]))

    # cycle 检测（requires / under；Kahn 残留 = 环）
    for cycle_type in ("requires", "under"):
        residual = _cycle_nodes(graph, cycle_type)
        if residual:
            issues.append(GraphIssue(
                code="cycle", severity="error",
                message=f"{cycle_type} 边成环：{' → '.join(residual[:6])}",
                ids=residual))

    # unknown component type
    for node in graph.nodes:
        if not node.registry_known:
            issues.append(GraphIssue(
                code="unknown_component_type", severity="warning",
                message=f"组件 {node.id} 类型 {node.type} 未注册 descriptor",
                ids=[node.id]))

    # 显式 binds_to 与 options.layerId 冲突
    for lk in graph.links:
        if lk.type != "binds_to" or lk.origin != "explicit" or lk.dst_kind != "layer":
            continue
        node = graph.node(lk.src)
        if node is not None and node.layer_binding and node.layer_binding != lk.dst:
            issues.append(GraphIssue(
                code="orphan_binding", severity="warning",
                message=f"组件 {lk.src} 的 layerId={node.layer_binding} 与显式边 "
                        f"binds_to {lk.dst} 冲突",
                ids=[lk.src, lk.dst]))
    return issues


def _precedence_edges(graph: ComponentGraph, link_type: str) -> List[Tuple[str, str]]:
    """(先, 后) 前序对。

    - ``requires``：dst 是 src 的依赖 → dst 先；
    - ``under``：src 渲染在 dst 之下（先绘制，后绘制者覆盖其上）→ src 先。
    """
    out: List[Tuple[str, str]] = []
    for lk in graph.links:
        if lk.type != link_type or lk.dst_kind != "component":
            continue
        if link_type == "requires":
            out.append((lk.dst, lk.src))
        else:
            out.append((lk.src, lk.dst))
    return out


def _cycle_nodes(graph: ComponentGraph, link_type: str) -> List[str]:
    """环上节点（确定性序）。

    Kahn 残量包含环本身及其下游受害者（被环阻塞的无辜节点）—— 对下游
    再做迭代零出度剪枝，只留真正在环上的节点（QA 证据不得诬指无辜）。
    """
    indegree = {n.id: 0 for n in graph.nodes}
    adjacency: Dict[str, List[str]] = {n.id: [] for n in graph.nodes}
    for first, second in _precedence_edges(graph, link_type):
        if first in indegree and second in indegree:
            adjacency[first].append(second)
            indegree[second] += 1
    queue = sorted(nid for nid, deg in indegree.items() if deg == 0)
    visited = 0
    while queue:
        nid = queue.pop(0)
        visited += 1
        for nxt in adjacency.get(nid, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
        queue.sort()
    if visited == len(indegree):
        return []
    residual = {nid for nid, deg in indegree.items() if deg > 0}
    # 残量子图内反复剪零出度节点（出边指向残量外的不计），余下即环成员
    changed = True
    while changed and residual:
        changed = False
        for nid in sorted(residual):
            if not any(nxt in residual for nxt in adjacency.get(nid, [])):
                residual.discard(nid)
                changed = True
    return sorted(residual)


def topological_component_order(graph: ComponentGraph) -> List[str]:
    """确定性渲染/导出顺序：under/requires 边尊重先后语义，其余按
    (priority, id)。

    语义：``requires`` 依赖者后绘制；``under`` 的 src 在 dst 之下先绘制。
    环存在时回退纯 (priority, id) 序（渲染必须有确定序 —— 环本身由
    validate_component_graph 另行报警，不阻塞渲染）。
    """
    base_order = sorted(
        graph.nodes, key=lambda n: (n.priority, n.id))
    fallback = [n.id for n in base_order]
    rank = {nid: i for i, nid in enumerate(fallback)}

    indegree = {n.id: 0 for n in graph.nodes}
    adjacency: Dict[str, List[str]] = {n.id: [] for n in graph.nodes}
    for link_type in ("requires", "under"):
        for first, second in _precedence_edges(graph, link_type):
            if first in indegree and second in indegree:
                adjacency[first].append(second)
                indegree[second] += 1
    # 最小堆语义：每步取 rank 最小的零入度节点（稳定且确定性）
    import heapq
    heap = [rank[nid] for nid, deg in indegree.items() if deg == 0]
    heapq.heapify(heap)
    out: List[str] = []
    rank_to_id = {v: k for k, v in rank.items()}
    while heap:
        nid = rank_to_id[heapq.heappop(heap)]
        out.append(nid)
        for nxt in sorted(adjacency.get(nid, [])):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(heap, rank[nxt])
    if len(out) == len(indegree):
        return out
    return fallback


def graph_summary(spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """有界图摘要（tool/manifest 投影用）。"""
    graph = build_component_graph(spec)
    issues = validate_component_graph(graph)
    return {
        "node_count": len(graph.nodes),
        "link_count": len(graph.links),
        "roles": sorted({n.semantic_role for n in graph.nodes if n.semantic_role}),
        "floating": sorted(n.id for n in graph.nodes if n.placement_mode == "floating"),
        "issues": [i.model_dump() for i in issues[:8]],
        "disclosure_count": len(graph.disclosures),
    }


# ── 断环（AC-07 / ADR-0156：检测 → 可执行修复建议）─────────────────────


def _link_weight(graph: ComponentGraph, link: ComponentLink) -> Tuple[int, str, str]:
    """断环权重键：端点 priority 和（小者优先断 —— 最低权重链接先断），
    平局按 (dst, src) 字典序保证确定性。"""
    src = graph.node(link.src)
    dst = graph.node(link.dst)
    w = (src.priority if src else 50) + (dst.priority if dst else 50)
    return (w, link.dst, link.src)


def _find_one_cycle(graph: ComponentGraph, link_type: str) -> Optional[List[ComponentLink]]:
    """在给定边型的前序子图上找**一条**环（确定性：节点/邻接均字典序 DFS）。"""
    adjacency: Dict[str, List[str]] = {n.id: [] for n in graph.nodes}
    link_index: Dict[Tuple[str, str], ComponentLink] = {}
    for first, second in _precedence_edges(graph, link_type):
        if first in adjacency and second in adjacency:
            adjacency[first].append(second)
            link_index[(first, second)] = next(
                lk for lk in graph.links
                if lk.type == link_type and lk.dst_kind == "component"
                and ((lk.dst, lk.src) if link_type == "requires" else (lk.src, lk.dst))
                == (first, second)
            )
    for nid in adjacency:
        adjacency[nid] = sorted(set(adjacency[nid]))

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in adjacency}
    path: List[str] = []

    def dfs(u: str) -> Optional[List[str]]:
        color[u] = GRAY
        path.append(u)
        for v in adjacency.get(u, []):
            if color.get(v, BLACK) == GRAY:
                # 环 = path 中从 v 起的片段 + 回边 v←u
                start = path.index(v)
                return path[start:] + [v]
            if color.get(v, BLACK) == WHITE:
                found = dfs(v)
                if found:
                    return found
        path.pop()
        color[u] = BLACK
        return None

    for nid in sorted(adjacency):
        if color[nid] == WHITE:
            cycle = dfs(nid)
            if cycle:
                pairs = list(zip(cycle, cycle[1:]))
                return [link_index[p] for p in pairs if p in link_index]
    return None


def break_component_cycles(
    graph: ComponentGraph, *, max_breaks: int = 8
) -> List[Dict[str, Any]]:
    """确定性断环规划：每个 requires/under 环断开**最低权重**边。

    权重 = 端点 priority 之和（低优先级的边先断 —— 断开对渲染序影响
    最小的链接），平局按 (dst, src) 字典序。返回建议移除的显式边清单
    （suggested_fix.remove_links 载荷 + evidence 同构）；只建议 explicit
    边可移除，derived 边成环说明语义建模错误，如实返回 origin 标记。

    循环终止：每次迭代恰断一边，``max_breaks`` 封顶（防御病态图）；
    纯函数 —— 不改写输入图。
    """
    removals: List[Dict[str, Any]] = []
    working = graph.model_copy(deep=True)
    for _ in range(max_breaks):
        cycle = None
        cycle_type = None
        for lt in ("requires", "under"):
            cycle = _find_one_cycle(working, lt)
            if cycle:
                cycle_type = lt
                break
        if not cycle or cycle_type is None:
            break
        victim = min(cycle, key=lambda lk: _link_weight(working, lk))
        w, _, _ = _link_weight(working, victim)
        removals.append({
            "src": victim.src,
            "dst": victim.dst,
            "type": victim.type,
            "origin": victim.origin,
            "weight": w,
            "reason": f"cycle_break:lowest_weight:{cycle_type}",
        })
        working.links = [
            lk for lk in working.links
            if not (lk.src == victim.src and lk.dst == victim.dst
                    and lk.type == victim.type)
        ]
    return removals


__all__ = [
    "LINK_TYPES",
    "ComponentNode",
    "ComponentLink",
    "GraphIssue",
    "ComponentGraph",
    "build_component_graph",
    "validate_component_graph",
    "topological_component_order",
    "graph_summary",
    "break_component_cycles",
]
