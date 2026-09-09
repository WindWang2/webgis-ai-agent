"""Partial Recompute V4 —— 变更 → 受影响子图（编译期语义，执行归 runtime）。

输入/参数/算法变化后，哪些节点必须重算、哪些产物可复用？本模块在
typed DAG 上给出**精确**回答（正向闭包），替代「全量重跑」与
「拍脑袋复用」两个极端：

    WorkflowChange（维度 ⊆ RECOMPUTE_DIMENSIONS + 目标）
        → dirty 种子节点 → 数据流边 ∪ 结构依赖的正向闭包
        → RecomputePlan { recompute: [...], reuse: [...], explanations }

红线：

- 维度词表复用 workflow_schema.RECOMPUTE_DIMENSIONS（单一事实源）；
- 只算集合：重算的调度/校验/恢复由 Harness runtime 负责；
- 保守正确：闭包内节点一律重算；闭包外节点才可复用（宁可多算，
  不错误复用陈旧产物）；
- 全部确定性、O(nodes+edges)、零 LLM / 零 I/O。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set

from pydantic import BaseModel, Field

from app.services.gis_harness.workflow_schema import RECOMPUTE_DIMENSIONS

#: 变更目标种类（seed 解析规则）。
CHANGE_TARGETS = (
    "data_role",      # target = 角色名 → data:<role> 节点
    "parameter",      # target = 参数名 → 拥有该参数的节点（param_owner）
    "algorithm",      # target = node_id（算法替换）
    "style",          # target = node_id 或空 → 只影响呈现，不触发科学重算
    "output",         # target = output node_id
    "recipe",         # target 空 → recipe 级变化 = 全图失效（保守正确）
)


class WorkflowChange(BaseModel):
    """一次声明式变更（diff/用户编辑/数据更新投影为若干 change）。"""
    dimension: str                     # ⊆ RECOMPUTE_DIMENSIONS
    target_kind: str                   # ⊆ CHANGE_TARGETS
    target: str = ""                   # 角色/参数名/节点 id
    detail: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension[:24],
            "target_kind": self.target_kind[:24],
            "target": self.target[:64],
            "detail": self.detail[:200],
        }


class RecomputePlan(BaseModel):
    """受影响子图裁决（有界、可解释）。"""
    recompute: List[str] = Field(default_factory=list)   # 节点 id（拓扑近似序）
    reuse: List[str] = Field(default_factory=list)       # 可复用节点
    reuse_artifacts: List[str] = Field(default_factory=list)  # 可复用输出 node_id
    changed_dimensions: List[str] = Field(default_factory=list)
    explanations: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "recompute": [n[:64] for n in self.recompute[:32]],
            "reuse": [n[:64] for n in self.reuse[:32]],
            "reuse_artifacts": [n[:64] for n in self.reuse_artifacts[:8]],
            "changed_dimensions": list(self.changed_dimensions[:5]),
            "explanations": [e[:200] for e in self.explanations[:6]],
        }


def _seed_nodes(
    change: WorkflowChange,
    node_ids: Sequence[str],
    *,
    param_owners: Optional[Dict[str, str]] = None,
) -> List[str]:
    if change.target_kind == "data_role":
        nid = f"data:{change.target}"
        return [nid] if nid in node_ids else []
    if change.target_kind == "parameter":
        owner = (param_owners or {}).get(change.target)
        return [owner] if owner else []
    if change.target_kind == "recipe":
        # recipe 级变化：数据需求与方法面整体更换 —— 全图失效（保守正确：
        # 宁可多算，不错误复用旧 recipe 的产物）。
        return list(node_ids)
    if change.target_kind in ("algorithm", "style", "output"):
        return [change.target] if change.target in node_ids else []
    return []


def _node_id_of(endpoint: str, node_set: Set[str]) -> str:
    """bounded 边端点（"node.port"）→ 节点 id 归一化（V5 修复）。

    ``to_bounded_dict()`` 形态的边端点携带端口后缀（``"data:x.data"``），
    而种子/节点 id 是裸节点 id —— 不归一则邻接表键与种子永不相遇，
    正向闭包失效（真实包形态实测：data_role 变更只标记种子自身）。
    仅当剥离端口后缀后的前缀确实是已知节点 id 时归一（节点 id 词表
    ``<kind>:<name>`` 与端口名均不含 ``.``；无法归一时保持原样）。
    """
    ep = str(endpoint or "")
    if ep in node_set:
        return ep
    head, sep, _tail = ep.rpartition(".")
    return head if sep and head and head in node_set else ep


def compute_affected_subgraph(
    dag: Dict[str, Any],
    changes: Sequence[WorkflowChange],
) -> RecomputePlan:
    """bounded typed DAG（to_bounded_dict 形态）上的受影响子图计算。

    ``dag`` 可为 live graph 的 ``to_bounded_dict()`` 或 package compiled
    form 的 ``typed_dag`` —— 同一形状，同一函数。
    """
    nodes = [n.get("node_id", "") for n in (dag.get("nodes") or [])]
    node_set = {n for n in nodes if n}
    param_owners: Dict[str, str] = {}
    for n in dag.get("nodes") or []:
        for p in n.get("parameters", ()) or []:
            if isinstance(p, dict) and p.get("name"):
                param_owners[str(p["name"])] = str(n.get("node_id", ""))

    adjacency: Dict[str, List[str]] = {}
    for e in dag.get("edges") or []:
        src = _node_id_of(e.get("from", ""), node_set)
        dst = _node_id_of(e.get("to", ""), node_set)
        adjacency.setdefault(src, []).append(dst)
    for n in dag.get("nodes") or []:
        for dep in n.get("depends_on", ()) or []:
            adjacency.setdefault(str(dep), []).append(str(n.get("node_id", "")))

    seeds: List[str] = []
    dimensions: List[str] = []
    explanations: List[str] = []
    for ch in changes:
        if ch.dimension not in RECOMPUTE_DIMENSIONS:
            continue
        if ch.dimension not in dimensions:
            dimensions.append(ch.dimension)
        found = _seed_nodes(ch, node_set, param_owners=param_owners)
        if found:
            seeds.extend(found)
            if ch.target_kind == "style":
                explanations.append(
                    f"style:{ch.target} → 呈现态变更，科学子图零触碰")
            else:
                explanations.append(
                    f"{ch.dimension}:{ch.target_kind}:{ch.target} → {found[0]}")
        elif ch.target_kind == "style":
            # style 变更不触发科学重算（呈现态归 runtime 呈现层）
            explanations.append(
                f"style:{ch.target} → 呈现态变更，科学子图零触碰")
        else:
            explanations.append(
                f"{ch.target_kind}:{ch.target} → 图中无对应节点（no-op）")

    # 正向闭包（数据流边 ∪ 结构依赖）
    dirty: Set[str] = set()

    def _mark(nid: str) -> None:
        if nid in dirty or nid not in node_set:
            return
        dirty.add(nid)
        for nxt in adjacency.get(nid, ()):
            _mark(nxt)

    for s in seeds:
        _mark(s)

    # style-only：dirty 集里去掉纯 style 种子自身？——保留（呈现节点
    # 标记为重算无害；科学维度未触发时 runtime 只推进呈现态）。
    recompute = sorted(dirty)
    reuse = sorted(n for n in node_set if n not in dirty)
    reuse_artifacts = sorted(
        n for n in reuse if n.startswith("output:"))
    return RecomputePlan(
        recompute=recompute,
        reuse=reuse,
        reuse_artifacts=reuse_artifacts,
        changed_dimensions=dimensions,
        explanations=explanations,
    )
