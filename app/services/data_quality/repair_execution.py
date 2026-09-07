"""修复执行缝 —— 显式执行 → 新 ref + 有界 repair_evidence（绝不静默覆写）。

审计 08 §4.2/§5.1（"new-revision output, no silent overwrite" PARTIAL /
"tracked semantic repairs" MISSING / "repair evidence into lineage" PARTIAL）：
修复此前只返回内存中的新 FC + 一次性日志计数 —— 没有新引用登记、没有
操作级证据落地。本模块把既有 ``SpatialRepairPipeline`` 的执行结果接线为：

    (a) 修复输出经 session store 注册为**新 ref**（源载荷永不被覆写），
        ledger 登记 producer_tool + inputs=[source_ref]（replaces 型血缘，
        dispatch seam 同款登记模式，见 tool_dispatch_service P1/ADR-0082）；
    (b) per-op 有界证据 + 内容摘要落为 ``repair_evidence``，经调用方
        （工具 / REST）写入既有血缘边（``record_lineage``）——
        纯摘要与计数，绝不落要素载荷。

红线：执行只经显式调用（工具 / REST）；执行失败不静默 —— 登记失败
如实披露（``ref_registration_error``），修复产物本身不受影响。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.data_quality.repair_plan import RepairPlan

logger = logging.getLogger(__name__)

#: 证据键上限（有界投影；与 artifact_registry ≤16 inputs / ≤24 metadata 同约定）。
_MAX_EVIDENCE_OPS = 16
_MAX_ISSUE_CODES = 16
_MAX_LOG_ENTRIES = 200
_MAX_LOG_ENTRY_CHARS = 300


def _count_features(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    features = payload.get("features")
    if isinstance(features, list):
        return len(features)
    if payload.get("type") == "Feature":
        return 1
    return 0


def _declared_crs(payload: Any) -> Optional[str]:
    """修复后载荷的 CRS 声明（诚实提取；无声明 → None，绝不虚构 4326）。"""
    if not isinstance(payload, dict):
        return None
    crs = payload.get("crs")
    if isinstance(crs, dict):
        props = crs.get("properties") or {}
        name = props.get("name") or props.get("code")
        return str(name)[:100] if name else None
    return None


def build_repair_evidence(
    *,
    ops_evidence: List[Dict[str, Any]],
    issue_codes: Optional[List[str]],
    feature_count_before: int,
    feature_count_after: int,
    content_digest_before: str,
    content_digest_after: str,
    plan: Optional[RepairPlan] = None,
    output_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """执行事实 → 有界、仅摘要的 ``repair_evidence``（血缘边载荷）。

    键集封闭：plan_id / ops / 码 / 计数 / 内容摘要 —— 绝不携带要素或
    几何载荷（血缘边是链接 + 有界事实，不是第二个产物库）。
    """
    codes = [str(c)[:64] for c in (issue_codes or [])][:_MAX_ISSUE_CODES]
    evidence: Dict[str, Any] = {
        "plan_id": (plan.plan_id if plan else "")[:64],
        "ops_applied": [
            str(e.get("op", ""))[:32] for e in (ops_evidence or [])[:_MAX_EVIDENCE_OPS]
        ],
        "op_evidence": [
            {
                "op": str(e.get("op", ""))[:32],
                "features_affected": int(e.get("features_affected", 0)),
                "failed_count": int(e.get("failed_count", 0)),
            }
            for e in (ops_evidence or [])[:_MAX_EVIDENCE_OPS]
        ],
        "issue_codes_addressed": codes,
        "feature_count_before": int(feature_count_before),
        "feature_count_after": int(feature_count_after),
        "content_digest_before": str(content_digest_before)[:64],
        "content_digest_after": str(content_digest_after)[:64],
    }
    if output_ref:
        evidence["output_ref"] = str(output_ref)[:80]
    return evidence


async def execute_repair(
    *,
    geojson: Dict[str, Any],
    operations: Optional[List[str]] = None,
    source_crs: str = "EPSG:4326",
    target_crs: str = "EPSG:4326",
    tolerance: float = 1e-5,
    session_id: Optional[str] = None,
    source_ref: Optional[str] = None,
    issue_codes: Optional[List[str]] = None,
    plan: Optional[RepairPlan] = None,
) -> Dict[str, Any]:
    """显式修复执行：修复（非破坏）→ 新 ref 登记 → 有界证据组装。

    返回 dict（调用方决定对外投影与裁剪）。键：
    ``status / operations_applied / ops_evidence / logs / logs_count /
    feature_count(_before) / content_digest_before/after / output_crs /
    repaired_geojson（全量载荷 —— 调用方必须 trim 后才可入响应）/
    repaired_ref / ref_registration_error / repair_evidence``。
    """
    from app.services.spatial_repair_pipeline import SpatialRepairPipeline

    feature_count_before = _count_features(geojson)
    digest_before = await asyncio.to_thread(_compute_digest, geojson)

    repaired, logs, ops_evidence = await asyncio.to_thread(
        SpatialRepairPipeline.repair_dataset_detailed,
        geojson,
        ops=operations,
        tolerance=tolerance,
        source_crs=source_crs,
        target_crs=target_crs,
    )
    feature_count_after = _count_features(repaired)
    digest_after = await asyncio.to_thread(_compute_digest, repaired)

    evidence = build_repair_evidence(
        ops_evidence=ops_evidence,
        issue_codes=issue_codes,
        feature_count_before=feature_count_before,
        feature_count_after=feature_count_after,
        content_digest_before=digest_before,
        content_digest_after=digest_after,
        plan=plan,
    )

    result: Dict[str, Any] = {
        "status": "success",
        "operations_applied": list(evidence["ops_applied"]),
        "ops_evidence": list(evidence["op_evidence"]),
        "logs": [str(line)[:_MAX_LOG_ENTRY_CHARS] for line in logs[:_MAX_LOG_ENTRIES]],
        "logs_count": len(logs),
        "feature_count": feature_count_after,
        "feature_count_before": feature_count_before,
        "content_digest_before": digest_before,
        "content_digest_after": digest_after,
        "output_crs": _declared_crs(repaired),
        "repaired_geojson": repaired,
        "repaired_ref": None,
        "ref_registration_error": None,
        "repair_evidence": evidence,
    }

    # (a) 新 ref（绝不覆写源载荷）+ ledger 登记（dispatch seam 同款模式）。
    if session_id:
        try:
            from app.services.session_data import session_data_manager

            repaired_ref = await session_data_manager.store(
                session_id, repaired, prefix="repaired"
            )
            result["repaired_ref"] = repaired_ref
            evidence["output_ref"] = str(repaired_ref)[:80]
            from app.services.artifact_registry import register_artifact

            await register_artifact(
                session_id,
                artifact_id=repaired_ref,
                artifact_type="feature_collection",
                producer_tool="repair_spatial_dataset",
                # replaces 型血缘：诚实 inputs 边 —— 无源 ref 时绝不虚构。
                inputs=[source_ref] if source_ref else None,
                metadata={
                    "repair_plan_id": evidence["plan_id"],
                    "repair_ops": list(evidence["ops_applied"]),
                    "replaces": str(source_ref)[:80] if source_ref else "",
                    "content_sha256": digest_after,
                    "issue_codes_addressed": list(evidence["issue_codes_addressed"]),
                },
            )
        except Exception as exc:  # noqa: BLE001 — 登记失败不影响产物，如实披露
            logger.warning(
                "[RepairExecution] repaired-ref registration failed session=%s: %s",
                session_id, exc,
            )
            result["ref_registration_error"] = str(exc)[:200]
    return result


def _compute_digest(data: Any) -> str:
    """线程内摘要（canonical JSON sha256，与 ingest dedup 键同源）。"""
    import hashlib
    import json

    try:
        canonical = json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    except (TypeError, ValueError):
        canonical = repr(data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def persist_repair_lineage(
    db: Any,
    project_id: str,
    *,
    repair_evidence: Dict[str, Any],
    content_fingerprint: str,
    name: str = "repaired_dataset",
    crs: Optional[str] = None,
    storage_ref: Optional[str] = None,
    source_dataset_id: Optional[str] = None,
    source_dataset_fingerprint: Optional[str] = None,
) -> str:
    """修复输出 → 项目侧 Artifact 行 + 根血缘边（携带 repair_evidence）。

    根边语义（INV-LIN4）：修复产物不虚构父 artifact —— 输入数据集经
    ``source_dataset_id``/``source_dataset_fingerprint`` 进入血缘；执行
    证据经 ``repair_evidence`` 落在同一条边上。提交语义：本函数提交
    （调用方无每步事务边界）。
    """
    from app.models.project import Artifact
    from app.services.lineage_service import LineageService

    artifact_id = f"art_{uuid.uuid4().hex[:16]}"
    db.add(
        Artifact(
            id=artifact_id,
            project_id=project_id,
            name=str(name)[:255],
            artifact_type="feature_collection",
            format="geojson",
            # 诚实 CRS：修复后载荷的真实声明；无声明 → NULL（INV-ART1）。
            crs=crs,
            storage_ref=str(storage_ref or content_fingerprint)[:500],
            content_fingerprint=str(content_fingerprint)[:64],
            metadata_json={"repair_plan_id": str(repair_evidence.get("plan_id", ""))[:64]},
            created_at=datetime.now(timezone.utc),
        )
    )
    db.flush()
    LineageService.record_lineage(
        db=db,
        artifact_id=artifact_id,
        producing_tool="repair_spatial_dataset",
        parent_artifact_ids=None,
        source_dataset_id=source_dataset_id,
        source_dataset_fingerprint=source_dataset_fingerprint,
        content_fingerprint=str(content_fingerprint)[:64],
        repair_evidence=repair_evidence,
        commit=True,
    )
    return artifact_id


__all__ = [
    "build_repair_evidence",
    "execute_repair",
    "persist_repair_lineage",
]
