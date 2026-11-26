"""Series Production manifest —— series_publication_manifest.v1（WP5）。

确定性（无 wall-clock）：同冻结输入 + 同产物字节 ⇒ 同 manifest。
结构对齐 atlas ``atlas_publication_manifest.v1`` 纪律：inputs 指纹锚定
+ 逐成员产物 digest + QA 摘要 + 披露面；成员数有界（≤50）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.services.series_production.contract import (
    MemberResult,
    SeriesFreezePlan,
    SERIES_MANIFEST_VERSION,
)
from app.services.design_specs.contract import canonical_json

#: manifest 成员上限（与 freeze plan 预算一致）。
_MANIFEST_MEMBER_LIMIT = 50


def build_series_manifest(
    plan: SeriesFreezePlan,
    results: List[MemberResult],
) -> Dict[str, Any]:
    members: List[Dict[str, Any]] = []
    for result in results[:_MANIFEST_MEMBER_LIMIT]:
        members.append({
            "member_id": result.member_id,
            "status": result.status,
            "code": result.code,
            "message": result.message[:280],
            "artifacts": [a.model_dump() for a in result.artifacts],
            "qa": result.qa.model_dump() if result.qa else None,
            "member_fingerprint": result.member_fingerprint,
            "cache_key": result.cache_key,
        })

    complete = sum(1 for r in results if r.status in ("complete", "cached_reused"))
    failed = sum(1 for r in results if r.status == "failed")
    blocked = sum(1 for r in results if r.status == "blocked")
    skipped = sum(1 for r in results if r.status == "skipped")

    disclosures: List[Dict[str, str]] = []
    if plan.simulated_data:
        disclosures.append({
            "code": "SIMULATED_DATA",
            "message": "本系列使用合成/模拟数据（simulated_data=true，非实测）",
        })
    if blocked:
        disclosures.append({
            "code": "MEMBERS_BLOCKED",
            "message": f"{blocked} 个成员被阻断（会话缺失/指纹漂移）——未产出，不标完整",
        })
    if failed:
        disclosures.append({
            "code": "MEMBERS_FAILED",
            "message": f"{failed} 个成员生产失败——失败原因见逐成员回执",
        })

    manifest = {
        "schema_version": SERIES_MANIFEST_VERSION,
        "series": {
            "id": plan.series_id,
            "name": plan.name,
            "member_count": len(plan.members),
        },
        "design_spec": {
            "id": plan.design_spec_id,
            "version": plan.design_spec_version,
            "structural_digest": plan.design_spec_digest,
        },
        "inputs": {
            "freeze_plan_version": plan.spec_version,
            "freeze_plan_digest": plan.plan_digest(),
            "output": plan.output,
        },
        "members": members,
        "summary": {
            "total": len(results),
            "complete": complete,
            "cached_reused": sum(
                1 for r in results if r.status == "cached_reused"),
            "failed": failed,
            "blocked": blocked,
            "skipped": skipped,
            "all_complete": complete == len(results) and len(results) > 0,
        },
        "disclosures": disclosures,
    }
    manifest["manifest_digest"] = "smanifest-sha256:" + _sha256(
        canonical_json({k: v for k, v in manifest.items()})
    )
    return manifest


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["build_series_manifest"]
