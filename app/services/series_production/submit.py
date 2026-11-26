"""Series Production 提交与执行 —— durable job 面（WP5 接线）。

提交幂等键 = 冻结计划 digest：同一冻结计划的重复提交（双击/SSE 重连）
复用同一 job 行。执行体逐成员 checkpoint + 取消看门狗（durable_job
上下文，atlas 同模式）；manifest 落 exports 并回填 job result。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from app.services.series_production.contract import (
    SeriesFreezePlan,
    validate_freeze_plan_payload,
)

logger = logging.getLogger(__name__)

SERIES_TASK_TYPE = "series_production"


def freeze_plan_from_series_payload(
    freeze_payload: Dict[str, Any],
) -> SeriesFreezePlan:
    """WP3 freeze 输出 → SeriesFreezePlan（桥接；字段映射 + 有界化）。

    WP3 计划形状：``{plan_version, series_id, frozen_at, structural_digest,
    spec_pin:{design_spec_id, version}, output_profile, members:[...],
    unresolvable_member_ids}``。``frozen_at``/``note`` 是 wall-clock/描述
    字段 —— 不进我的 frozen 契约（digest 确定性），如实丢弃。
    """
    spec_pin = freeze_payload.get("spec_pin") or {}
    output = freeze_payload.get("output_profile") or {}
    members = []
    for member in freeze_payload.get("members") or []:
        if not isinstance(member, dict):
            continue
        # freeze 值形状：{kind, value|ref_digest|content_digest|type}
        data_fps: Dict[str, str] = {}
        for k, v in (member.get("data_fingerprints") or {}).items():
            if isinstance(v, dict):
                digest = (v.get("value") or v.get("ref_digest")
                          or v.get("content_digest") or v.get("type") or "")
                data_fps[str(k)[:80]] = str(digest)[:80]
            else:
                data_fps[str(k)[:80]] = str(v)[:80]
        members.append({
            "member_id": str(member.get("member_id") or "")[:40],
            "title": str(member.get("title") or "")[:160],
            "session_id": str(member.get("session_id") or ""),
            "mapspec_fingerprint": str(member.get("mapspec_fingerprint") or ""),
            "print_layout_digest": member.get("print_layout_digest"),
            "data_fingerprints": data_fps,
            "design_spec_id": member.get("design_spec_id") or spec_pin.get("design_spec_id"),
            "design_spec_version": member.get("design_spec_version")
            or spec_pin.get("version"),
            "role": str(member.get("role") or "primary")[:20],
        })
    return validate_freeze_plan_payload({
        "spec_version": "series_freeze_plan.v1",
        "series_id": str(freeze_payload.get("series_id") or "")[:36],
        "name": str(freeze_payload.get("series_name")
                    or freeze_payload.get("name") or "")[:255],
        "design_spec_id": spec_pin.get("design_spec_id"),
        "design_spec_version": spec_pin.get("version"),
        "output": output,
        "members": members,
        "simulated_data": bool(freeze_payload.get("simulated_data")),
    })


def job_params(plan: SeriesFreezePlan) -> Dict[str, Any]:
    """幂等键与落库参数投影（无载荷数据）。"""
    return {
        "task_type": SERIES_TASK_TYPE,
        "series_id": plan.series_id,
        "name": plan.name,
        "freeze_plan_digest": plan.plan_digest(),
        "member_count": len(plan.members),
        "member_ids": [m.member_id for m in plan.members],
        "design_spec_id": plan.design_spec_id,
        "design_spec_version": plan.design_spec_version,
        "output": plan.output,
    }


def persist_manifest(plan: SeriesFreezePlan, manifest: Dict[str, Any]) -> str:
    """manifest → exports/series_jobs/manifests/<digest>.json；返回相对路径。"""
    from app.services.export_paths import exports_root

    digest = str(manifest.get("manifest_digest") or "").replace(":", "_")
    safe = "".join(c for c in digest if c.isalnum() or c in "_-")[:80]
    rel = f"series_jobs/manifests/{safe or 'manifest'}.json"
    path = exports_root() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return rel


async def execute_series_production_job(job_id: int, job: Any = None) -> Dict[str, Any]:
    """durable job 执行体：载参 → 重建计划 → 生产 → manifest 落盘。

    单成员失败不中断；job 终态语义：任一成员 complete → completed，
    其余失败/阻断由 manifest 如实披露（不伪装全成功）。
    """
    from app.services.series_production.executor import execute_series_production
    from app.services.jobs.store import DurableJobStore
    from app.tools._utils import db_session

    with db_session() as db:
        record = DurableJobStore.get_sync(db, int(job_id))
        params = record.parameters if (record and isinstance(record.parameters, dict)) else {}
    freeze_payload = params.get("freeze_plan") if isinstance(params, dict) else None
    if not isinstance(freeze_payload, dict):
        return {"status": "failed", "code": "FREEZE_PLAN_MISSING",
                "message": "job params 缺少 freeze_plan"}
    plan = freeze_plan_from_series_payload(freeze_payload)
    if job is not None and hasattr(job, "progress"):
        job.progress(5, f"系列生产开始：{len(plan.members)} 成员")

    outcome = await execute_series_production(plan)
    if job is not None and hasattr(job, "progress"):
        job.progress(95, "成员生产完成，写 manifest")

    manifest_rel = persist_manifest(plan, outcome["manifest"])
    summary = outcome["summary"]
    # 终态语义：complete + cached_reused 都代表「产物在且经 QA 验证」
    #（缓存加载面复核 checksum）；全成员失败/阻断才是 failed。
    produced = summary.get("complete", 0) + summary.get("cached_reused", 0)
    return {
        "status": "completed" if produced > 0 else "failed",
        "code": None if produced > 0 else "ALL_MEMBERS_INCOMPLETE",
        "summary": summary,
        "manifest_file": manifest_rel,
        "manifest_digest": outcome["manifest"].get("manifest_digest"),
        "results": [r.model_dump() for r in outcome["results"]],
        "freeze_plan_digest": plan.plan_digest(),
    }


__all__ = [
    "SERIES_TASK_TYPE",
    "freeze_plan_from_series_payload",
    "job_params",
    "persist_manifest",
    "execute_series_production_job",
]
