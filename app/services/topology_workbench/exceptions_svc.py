"""Topology Workbench —— 合法例外（WP09）与复验（WP11）服务。

例外纪律（任务书 WP09）：
- 例外是**用户决定**（REST/UI 面；Agent 工具不得代用户接受 —— 无状态权限的
  模型输出不能充当用户例外决定）；
- 理由必填、锚点固化（规则 digest + 源指纹 + locator 身份 + witness digest）；
- 撤销 → finding 回到 open；
- stale 判定在**运行执行**时（runner）：规则或源变化 → 例外 stale，绝不按
  bbox/文本相似自动延续；
- 接受例外只改处理状态，不改检测事实、不抹严重度，也不关 G14 硬门。

复验纪律（任务书 WP10/WP11）：
- repair receipt 绑定输入/输出 revision + 规则 digest + 受影响问题；
- 复验 = 同规则集 + 最新可解析源重跑（显式 allow refresh）→ finding-key diff；
- resolved 判定：新运行该规则 computation=full 且 finding_key 消失 —— 只有
  证据确实消失才 resolved；新问题自然出现在新运行中。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.services.topology_workbench.service import (
    WorkbenchError,
    _get_owned_run,
)

logger = logging.getLogger(__name__)


# ─── 例外 ─────────────────────────────────────────────────────────────


def accept_exception(
    finding_id: str, *, reason: str, actor_user_id: Optional[str],
    is_admin: bool = False,
) -> Dict[str, Any]:
    """把 finding 标记为 accepted exception（用户决定；理由必填 1..2000）。"""
    from app.core.database import SessionLocal
    from app.models.topology_workbench import (
        TopologyException,
        TopologyFinding,
    )

    reason = str(reason or "").strip()
    if not (1 <= len(reason) <= 2000):
        raise WorkbenchError(
            "exception_invalid_reason", "例外理由必填（1..2000 字符）"
        )
    with SessionLocal() as db:
        finding = db.get(TopologyFinding, finding_id)
        if finding is None:
            raise WorkbenchError("finding_not_found", "问题不存在", status=404)
        run = _get_owned_run(db, finding.run_id, actor_user_id, is_admin)
        if finding.status == "resolved":
            raise WorkbenchError(
                "exception_stale", "该问题已被复验判定 resolved —— 无需例外"
            )
        # 已有 active 例外 → 幂等返回
        existing = db.execute(
            select(TopologyException).where(
                TopologyException.finding_key == finding.finding_key,
                TopologyException.ruleset_id == run.ruleset_id,
                TopologyException.status.in_(("active", "stale")),
            )
        ).scalars().first()
        if existing is not None and existing.status == "active":
            return _exception_view(existing, finding_status=finding.status)

        anchor = {
            "ruleset_digest": run.ruleset_digest,
            "source_fingerprints": {
                role: ev.get("fingerprint")
                for role, ev in (run.bindings or {}).items()
            },
            "source_revisions": {
                role: ev.get("content_revision")
                for role, ev in (run.bindings or {}).items()
            },
            "locator_ids": sorted(
                f"{loc.get('role')}:{loc.get('identity', {}).get('kind')}:"
                f"{loc.get('identity', {}).get('value')}"
                for loc in  (finding.locators or [])
            ),
            "witness_digest": finding.witness_digest,
            "metric": finding.metric,
        }
        exc = TopologyException(
            ruleset_id=run.ruleset_id, rule_id=finding.rule_id,
            finding_key=finding.finding_key, anchor=anchor, reason=reason,
            status="active", created_by=actor_user_id,
        )
        db.add(exc)
        db.flush()
        finding.status = "accepted_exception"
        finding.exception_id = exc.id
        db.commit()
        return _exception_view(exc, finding_status=finding.status)



def _ruleset_owned(db, ruleset, actor_user_id: Optional[str]) -> bool:
    """例外面的规则集归属：created_by 或会话属主兜底（与 service._owned 对齐）。"""
    if ruleset.created_by == actor_user_id:
        return True
    if ruleset.session_id:
        from app.models.db_model import Conversation

        conv = db.get(Conversation, str(ruleset.session_id)[:255])
        if conv is not None and str(conv.user_id or "") == str(actor_user_id):
            return True
    return False


def revoke_exception(
    exception_id: str, *, actor_user_id: Optional[str], is_admin: bool = False
) -> Dict[str, Any]:
    """撤销例外 → 关联 finding 回到 open（检测事实不变）。"""
    from app.core.database import SessionLocal
    from app.models.topology_workbench import (
        TopologyException,
        TopologyFinding,
        TopologyRuleset,
    )

    with SessionLocal() as db:
        exc = db.get(TopologyException, exception_id)
        if exc is None:
            raise WorkbenchError("finding_not_found", "例外不存在", status=404)
        ruleset = db.get(TopologyRuleset, exc.ruleset_id)
        if ruleset is None or (actor_user_id and not is_admin and not _ruleset_owned(
                db, ruleset, actor_user_id)):
            raise WorkbenchError("not_owned", "例外不存在或无权访问", status=404)
        if exc.status == "revoked":
            return _exception_view(exc)
        exc.status = "revoked"
        exc.revoked_at = datetime.now(timezone.utc)
        exc.revoked_by = actor_user_id
        # 关联 open 化：本规则集下同 finding_key 的 accepted finding 回 open
        findings = db.execute(
            select(TopologyFinding).where(
                TopologyFinding.finding_key == exc.finding_key,
                TopologyFinding.status == "accepted_exception",
            )
        ).scalars().all()
        for f in findings:
            f.status = "open"
            f.exception_id = None
        db.commit()
        return _exception_view(exc)


def list_exceptions(
    *, ruleset_id: Optional[str] = None, project_id: Optional[str] = None,
    actor_user_id: Optional[str], is_admin: bool = False,
    limit: int = 100, offset: int = 0,
) -> List[Dict[str, Any]]:
    from app.core.database import SessionLocal
    from app.models.topology_workbench import TopologyException, TopologyRuleset

    with SessionLocal() as db:
        stmt = select(TopologyException).order_by(TopologyException.created_at.desc())
        if ruleset_id:
            stmt = stmt.where(TopologyException.ruleset_id == ruleset_id)
        if actor_user_id and not is_admin:
            # 归属 = created_by 或（agent 会话内创建时）会话属主兜底
            from sqlalchemy import or_
            from app.models.db_model import Conversation

            owned_session_ids = [
                row[0] for row in db.execute(
                    select(Conversation.id).where(Conversation.user_id == actor_user_id)
                ).all()
            ]
            cond = TopologyRuleset.created_by == actor_user_id
            if owned_session_ids:
                cond = or_(cond, TopologyRuleset.session_id.in_(owned_session_ids))
            stmt = stmt.join(
                TopologyRuleset,
                TopologyException.ruleset_id == TopologyRuleset.id,
            ).where(cond)
        rows = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
        return [_exception_view(r) for r in rows]


def _exception_view(exc: Any, *, finding_status: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": exc.id, "ruleset_id": exc.ruleset_id, "rule_id": exc.rule_id,
        "finding_key": exc.finding_key, "reason": exc.reason,
        "status": exc.status, "created_by": exc.created_by,
        "created_at": exc.created_at.isoformat() if exc.created_at else None,
        "revoked_at": exc.revoked_at.isoformat() if exc.revoked_at else None,
        "anchor": exc.anchor, "finding_status": finding_status,
    }


# ─── 复验 ─────────────────────────────────────────────────────────────


async def recheck_run(
    run_id: str, *, actor_user_id: Optional[str], actor_owner_token: Optional[str],
    is_admin: bool = False, repair_receipt: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """复验：同规则集 + 最新源重跑 → diff → resolved 证据判定。

    repair_receipt（可选）：{"plan_id", "ref_id", "revision_before",
    "revision_after"} —— 记录在新运行证据中（WP11：receipt 绑定输入/输出
    revision + 规则 digest + 受影响问题）。
    """
    from app.core.database import SessionLocal
    from app.models.topology_workbench import (
        TopologyFinding,
        TopologyRun,
    )
    from app.services.topology_workbench.service import submit_run

    # 旧运行完整视图（bindings 带原始绑定）
    with SessionLocal() as db:
        old = _get_owned_run(db, run_id, actor_user_id, is_admin)
        ruleset_id = old.ruleset_id
        bindings_raw = {
            role: ev.get("binding") or {}
            for role, ev in (old.bindings or {}).items()
        }
        old_rules_digest = old.ruleset_digest
        old_session_id = old.session_id
        old_project_id = old.project_id

    # 新运行：同规则集当前版本 + 相同绑定（允许源刷新 —— 复验语义就是看最新）
    submitted = await submit_run(
        ruleset_id=ruleset_id, bindings=bindings_raw,
        actor_user_id=actor_user_id, actor_owner_token=actor_owner_token,
        session_id=old_session_id, project_id=old_project_id, submit_job=False,
    )
    new_run_id = submitted["run_id"]

    from app.services.topology_workbench.runner import execute_topology_run

    exec_result = await execute_topology_run(
        new_run_id, allow_source_refresh=True, recheck_of_run_id=run_id,
    )
    if exec_result.get("status") != "completed":
        return {
            "recheck_run_id": new_run_id, "original_run_id": run_id,
            "status": exec_result.get("status"),
            "message": "复验运行未完成 —— 原问题状态不变",
            "detail": exec_result,
        }

    with SessionLocal() as db:
        new_run = db.get(TopologyRun, new_run_id)
        # 规则集在复验间被编辑 → digest 不同：这不是复验而是新检查，如实披露
        digest_changed = new_run.ruleset_digest != old_rules_digest
        new_keys_by_rule: Dict[str, set] = {}
        for f in db.execute(
            select(TopologyFinding).where(TopologyFinding.run_id == new_run_id)
        ).scalars():
            new_keys_by_rule.setdefault(f.rule_id, set()).add(f.finding_key)

        # 新运行逐规则计算完整性（resolved 需要该规则 computation=full）
        computation_by_rule = {
            (rr or {}).get("rule_id"): (rr or {}).get("computation")
            for rr in (new_run.rule_results or [])
        }

        old_findings = db.execute(
            select(TopologyFinding).where(TopologyFinding.run_id == run_id)
        ).scalars().all()
        resolved_keys: List[str] = []
        persisted_keys: List[str] = []
        for f in old_findings:
            if f.status in ("resolved",):
                resolved_keys.append(f.finding_key)
                continue
            still_present = f.finding_key in new_keys_by_rule.get(f.rule_id, set())
            if still_present:
                persisted_keys.append(f.finding_key)
                continue
            comp = computation_by_rule.get(f.rule_id)
            if comp == "full":
                f.status = "resolved"
                f.resolved_by_run_id = new_run_id
                resolved_keys.append(f.finding_key)
            else:
                # 新运行该规则计算不完整 —— 无法证明证据消失，不判 resolved
                persisted_keys.append(f.finding_key)
        # receipt 证据（绑输入/输出 revision + 规则 digest + 受影响问题）
        recheck_evidence = {
            "original_run_id": run_id,
            "ruleset_digest": new_run.ruleset_digest,
            "digest_changed": digest_changed,
            "resolved_finding_keys": resolved_keys[:500],
            "persisted_finding_keys": persisted_keys[:500],
            "new_findings_total": new_run.findings_total,
        }
        if repair_receipt:
            recheck_evidence["repair_receipt"] = {
                "plan_id": str(repair_receipt.get("plan_id", ""))[:64],
                "ref_id": str(repair_receipt.get("ref_id", ""))[:128],
                "revision_before": repair_receipt.get("revision_before"),
                "revision_after": repair_receipt.get("revision_after"),
            }
        completeness = dict(new_run.completeness or {})
        completeness["recheck"] = recheck_evidence
        new_run.completeness = completeness
        new_run.recheck_of_run_id = run_id
        db.commit()

        return {
            "recheck_run_id": new_run_id,
            "original_run_id": run_id,
            "status": "completed",
            "digest_changed": digest_changed,
            "resolved_count": len(resolved_keys),
            "persisted_count": len(persisted_keys),
            "new_run_findings_total": new_run.findings_total,
            "new_run_findings_open": new_run.findings_open,
            "recheck_evidence": recheck_evidence,
        }


__all__ = [
    "accept_exception", "revoke_exception", "list_exceptions", "recheck_run",
]
