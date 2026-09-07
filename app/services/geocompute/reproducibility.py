"""可复现执行包与执行级 lineage 投影（ADR-0101 D12，V4 §29/§30）。

- **执行包**：诊断 stale/可重放性的紧凑清单 —— runtime manifest 指纹、
  计划指纹、数据集版本、节点实现版本、参数（有界）、CRS、后端变体、
  确定性声明、物化产物 ref、关键证据。它**不**承诺远端变化源的
  字节级重放 —— 分类为 conditionally reproducible 是诚实披露。
- **lineage 投影**：执行节点通过 ``lineage_inputs`` 连到既有 Artifact
  /DatasetVersion 身份（不建第二 lineage 存储）；投影有界、无载荷、
  按归属域过滤 —— 绝不把内部原始载荷暴露给 LLM 上下文。
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from app.services.geocompute.drift import check_plan_drift
from app.services.geocompute.normalization import canonical_dumps
from app.services.geocompute.plan import EXECUTION_PLAN_VERSION, ExecutionPlan, ExecutionRun

#: 执行包可复现性分类（V4 §30）。
REPRODUCIBLE = "reproducible"
CONDITIONALLY_REPRODUCIBLE = "conditionally_reproducible"
STALE = "stale"
SOURCE_UNAVAILABLE = "source_unavailable"
NON_DETERMINISTIC = "non_deterministic"

_MAX_PARAM_JSON_CHARS = 4096
_MAX_NODES_IN_BUNDLE = 256


def build_execution_bundle(
    plan: ExecutionPlan,
    run: ExecutionRun,
    *,
    runtime_manifest_fingerprint: Optional[str] = None,
    backend_variant: Optional[str] = None,
) -> dict[str, Any]:
    """执行结束后的可复现清单（有界、无载荷、自含诊断事实）。"""
    stored_record = {
        "execution_plan_version": EXECUTION_PLAN_VERSION,
        "plan_id": plan.plan_id,
        "plan_fingerprint": plan.graph_fingerprint(),
        "node_fingerprints": {
            n.node_id: n.semantic_fingerprint() for n in plan.nodes[:_MAX_NODES_IN_BUNDLE]
        },
        "runtime_manifest_fingerprint": runtime_manifest_fingerprint,
    }
    verdict = check_plan_drift(
        stored_record, plan=plan,
        current_runtime_fingerprint=runtime_manifest_fingerprint,
    )

    dataset_versions: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    any_source_unavailable = False
    for node in plan.nodes[:_MAX_NODES_IN_BUNDLE]:
        ev = run.evidence.get(node.node_id)
        try:
            params_json = canonical_dumps(node.parameters)[:_MAX_PARAM_JSON_CHARS]
        except Exception:  # noqa: BLE001 - 参数序列化失败 = 有界占位
            params_json = '"<unserializable>"'
        nodes.append({
            "node_id": node.node_id,
            "category": node.category.value,
            "operation": node.operation,
            "fingerprint": node.semantic_fingerprint(),
            "deterministic": node.deterministic,
            "policy": node.policy.value,
            "crs": node.crs.model_dump() if node.crs else None,
            "dataset_fingerprints": dict(sorted(node.dataset_fingerprints.items())),
            "parameters_json": params_json,
            "status": ev.status if ev else "pending",
            "output_ref": ev.output_ref if ev else None,
            "attempts": ev.attempts if ev else 0,
        })
        for ds_fp in node.dataset_fingerprints.values():
            dataset_versions[ds_fp] = "declared"
        if node.category.value in ("query", "source_scan") and not node.dataset_fingerprints:
            # 外部源节点未声明内容指纹 → 源可用性/内容无法证明。
            any_source_unavailable = any_source_unavailable or (
                ev is not None and ev.status not in ("completed", "reused"))

    # ---- 分类（首个命中即定；显式优先级）----
    if any(n["deterministic"] is False for n in nodes):
        reproducibility = NON_DETERMINISTIC
    elif verdict.state == "stale_runtime":
        reproducibility = STALE
    elif verdict.state == "degraded_plan":
        reproducibility = CONDITIONALLY_REPRODUCIBLE
    elif any_source_unavailable:
        reproducibility = SOURCE_UNAVAILABLE
    else:
        # 所有源都是外部查询类（内容随源漂移）→ 条件可复现（诚实）。
        has_external = any(
            n["category"] in ("query", "source_scan") for n in nodes
        )
        reproducibility = (
            CONDITIONALLY_REPRODUCIBLE if has_external else REPRODUCIBLE
        )

    return {
        "bundle_version": 1,
        "reproducibility": reproducibility,
        "reproducibility_reason": verdict.reason,
        "runtime_manifest_fingerprint": runtime_manifest_fingerprint,
        "plan_fingerprint": stored_record["plan_fingerprint"],
        "plan_id": plan.plan_id,
        "execution_plan_version": 2,
        "run_id": run.run_id,
        "run_status": run.status.value,
        "backend_variant": backend_variant or "in_process",
        "dataset_versions": dataset_versions,
        "nodes": nodes,
        "materialized_refs": [
            ev.output_ref for ev in run.evidence.values() if ev.output_ref
        ],
        "drift": verdict.to_dict(),
    }


def bundle_verdict_block(bundle: dict[str, Any]) -> dict[str, Any]:
    """bundle → 有界判定块（Wave-11 接线，audit 08 §6.2.1）。

    完整 bundle 不落库 —— 判定块携带 bundle 文档的 sha256[:16] 内容摘要
    （跨 run 可比对、可验证）+ 分类/理由/指纹等标量事实；无载荷。执行器
    在 run 终态把它折进终态证据快照的既有 JSON（``run.reproducibility``）
    并经 REST 暴露 —— bundle 从 built-but-orphaned 变为每次执行的事实。
    """
    digest = hashlib.sha256(
        canonical_dumps(bundle).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "classification": bundle.get("reproducibility"),
        "reason": str(bundle.get("reproducibility_reason") or "")[:200] or None,
        "bundle_digest": f"sha256:{digest}",
        "plan_fingerprint": bundle.get("plan_fingerprint"),
        "runtime_manifest_fingerprint": bundle.get("runtime_manifest_fingerprint"),
        "backend_variant": bundle.get("backend_variant"),
    }


def lineage_projection(
    plan: ExecutionPlan,
    run: ExecutionRun,
    *,
    node_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """执行级 lineage 投影（有界、无载荷）。

    源数据集/产物身份 → 查询/变换节点 → 物化结果 的链路视图；节点
    ``lineage_inputs`` 引用既有 ArtifactRef/DatasetVersion 身份 —— 与
    ArtifactLineage / provenance 体系互连而非平行。输出不含几何/栅格
    载荷（LLM/调试安全）。
    """
    out: list[dict[str, Any]] = []
    for node in plan.nodes[:_MAX_NODES_IN_BUNDLE]:
        if node_id is not None and node.node_id != node_id:
            continue
        ev = run.evidence.get(node.node_id)
        entry: dict[str, Any] = {
            "node_id": node.node_id,
            "category": node.category.value,
            "fingerprint": node.semantic_fingerprint(),
            "status": ev.status if ev else "pending",
            "inputs": list(node.inputs),
            "input_lineage": [
                {"ref_id": link.ref_id, "kind": link.kind}
                for link in node.lineage_inputs
            ],
            "dataset_fingerprints": dict(sorted(node.dataset_fingerprints.items())),
            "output_ref": ev.output_ref if ev else None,
            "output_summary": {
                k: v for k, v in (ev.output_summary or {}).items()
                if isinstance(v, (str, int, float, bool, type(None)))
            } if ev else {},
        }
        out.append(entry)
    return out
