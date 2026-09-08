"""Workflow Diff V4 —— 两个工作流包之间的语义 diff 与重算解释。

diff 的是**语义契约**，不是文本：recipe 指纹、方法选择、算法替换、
义务链、参数、typed DAG 结构。每个差异条目直接映射 RECOMPUTE_DIMENSIONS，
并给出「为什么需要重算」的机器可读解释。

红线：

- 输入是不可变包（WorkflowPackage，纯派生物），diff 纯函数；
- 算法替换区分 downgrade_class：同为 preferred 间替换 = algorithm 维；
  降级替换必须附披露（approximation/proxy 语义不得静默）；
- diff → WorkflowChange 列表，可直接喂 recompute.compute_affected_subgraph
  （typed interface 桥接，不复制图逻辑）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.services.gis_harness.workflow_v4.package import WorkflowPackage
from app.services.gis_harness.workflow_v4.recompute import (
    WorkflowChange,
)

#: diff 条目种类（稳定词表）。
DIFF_KINDS = (
    "recipe_change",           # recipe 指纹变化
    "method_change",           # 方法选择变化
    "algorithm_substitution",  # 同节点算法替换
    "obligation_change",       # 义务链变化
    "parameter_change",        # 参数默认值/范围变化
    "dag_structure_change",    # 节点/边结构变化
    "family_change",           # 方法论族变化
)


class DiffEntry(BaseModel):
    kind: str                         # ⊆ DIFF_KINDS
    path: str                         # 语义路径（如 typed_dag.cap:x.algorithm）
    old: str = ""
    new: str = ""
    disclosure: str = ""              # 降级替换等必须披露的语义
    change: WorkflowChange            # → recompute 的桥接

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind[:32],
            "path": self.path[:96],
            "old": self.old[:64],
            "new": self.new[:64],
            "disclosure": self.disclosure[:200],
            "change": self.change.to_bounded_dict(),
        }


class WorkflowDiff(BaseModel):
    compatible: bool                  # 包兼容性（major 不同 = 结构性 diff）
    entries: List[DiffEntry] = Field(default_factory=list)
    recompute_dimensions: List[str] = Field(default_factory=list)
    explanation: str = ""             # 「为什么重算」一句话（机器可读拼接）

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "compatible": self.compatible,
            "entries": [e.to_bounded_dict() for e in self.entries[:24]],
            "recompute_dimensions": list(self.recompute_dimensions[:5]),
            "explanation": self.explanation[:400],
        }


def _dag_map(pkg: WorkflowPackage) -> Dict[str, Dict[str, Any]]:
    nodes = (pkg.compiled_form.get("typed_dag") or {}).get("nodes") or []
    return {str(n.get("node_id", "")): n for n in nodes}


def _method_of(pkg: WorkflowPackage) -> str:
    return str(
        (pkg.compiled_form.get("method_qualification") or {}).get(
            "selected_id", "") or "")


def _node_for_method(pkg: WorkflowPackage, method_id: str) -> str:
    """方法 → 拥有它的 analysis 节点 id（diff→recompute 的 target 投影）。

    节点的 method_id 记录选中方法；找不到时回退第一个 analysis 节点
    （方法面变化至少波及主管线头节点），再无 → 空串（no-op 诚实退化）。
    """
    for nid, n in sorted(_dag_map(pkg).items()):
        if n.get("kind") == "analysis" and n.get("method_id") == method_id:
            return nid
    for nid, n in sorted(_dag_map(pkg).items()):
        if n.get("kind") == "analysis":
            return nid
    return ""


def _primary_output_of(pkg: WorkflowPackage) -> str:
    return str((pkg.compiled_form.get("typed_dag") or {}).get(
        "primary_output", "") or "")


def _param_map_of(pkg: WorkflowPackage) -> Dict[str, str]:
    """compiled form parameters → name → "value@provenance" 投影。"""
    out: Dict[str, str] = {}
    for p in pkg.compiled_form.get("parameters") or []:
        if isinstance(p, dict) and p.get("name"):
            out[str(p["name"])] = f"{p.get('value')!r}@{p.get('provenance')}"
    return out


def diff_workflow_packages(
    old: WorkflowPackage, new: WorkflowPackage,
) -> WorkflowDiff:
    """包间语义 diff（确定性；条目按 DIFF_KINDS 词表序 → path 排序）。

    每个条目的 ``change.target`` 都是**真实节点 id**（方法/族变化投影到
    拥有该方法的 analysis 节点；义务变化投影到主输出节点）——保证
    diff → compute_affected_subgraph 桥接无 no-op。
    """
    from app.services.gis_harness.workflow_v4.package import (
        check_compatibility,
    )
    compat = check_compatibility(new)
    entries: List[DiffEntry] = []

    # ── recipe / family ───────────────────────────────────────────────
    if old.recipe_fingerprint != new.recipe_fingerprint:
        entries.append(DiffEntry(
            kind="recipe_change", path="recipe.fingerprint",
            old=old.recipe_fingerprint[:16], new=new.recipe_fingerprint[:16],
            change=WorkflowChange(
                dimension="data", target_kind="recipe",
                target="", detail="recipe 内容指纹变化 → 全图失效重算")))
    if old.methodology_family != new.methodology_family:
        entries.append(DiffEntry(
            kind="family_change", path="methodology.family",
            old=old.methodology_family, new=new.methodology_family,
            change=WorkflowChange(
                dimension="algorithm", target_kind="algorithm",
                target=_node_for_method(new, _method_of(new)),
                detail="方法论族变化（方法面结构性变化）")))

    # ── 方法选择 ──────────────────────────────────────────────────────
    old_m, new_m = _method_of(old), _method_of(new)
    if old_m != new_m:
        entries.append(DiffEntry(
            kind="method_change", path="method_qualification.selected_id",
            old=old_m, new=new_m,
            change=WorkflowChange(
                dimension="algorithm", target_kind="algorithm",
                target=_node_for_method(new, new_m),
                detail=f"方法替换 {old_m} → {new_m}")))

    # ── 算法替换（同节点 algorithm_id 变化；降级必须披露）─────────────
    old_nodes, new_nodes = _dag_map(old), _dag_map(new)
    for node_id in sorted(set(old_nodes) & set(new_nodes)):
        o_alg = str(old_nodes[node_id].get("algorithm_id", ""))
        n_alg = str(new_nodes[node_id].get("algorithm_id", ""))
        if o_alg and n_alg and o_alg != n_alg:
            disclosure = ""
            if not old_nodes[node_id].get("parallel_safe", False):
                disclosure = "非并行安全节点：重算期间不可与上游并行。"
            entries.append(DiffEntry(
                kind="algorithm_substitution",
                path=f"typed_dag.{node_id}.algorithm",
                old=o_alg, new=n_alg, disclosure=disclosure,
                change=WorkflowChange(
                    dimension="algorithm", target_kind="algorithm",
                    target=node_id, detail=f"算法替换 {o_alg} → {n_alg}")))

    # ── 结构变化（节点增删 + 边增删）─────────────────────────────────
    added = sorted(set(new_nodes) - set(old_nodes))
    removed = sorted(set(old_nodes) - set(new_nodes))
    for node_id in added:
        entries.append(DiffEntry(
            kind="dag_structure_change", path=f"typed_dag.{node_id}",
            new=node_id,
            change=WorkflowChange(
                dimension="algorithm", target_kind="algorithm",
                target=node_id, detail="新增节点")))
    # 移除节点/边的 target 投影到**新图中存活的下游节点**（重算消费方）；
    # 无存活下游 → 空串（诚实 no-op：该子树整体消失，无物可重算）。
    old_edges_raw = (old.compiled_form.get("typed_dag") or {}).get("edges") or []

    def _survivor(removed_id: str) -> str:
        for e in old_edges_raw:
            if isinstance(e, dict) and str(e.get("from", "")) == removed_id:
                to_node = str(e.get("to", ""))
                if to_node in new_nodes:
                    return to_node
        return ""

    for node_id in removed:
        entries.append(DiffEntry(
            kind="dag_structure_change", path=f"typed_dag.{node_id}",
            old=node_id,
            change=WorkflowChange(
                dimension="algorithm", target_kind="algorithm",
                target=_survivor(node_id),
                detail="移除节点（下游需复验）")))

    def _edge_set(pkg: WorkflowPackage) -> Dict[str, str]:
        """边集：key = from→to（bounded 形态含端口），value = 目标节点 id
        （剥离端口后缀 —— target 必须是真实 node id）。"""
        edges = (pkg.compiled_form.get("typed_dag") or {}).get("edges") or []
        out: Dict[str, str] = {}
        for e in edges:
            if not isinstance(e, dict):
                continue
            to_full = str(e.get("to", ""))
            to_node = to_full.rsplit(".", 1)[0] if "." in to_full else to_full
            out[f"{e.get('from')}->{e.get('to')}"] = to_node
        return out

    old_edges, new_edges = _edge_set(old), _edge_set(new)
    for key in sorted(set(new_edges) - set(old_edges)):
        entries.append(DiffEntry(
            kind="dag_structure_change", path=f"typed_dag.edges.{key}",
            new=key,
            change=WorkflowChange(
                dimension="algorithm", target_kind="algorithm",
                target=new_edges[key], detail="新增数据流边")))
    for key in sorted(set(old_edges) - set(new_edges)):
        survivor = old_edges[key] if old_edges[key] in new_nodes else ""
        entries.append(DiffEntry(
            kind="dag_structure_change", path=f"typed_dag.edges.{key}",
            old=key,
            change=WorkflowChange(
                dimension="algorithm", target_kind="algorithm",
                target=survivor, detail="移除数据流边")))

    # ── 参数变化（resolved 参数 name→value@provenance 投影）───────────
    old_params, new_params = _param_map_of(old), _param_map_of(new)
    for name in sorted(set(old_params) & set(new_params)):
        if old_params[name] != new_params[name]:
            entries.append(DiffEntry(
                kind="parameter_change", path=f"parameters.{name}",
                old=old_params[name][:64], new=new_params[name][:64],
                change=WorkflowChange(
                    dimension="parameter", target_kind="parameter",
                    target=name, detail=f"参数 {name} 解析值变化")))

    # ── 义务链（完成契约义务披露集变化 → 主输出节点）──────────────────
    old_oc = (old.compiled_form.get("completion_contract") or {})
    new_oc = (new.compiled_form.get("completion_contract") or {})
    old_disc = sorted(str(x) for x in old_oc.get("required_disclosures") or [])
    new_disc = sorted(str(x) for x in new_oc.get("required_disclosures") or [])
    if old_disc != new_disc:
        entries.append(DiffEntry(
            kind="obligation_change",
            path="completion_contract.required_disclosures",
            old=",".join(old_disc)[:64], new=",".join(new_disc)[:64],
            change=WorkflowChange(
                dimension="data", target_kind="output",
                target=_primary_output_of(new),
                detail="义务披露集变化：完成语义变化，需复验")))

    dimensions: List[str] = []
    for e in entries:
        if e.change.dimension not in dimensions:
            dimensions.append(e.change.dimension)
    explanation = (
        "; ".join(f"{e.kind}@{e.path}" for e in entries[:6])
        or "no semantic change"
    )
    return WorkflowDiff(
        compatible=compat.compatible,
        entries=entries,
        recompute_dimensions=dimensions,
        explanation=explanation,
    )
