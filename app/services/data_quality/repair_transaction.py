"""RepairSession 修复事务 —— plan→dry-run→apply→verify→rollback 状态机。

定位（DQH v1）：既有管道已经拥有全部执行件 —— ``build_repair_plan``
（plan-only 确定性计划）、``dry_run`` 语义（pipeline 天然非破坏 deepcopy）、
``execute_repair``（新 ref + replaces 血缘 + 有界证据）、
``ArtifactGraph.replacement_chain``（回退导航）。缺的只是**事务语义**：
状态机、失败零残留的显式建模、verify（修复后复评）与 rollback 证据。
本模块编排既有件，绝不新增执行路径、绝不物理改写任何 ref。

红线：

- **状态机确定性**：非法迁移 raise（绝不静默换态）；``session_id`` 由
  (plan_id, source_digest) sha256 派生，墙钟不参与（幂等复用键）；
- **失败零残留**：所有执行件都非破坏（deepcopy + 新 ref），apply 异常 →
  state=failed，源 payload 逐字节不变是结构性保证而非补偿动作；
- **rollback = append-only**：登记回指 source 的证据/新 ref（best-effort，
  失败如实披露），绝不物理回滚；
- **幂等**：同 (plan, source) 已 applied/verified → 返回既有会话，
  不二次执行、不产生第二条 ref 链分叉（有界 LRU，profiler 同纪律）；
- **提案词表 → pipeline 词表**：只经 ``REMEDIATION_OP_BACKING`` 的 fn 背书
  解析（单一事实源），无背书的提案 op 诚实跳过（不硬凑）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.services.data_quality.repair_plan import RepairPlan

logger = logging.getLogger(__name__)

STATE_PROPOSED = "proposed"
STATE_DRY_RUN = "dry_run"
STATE_APPLIED = "applied"
STATE_VERIFIED = "verified"
STATE_FAILED = "failed"
STATE_ROLLED_BACK = "rolled_back"

#: 合法迁移表（缺省 = 非法，raise）。
_ALLOWED_TRANSITIONS: Dict[str, frozenset] = {
    STATE_PROPOSED: frozenset({STATE_DRY_RUN, STATE_FAILED}),
    STATE_DRY_RUN: frozenset({STATE_APPLIED, STATE_FAILED}),
    STATE_APPLIED: frozenset({STATE_VERIFIED, STATE_FAILED, STATE_ROLLED_BACK}),
    STATE_VERIFIED: frozenset({STATE_ROLLED_BACK}),
    STATE_FAILED: frozenset(),
    STATE_ROLLED_BACK: frozenset(),
}

_MAX_HISTORY = 16
_MAX_SESSION_CACHE = 256


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    )


def _digest(data: Any) -> str:
    try:
        canonical = _canonical(data)
    except (TypeError, ValueError):
        canonical = repr(data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class RepairSession:
    """一次修复的事务会话（状态 + 有界历史 + 各阶段证据）。"""

    session_id: str
    plan_id: str
    source_digest: str
    state: str = STATE_PROPOSED
    history: List[Dict[str, Any]] = field(default_factory=list)
    preview: Optional[Dict[str, Any]] = None
    apply_result: Optional[Dict[str, Any]] = None
    verify_result: Optional[Dict[str, Any]] = None
    rollback_result: Optional[Dict[str, Any]] = None
    failure: Optional[Dict[str, Any]] = None

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id[:64],
            "plan_id": self.plan_id[:64],
            "source_digest": self.source_digest[:64],
            "state": self.state,
            "history": [
                {str(k)[:24]: str(v)[:80] for k, v in list(h.items())[:6]}
                for h in self.history[:_MAX_HISTORY]
            ],
            "preview": self.preview,
            "apply_result": self.apply_result,
            "verify_result": self.verify_result,
            "rollback_result": self.rollback_result,
            "failure": self.failure,
        }


def open_repair_session(
    *, plan: RepairPlan, source_payload: Any,
) -> RepairSession:
    """plan + 源载荷 → 确定性会话（proposed）。同输入同 session_id。"""
    source_digest = _digest(source_payload)
    session_id = "rsession_" + hashlib.sha256(
        f"{plan.plan_id}:{source_digest}".encode("utf-8")
    ).hexdigest()[:24]
    return RepairSession(
        session_id=session_id,
        plan_id=plan.plan_id,
        source_digest=source_digest,
        history=[{"action": "open", "state": STATE_PROPOSED}],
    )


def _transition(session: RepairSession, action: str, to_state: str, **extra: Any) -> RepairSession:
    if to_state not in _ALLOWED_TRANSITIONS.get(session.state, frozenset()):
        raise ValueError(
            f"非法事务迁移 {session.state} → {to_state}（action={action}）；"
            f"合法目标 = {sorted(_ALLOWED_TRANSITIONS.get(session.state, frozenset()))}"
        )
    from_state = session.state
    session.state = to_state
    session.history.append(
        {"action": action, "from": from_state, "to": to_state, "ts": time.time(), **extra}
    )
    return session


def mark_dry_run(session: RepairSession, *, preview: Dict[str, Any]) -> RepairSession:
    session.preview = dict(list(preview.items())[:12])
    return _transition(session, "dry_run", STATE_DRY_RUN)


def mark_applied(session: RepairSession, *, apply_result: Dict[str, Any]) -> RepairSession:
    if str(apply_result.get("status", "")) != "success":
        return mark_failed(session, reason=str(apply_result.get("status", "no-status")))
    session.apply_result = dict(list(apply_result.items())[:16])
    return _transition(session, "apply", STATE_APPLIED)


def mark_verified(session: RepairSession, *, verify_result: Dict[str, Any]) -> RepairSession:
    session.verify_result = dict(list(verify_result.items())[:12])
    return _transition(session, "verify", STATE_VERIFIED)


def mark_failed(session: RepairSession, *, reason: str) -> RepairSession:
    session.failure = {"reason": str(reason)[:200]}
    return _transition(session, "fail", STATE_FAILED, reason=str(reason)[:80])


def mark_rolled_back(session: RepairSession, *, rollback_evidence: Dict[str, Any]) -> RepairSession:
    session.rollback_result = dict(list(rollback_evidence.items())[:12])
    return _transition(session, "rollback", STATE_ROLLED_BACK)


# ── 提案词表 → pipeline 词表（REMEDIATION_OP_BACKING 单一事实源）────────


def resolve_pipeline_ops(proposal_operations: Sequence[str]) -> List[str]:
    """提案 op → 有背书的 pipeline op（CANONICAL_OP_ORDER 序）。

    ``fn:app.services.spatial_repair_pipeline:<name>`` 背书 → pipeline op；
    其它背书形态（capability:/op:）不在 pipeline 事务口径内 → 诚实跳过。
    """
    from app.services.gis_harness.data_qualification import REMEDIATION_OP_BACKING
    from app.services.spatial_repair_pipeline import CANONICAL_OP_ORDER

    prefix = "fn:app.services.spatial_repair_pipeline:"
    resolved: set = set()
    for op in proposal_operations or []:
        for backing in REMEDIATION_OP_BACKING.get(str(op), ()):
            b = str(backing)
            if b.startswith(prefix):
                name = b[len(prefix):]
                if name in CANONICAL_OP_ORDER:
                    resolved.add(name)
    return [op for op in CANONICAL_OP_ORDER if op in resolved]


# ── 会话缓存（幂等复用；有界 LRU，profiler 同纪律）────────────────────

_SESSION_CACHE: "Dict[str, RepairSession]" = {}


def reset_session_cache() -> None:
    """清空会话缓存（测试隔离 / 长生命周期进程的显式回收点）。"""
    _SESSION_CACHE.clear()


def _cache_put(session: RepairSession) -> None:
    if len(_SESSION_CACHE) >= _MAX_SESSION_CACHE:
        _SESSION_CACHE.pop(next(iter(_SESSION_CACHE)), None)
    _SESSION_CACHE[session.session_id] = session


def _session_from_bounded(bounded: Dict[str, Any]) -> RepairSession:
    return RepairSession(
        session_id=str(bounded.get("session_id", "")),
        plan_id=str(bounded.get("plan_id", "")),
        source_digest=str(bounded.get("source_digest", "")),
        state=str(bounded.get("state", STATE_PROPOSED)),
        history=[{"action": "restore", "state": str(bounded.get("state", ""))}],
    )


# ── 事务编排 ─────────────────────────────────────────────────────────


async def run_repair_transaction(
    *,
    geojson: Dict[str, Any],
    plan: RepairPlan,
    operations: Optional[List[str]] = None,
    mode: str = "apply",
    source_crs: str = "EPSG:4326",
    target_crs: str = "EPSG:4326",
    tolerance: float = 1e-5,
    session_id: Optional[str] = None,
    source_ref: Optional[str] = None,
    issue_codes: Optional[List[str]] = None,
    prior: Optional[Dict[str, Any]] = None,
    verify_evaluator: Optional[Any] = None,
) -> Dict[str, Any]:
    """修复事务（dry_run / apply / rollback 三模式）。

    - ``dry_run``：pipeline 在 deepcopy 上演练（登记零副作用）→ 预测摘要；
    - ``apply``：演练 → ``execute_repair``（新 ref + 证据）→ verify 复评；
      同 (plan, source) 已 applied/verified → 幂等返回既有会话；
    - ``rollback``：仅从 applied/verified；登记回指 source 的证据
      （session_id 提供时 best-effort 重登记源载荷为新 ref，失败如实披露）。
    """
    from app.services.spatial_repair_pipeline import SpatialRepairPipeline

    if mode == "rollback":
        return await _rollback(geojson=geojson, plan=plan, prior=prior, session_id=session_id)

    pipeline_ops = list(operations) if operations is not None else []
    if not pipeline_ops and plan.operations:
        pipeline_ops = resolve_pipeline_ops(
            [s.operation for s in plan.operations])

    session = open_repair_session(plan=plan, source_payload=geojson)
    cached = _SESSION_CACHE.get(session.session_id)
    if cached is not None and cached.state in (STATE_APPLIED, STATE_VERIFIED, STATE_ROLLED_BACK):
        return {
            "session": cached.to_bounded_dict(),
            "idempotent_reuse": True,
        }

    try:
        # dry-run 演练：pipeline 天然非破坏（deepcopy 输入）。
        from app.services.data_quality.repair_execution import _count_features

        repaired, _logs, ops_evidence = await asyncio.to_thread(
            SpatialRepairPipeline.repair_dataset_detailed,
            geojson,
            ops=pipeline_ops,
            tolerance=tolerance,
            source_crs=source_crs,
            target_crs=target_crs,
        )
        preview = {
            "pipeline_ops": [str(o)[:32] for o in pipeline_ops],
            "source_feature_count": _count_features(geojson),
            "predicted_feature_count_after": _count_features(repaired),
            "predicted_digest_after": _digest(repaired),
            "ops_evidence": [
                {str(k)[:24]: v for k, v in list(e.items())[:6]}
                for e in ops_evidence[:_MAX_PREVIEW_OPS]
            ],
        }
        session = mark_dry_run(session, preview=preview)
    except Exception as exc:  # noqa: BLE001 — 演练失败也是事务失败（如实）
        logger.warning("[RepairTransaction] dry_run failed session=%s: %s",
                       session.session_id, exc)
        session = mark_failed(session, reason=f"dry_run: {exc}")
        _cache_put(session)
        return {
            "session": session.to_bounded_dict(),
            "failure": session.failure,
        }

    if mode == "dry_run":
        _cache_put(session)
        return {"session": session.to_bounded_dict(), "preview": preview}

    # ── apply：execute_repair（新 ref + replaces 血缘 + 有界证据）────────
    try:
        from app.services.data_quality.repair_execution import execute_repair

        apply_result = await execute_repair(
            geojson=geojson,
            operations=pipeline_ops,
            source_crs=source_crs,
            target_crs=target_crs,
            tolerance=tolerance,
            session_id=session_id,
            source_ref=source_ref,
            issue_codes=issue_codes,
            plan=plan,
        )
    except Exception as exc:  # noqa: BLE001 — 失败零残留：源载荷未被触碰
        logger.warning("[RepairTransaction] apply failed session=%s: %s",
                       session.session_id, exc)
        session = mark_failed(session, reason=str(exc)[:200])
        _cache_put(session)
        return {
            "session": session.to_bounded_dict(),
            "failure": session.failure,
        }

    session = mark_applied(session, apply_result={
        "status": "success",
        "feature_count_after": apply_result.get("feature_count"),
        "feature_count_before": apply_result.get("feature_count_before"),
        "content_digest_before": apply_result.get("content_digest_before"),
        "content_digest_after": apply_result.get("content_digest_after"),
        "repaired_ref": apply_result.get("repaired_ref"),
        "ref_registration_error": apply_result.get("ref_registration_error"),
    })

    # ── verify：修复后复评（residual 质量事实）───────────────────────────
    verify_result = await _verify(repaired, verify_evaluator)
    if verify_result.get("residual_status") == "pass":
        session = mark_verified(session, verify_result=verify_result)
    else:
        # apply 成功但有残留 → 保持 applied，残留事实随会话披露。
        session.verify_result = verify_result
        session.history.append(
            {"action": "verify", "state": session.state, "to": session.state,
             "residual": str(verify_result.get("residual_status", ""))[:32]})

    _cache_put(session)
    return {
        "session": session.to_bounded_dict(),
        "apply": {
            "feature_count_after": apply_result.get("feature_count"),
            "feature_count_before": apply_result.get("feature_count_before"),
            "content_digest_before": apply_result.get("content_digest_before"),
            "content_digest_after": apply_result.get("content_digest_after"),
            "repaired_ref": apply_result.get("repaired_ref"),
            "output_crs": apply_result.get("output_crs"),
        },
        "verify": verify_result,
        "repaired_geojson": apply_result.get("repaired_geojson"),
        "repair_evidence": apply_result.get("repair_evidence"),
    }


_MAX_PREVIEW_OPS = 16


async def _verify(repaired: Any, verify_evaluator: Optional[Any]) -> Dict[str, Any]:
    """修复后复评：默认规则引擎 overall；evaluator 可注入（契约同 dict）。"""
    try:
        if verify_evaluator is not None:
            report = verify_evaluator(repaired)
        else:
            from app.services.data_quality.engine import evaluate_payload

            report = await asyncio.to_thread(evaluate_payload, repaired)
        if isinstance(report, dict):
            overall = str(report.get("overall_status", ""))
            return {
                "residual_status": overall,
                "failed_count": int(report.get("failed_count", 0) or 0),
                "warn_count": int(report.get("warn_count", 0) or 0),
            }
        overall = str(getattr(report, "overall_status", "") or "")
        return {"residual_status": overall}
    except Exception as exc:  # noqa: BLE001 — verify 失败不否定已成功的 apply
        return {"residual_status": "error", "verify_error": str(exc)[:160]}


async def _rollback(
    *,
    geojson: Any,
    plan: RepairPlan,
    prior: Optional[Dict[str, Any]],
    session_id: Optional[str],
) -> Dict[str, Any]:
    """回退：append-only 语义 —— 记回指 source 的证据；绝不物理改写。"""
    cached: Optional[RepairSession] = None
    sid = str((prior or {}).get("session", {}).get("session_id", ""))
    if sid:
        cached = _SESSION_CACHE.get(sid)
    if cached is None and prior:
        cached = _session_from_bounded(prior.get("session") or {})
    if cached is None:
        raise ValueError("rollback 需要 prior 会话或已缓存的会话（无事务可回退）")

    repaired_ref = ""
    apply_result = cached.apply_result or {}
    if isinstance(apply_result, dict):
        repaired_ref = str(apply_result.get("repaired_ref", "") or "")
    evidence = {
        "plan_id": plan.plan_id,
        "session_id": cached.session_id,
        "source_digest": cached.source_digest,
        "rollback_of": repaired_ref[:80],
        "note": "source payload re-registration is caller-driven; "
                "rollback is append-only evidence + state transition",
    }

    # best-effort：把源载荷重登记为新 ref（恢复语义）；失败如实披露。
    registration_error = None
    if session_id:
        try:
            from app.services.session_data import session_data_manager

            restore_ref = await session_data_manager.store(
                session_id, geojson, prefix="restored")
            evidence["restore_ref"] = str(restore_ref)[:80]
        except Exception as exc:  # noqa: BLE001 — 登记失败不影响回退状态
            registration_error = str(exc)[:200]
            evidence["registration_error"] = registration_error

    session = mark_rolled_back(cached, rollback_evidence=evidence)
    _cache_put(session)
    return {"session": session.to_bounded_dict(), "rollback": evidence}


__all__ = [
    "STATE_PROPOSED",
    "STATE_DRY_RUN",
    "STATE_APPLIED",
    "STATE_VERIFIED",
    "STATE_FAILED",
    "STATE_ROLLED_BACK",
    "RepairSession",
    "open_repair_session",
    "mark_dry_run",
    "mark_applied",
    "mark_verified",
    "mark_failed",
    "mark_rolled_back",
    "resolve_pipeline_ops",
    "run_repair_transaction",
]
