"""G10 UPD-03：产物影响图与诚实的更新计划（只读预览，零写入）。

红线：
- **不新建第二执行 DAG**：复用 ``app.services.gis_harness.workflow_v4.recompute``
  的 ``compute_affected_subgraph``（正向闭包、保守正确）与
  ``app.services.data_lifecycle.service.impact_analysis`` 的 artifact 依赖图。
- 方法版本 / 参数 / 数据版本 / CRS / 空间·时间窗口 / 单位 / 分母 / 可用性
  全部参与复用指纹（``compute_reuse_fingerprint``）。
- 标题/颜色改变 → 零 GIS 数值重算；全局分类断点、核密度带宽、网络拓扑、
  分母或行政边界改变 → 扩大依赖域或整项重算。
- 无法证明局部安全（unknown/unverifiable/抽样/截断/全局依赖）→ 保守全量
  重算，绝不靠跳过未知依赖获得漂亮指标。
- proposal fingerprint 绑定输入版本集 + MapSpec revision + 用户锁集合 +
  owner scope，预览后任一漂移都必须重新计算。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.lib.data.fingerprints import canonical_fingerprint, compute_reuse_fingerprint
from app.services.gis_harness.workflow_v4.recompute import (
    RecomputePlan,
    WorkflowChange,
    compute_affected_subgraph,
)
from app.services.workflow_runtime.fingerprints import geo_stack_digest

from .contracts import (
    ChangeEvidence,
    PlanDecision,
    PROPOSAL_FINGERPRINT_PREFIX,
    RefreshPlan,
    SourceDiff,
)


def _node_ids(dag: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for node in dag.get("nodes") or []:
        if isinstance(node, dict) and node.get("node_id"):
            out.append(str(node["node_id"]))
    return out


def _to_workflow_changes(
    diffs: Sequence[SourceDiff], node_set: set[str]
) -> List[WorkflowChange]:
    """差异事实 → 既有 WorkflowChange 词表（不猜测图中不存在的节点）。"""
    changes: List[WorkflowChange] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(dimension: str, target_kind: str, target: str, detail: str) -> None:
        key = (dimension, target_kind, target)
        if key in seen:
            return
        seen.add(key)
        changes.append(WorkflowChange(
            dimension=dimension, target_kind=target_kind,
            target=target[:64], detail=detail[:200],
        ))

    for diff in diffs:
        if diff.change_class == "none" and diff.evidence is ChangeEvidence.EXACT:
            _add("style", "style", "", f"{diff.logical_id}: no change")
            continue
        for target in diff.seed_targets:
            if target == "recipe":
                _add("data", "recipe", "", f"{diff.logical_id}: global dependency / scope change")
            elif target in node_set:
                _add("data", "node", target, f"{diff.logical_id}: {diff.reason[:120]}")
            elif target.startswith("data:"):
                _add("data", "data_role", target[5:],
                     f"{diff.logical_id}: data version change")
            else:
                # 图中无对应节点：仍记为 data 变更，compute_affected_subgraph
                # 会给出 "no-op" explanation（诚实披露，不静默吞掉）。
                _add("data", "data_role", target, f"{diff.logical_id}: unmapped target")
        if diff.fields_unit_changed or diff.fields_type_changed:
            _add("parameter", "parameter", "units",
                 f"{diff.logical_id}: unit/type change {diff.fields_unit_changed}")
        if diff.denominator_changed:
            _add("parameter", "parameter", "denominator",
                 f"{diff.logical_id}: denominator changed")
        if diff.method_version_changed:
            _add("algorithm", "recipe", "", f"{diff.logical_id}: method version changed")
    return changes


def build_refresh_plan(
    *,
    diffs: Sequence[SourceDiff],
    dag: Dict[str, Any],
    style_only: bool = False,
    artifact_dependents: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    source_versions: Optional[Dict[str, str]] = None,
    mapspec_revision: int = 0,
    user_locks: Optional[List[str]] = None,
    owner_scope: str = "",
) -> RefreshPlan:
    """只读影响预览 → ``RefreshPlan``（含 proposal fingerprint）。

    ``dag`` 为 bounded typed DAG（``analysis_graph`` / workflow package 的
    ``to_bounded_dict()`` 形态），``artifact_dependents`` 为既有
    ``impact_analysis`` 的下游产物裁决（不写状态）。
    """
    nodes = _node_ids(dag)
    node_set = set(nodes)
    warnings: List[str] = []

    # ── style-only：零 GIS 数值重算（R1-P1-2：客户端不可借此跳过真实差异） ──
    real_changes = [d for d in diffs if d.change_class != "none"]
    effective_style_only = bool(style_only) and not real_changes
    if style_only and real_changes:
        warnings.append(
            "style_only request ignored: real data/schema/CRS changes detected; "
            "client-supplied style_only cannot suppress recomputation"
        )
    if effective_style_only:
        decisions = {n: PlanDecision.REUSE.value for n in nodes}
        return RefreshPlan(
            recompute=[], reuse=sorted(nodes), decisions=decisions,
            changed_dimensions=["style"],
            explanations=["style-only change: presentation only, GIS science subgraph untouched"],
            conservative=False, style_only=True, warnings=warnings[:16],
            style_only_reason="only title/color/presentation edits; all source diffs "
                              "verified as class=none",
            estimated_budget={"recompute_nodes": 0, "reuse_nodes": len(nodes),
                              "estimated_tool_calls": 0},
            proposal_fingerprint=PROPOSAL_FINGERPRINT_PREFIX + canonical_fingerprint({
                "versions": dict(sorted((source_versions or {}).items())),
                "revision": int(mapspec_revision),
                "locks": sorted(set(user_locks or [])),
                "owner": owner_scope,
                "style_only": True,
                "diffs": [
                    {"id": d.logical_id, "class": d.change_class,
                     "evidence": d.evidence.value} for d in diffs
                ],
            }),
        )

    changes = _to_workflow_changes(diffs, node_set)
    affected: RecomputePlan = compute_affected_subgraph(dag, changes)  # type: ignore[arg-type]

    conservative_reasons: List[str] = []
    for d in diffs:
        if d.evidence in (ChangeEvidence.UNKNOWN, ChangeEvidence.UNVERIFIED):
            conservative_reasons.append(f"{d.logical_id}: evidence={d.evidence.value}")
        if d.expanded_dependencies:
            conservative_reasons.append(
                f"{d.logical_id}: global dependencies {','.join(d.expanded_dependencies)}")
        if d.sampled or d.truncated:
            conservative_reasons.append(
                f"{d.logical_id}: sampled/truncated payload ({d.alignment})")
        if d.alignment not in ("stable_id",) and d.change_class != "none":
            conservative_reasons.append(f"{d.logical_id}: alignment={d.alignment}")
        if d.crs_changed or d.bbox_changed or d.temporal_changed:
            conservative_reasons.append(f"{d.logical_id}: CRS/coverage change")
    conservative = bool(conservative_reasons)

    if conservative:
        recompute = sorted(nodes)
        reuse: List[str] = []
        warnings.append(
            "conservative full recompute: " + "; ".join(conservative_reasons[:6])
        )
    else:
        recompute = list(affected.recompute)
        reuse = list(affected.reuse)

    decisions: Dict[str, str] = {}
    for n in nodes:
        decisions[n] = (PlanDecision.RECOMPUTE.value if n in recompute
                        else PlanDecision.REUSE.value)
    # 复用裁决由既有 impact_analysis 的下游产物补充（rebind / conflict /
    # unverifiable），不覆盖子图裁决。
    conflicts: List[str] = []
    for src, deps in (artifact_dependents or {}).items():
        for dep in deps:
            aid = str(dep.get("artifact_id") or "")
            action = str(dep.get("required_action") or "")
            if action in ("invalid", "recompute"):
                decisions.setdefault(aid, PlanDecision.REBIND.value if action == "invalid"
                                     else PlanDecision.RECOMPUTE.value)
            if str(dep.get("status") or "") in ("failed", "conflict"):
                conflicts.append(f"{src} -> {aid}: {dep.get('status')}")

    changed_dimensions = list(dict.fromkeys(
        [c.dimension for c in changes] + (affected.changed_dimensions or [])
    ))
    explanations = list(affected.explanations or [])
    if conservative:
        explanations.append("conservative: cannot prove partial recompute safe")

    versions = dict(sorted((source_versions or {}).items()))
    reuse_fp = compute_reuse_fingerprint(
        operation="project_data_refresh",
        operation_version="g10-v1",
        input_fingerprints={
            d.logical_id: f"{d.change_class}:{d.evidence.value}:{d.reason}"
            for d in diffs
        } or {"_empty": "none"},
        normalized_args={"recompute": sorted(recompute), "conservative": conservative},
        crs=";".join(sorted({d.logical_id for d in diffs if d.crs_changed})),
        runtime_semantic_version=geo_stack_digest(),
    )
    proposal_fp = PROPOSAL_FINGERPRINT_PREFIX + canonical_fingerprint({
        "versions": versions,
        "revision": int(mapspec_revision),
        "locks": sorted(set(user_locks or [])),
        "owner": owner_scope,
        "style_only": False,
        "diffs": [
            {"id": d.logical_id, "class": d.change_class, "evidence": d.evidence.value,
             "counts": d.exact_counts.model_dump() if d.exact_counts else None,
             "seeds": d.seed_targets}
            for d in diffs
        ],
        "env": geo_stack_digest(),
        "dag_nodes": sorted(nodes),
    })

    return RefreshPlan(
        recompute=sorted(recompute)[:64],
        reuse=sorted(reuse)[:64],
        decisions=decisions,
        changed_dimensions=changed_dimensions[:8],
        explanations=[str(x)[:200] for x in explanations[:8]],
        warnings=warnings[:16],
        conservative=conservative,
        style_only=False,
        preserved_user_edits=sorted(set(user_locks or [])),
        conflicts=conflicts[:32],
        estimated_budget={
            "recompute_nodes": len(recompute),
            "reuse_nodes": len(reuse),
            "estimated_tool_calls": len(recompute),
            "conservative": conservative,
        },
        reuse_fingerprint=reuse_fp,
        proposal_fingerprint=proposal_fp,
    )


__all__ = ["build_refresh_plan", "_to_workflow_changes"]
