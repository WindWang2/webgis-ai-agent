"""Topology Workbench —— 服务面（REST 路由与 Agent 工具共用的编排层）。

与 ``feature_edit.service`` 同纪律：单一编排点防两套语义漂移。

- 规则集 CRUD（版本化：内容修改 → version+1 + 新 digest）；
- 运行提交（快照解析 + 预算闸 + durable job）与查询（列表/详情/问题分页/
  witness 读取）；
- 例外（用户决定面：理由必填、锚点固化、stale 判定、撤销）；
- 定点修复桥（finding → G14 预览 plan；dataset 级走既有 new-ref 语义）；
- 复验（同规则 + 最新源重跑 → finding-key diff → resolved 证据判定）；
- 证据导出（findings GeoJSON —— 复用 BlobStore 产物，不另造交付器）。

权限模型：与 data_quality 同面 —— 登录用户 created_by 过滤；session 绑定经
verify_session_owner（外来会话 404 不泄露存在性）；项目绑定经项目访问校验。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select

from app.services.topology_workbench.contracts import (
    ERROR_CODES,
    METHOD_VERSION,
)
from app.services.topology_workbench.ruleset import (
    normalize_run_bindings,
    ruleset_digest,
    validate_ruleset_rules,
)
from app.services.topology_workbench.snapshots import (
    SnapshotError,
    resolve_role_snapshot,
)

logger = logging.getLogger(__name__)


class WorkbenchError(Exception):
    """typed 服务错误（code ∈ ERROR_CODES；路由层映射 4xx，工具层原样投影）。"""

    def __init__(self, code: str, message: str, status: int = 400, **detail: Any):
        super().__init__(message)
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code: {code}")
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail


# ─── 规则集 ───────────────────────────────────────────────────────────


def create_ruleset(
    *, name: str, rules: List[Dict[str, Any]],
    project_id: Optional[str], session_id: Optional[str],
    created_by: Optional[str], org_id: Optional[int] = None,
) -> Dict[str, Any]:
    """创建规则集（v1；非法 DSL → WorkbenchError invalid_params）。"""
    from app.core.database import SessionLocal
    from app.models.topology_workbench import TopologyRuleset

    name = str(name or "").strip()
    if not (1 <= len(name) <= 200):
        raise WorkbenchError("invalid_params", "name 必须是 1..200 字符")
    try:
        norm_rules = validate_ruleset_rules(rules)
    except ValueError as exc:
        raise WorkbenchError("invalid_params", str(exc)) from exc
    digest = ruleset_digest(norm_rules)
    with SessionLocal() as db:
        row = TopologyRuleset(
            org_id=org_id, project_id=project_id, session_id=session_id,
            name=name, current_version=1, rules=norm_rules, digest=digest,
            created_by=created_by,
        )
        db.add(row)
        db.commit()
        return _ruleset_view(row)


def update_ruleset(
    ruleset_id: str, *, name: Optional[str], rules: List[Dict[str, Any]],
    actor_user_id: Optional[str], anchor_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """修改规则集内容 → 新版本（历史运行持有旧 digest，不受影响）。"""
    from app.core.database import SessionLocal

    try:
        norm_rules = validate_ruleset_rules(rules)
    except ValueError as exc:
        raise WorkbenchError("invalid_params", str(exc)) from exc
    new_digest = ruleset_digest(norm_rules)
    with SessionLocal() as db:
        row = _get_owned_ruleset(db, ruleset_id, actor_user_id, anchor_session_id)
        if name is not None:
            nm = str(name).strip()
            if not (1 <= len(nm) <= 200):
                raise WorkbenchError("invalid_params", "name 必须是 1..200 字符")
            row.name = nm
        if new_digest != row.digest:
            row.current_version = int(row.current_version) + 1
            row.rules = norm_rules
            row.digest = new_digest
        elif rules is not None:
            row.rules = norm_rules  # 同 digest（如仅 description 变化）
        db.commit()
        return _ruleset_view(row)


def get_ruleset(
    ruleset_id: str, *, actor_user_id: Optional[str],
    anchor_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        return _ruleset_view(_get_owned_ruleset(
            db, ruleset_id, actor_user_id, anchor_session_id))


def list_rulesets(
    *, project_id: Optional[str], session_id: Optional[str],
    actor_user_id: Optional[str], is_admin: bool = False,
    limit: int = 50, offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    from app.core.database import SessionLocal
    from app.models.topology_workbench import TopologyRuleset

    with SessionLocal() as db:
        stmt = select(TopologyRuleset).order_by(TopologyRuleset.created_at.desc())
        count_stmt = select(func.count()).select_from(TopologyRuleset)
        if project_id:
            stmt = stmt.where(TopologyRuleset.project_id == project_id)
            count_stmt = count_stmt.where(TopologyRuleset.project_id == project_id)
        if session_id:
            stmt = stmt.where(TopologyRuleset.session_id == session_id)
            count_stmt = count_stmt.where(TopologyRuleset.session_id == session_id)
        if actor_user_id and not is_admin:
            stmt = stmt.where(TopologyRuleset.created_by == actor_user_id)
            count_stmt = count_stmt.where(TopologyRuleset.created_by == actor_user_id)
        total = int(db.execute(count_stmt).scalar_one())
        rows = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
        return [_ruleset_view(r) for r in rows], total


def _get_owned_ruleset(
    db: Any, ruleset_id: str, actor_user_id: Optional[str],
    anchor_session_id: Optional[str] = None,
):
    """所有权：用户匹配；或 agent 工具路径的**已验证会话锚定**（turn token
    在 dispatch 前校验 —— bridge 绝不带未验证会话回调）。"""
    from app.models.topology_workbench import TopologyRuleset

    row = db.get(TopologyRuleset, ruleset_id)
    if row is None or not _owned(db, row, actor_user_id, anchor_session_id):
        raise WorkbenchError(
            "not_owned", "规则集不存在或无权访问", status=404,
            ruleset_id=ruleset_id,
        )
    return row


def _owned(db: Any, row: Any, actor_user_id: Optional[str],
           anchor_session_id: Optional[str]) -> bool:
    if actor_user_id and row.created_by == actor_user_id:
        return True
    if anchor_session_id and row.session_id == anchor_session_id:
        return True
    # 会话属主兜底：资源在其会话内创建（如 agent turn 工具路径 —— 工具面
    # 无用户身份、仅 turn 锚定会话）→ 会话所有者经 REST 可见自己的资源。
    if actor_user_id and row.session_id:
        from app.models.db_model import Conversation

        conv = db.get(Conversation, str(row.session_id)[:255])
        if conv is not None and str(conv.user_id or "") == str(actor_user_id):
            return True
    return False


def _ruleset_view(row: Any) -> Dict[str, Any]:
    return {
        "id": row.id, "name": row.name, "project_id": row.project_id,
        "session_id": row.session_id, "current_version": row.current_version,
        "digest": row.digest, "rules": row.rules, "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ─── 运行提交与查询 ───────────────────────────────────────────────────


async def submit_run(
    *, ruleset_id: str, bindings: Dict[str, Dict[str, Any]],
    actor_user_id: Optional[str], actor_owner_token: Optional[str],
    org_id: Optional[int] = None, project_id: Optional[str] = None,
    session_id: Optional[str] = None, submit_job: bool = True,
    anchor_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """提交运行：绑定校验 → 快照解析（提交期）→ run 行 → durable job。

    提交期即解析快照并固化指纹/revision（执行期再解析比对 —— 漂移即 typed
    failed，绝不悄悄换 latest 续算）。``submit_job=False`` 供测试/内联执行。
    """
    from app.core.database import SessionLocal
    from app.models.topology_workbench import TopologyRun

    with SessionLocal() as db:
        # anchor_session_id 只由工具路径显式传入（bridge 已校验 turn 的
        # 会话绑定）；REST 不传 —— 请求体 session_id 不构成所有权证明。
        ruleset = _get_owned_ruleset(
            db, ruleset_id, actor_user_id, anchor_session_id,
        )
        rules = list(ruleset.rules or [])
        digest = ruleset.digest
        version = ruleset.current_version
        rid = ruleset.id
        ruleset_project = ruleset.project_id

    try:
        role_bindings = normalize_run_bindings(rules, bindings)
    except ValueError as exc:
        raise WorkbenchError("invalid_params", str(exc)) from exc

    # 逐（规则 × 角色）解析快照 —— 同一角色跨规则只解析一次，但按规则校验
    # complete/scope 兼容性（scope_not_allowed 在此 typed 拒绝，T12）。
    snapshots: Dict[str, Any] = {}
    for rule in rules:
        if not rule.get("enabled"):
            continue
        rt = rule["rule_type"]
        for role, binding in role_bindings.get(rule["rule_id"], {}).items():
            if role in snapshots:
                # 二次出现：仅校验 complete/scope 语义一致（同一绑定对象）
                continue
            try:
                snap = await resolve_role_snapshot(
                    role=role, binding=binding, rule_type=rt,
                    run_session_id=session_id,
                    actor_user_id=actor_user_id,
                    actor_owner_token=actor_owner_token,
                    max_features=0,
                    anchor_session_id=anchor_session_id,
                )
            except SnapshotError as exc:
                status = 404 if exc.code == "source_unauthorized" else 400
                raise WorkbenchError(exc.code, exc.message, status=status, **exc.detail) from exc
            snapshots[role] = snap

    bindings_evidence = {
        role: {**snap.binding_evidence(), "binding": binding_of(role_bindings, role)}
        for role, snap in snapshots.items()
    }

    with SessionLocal() as db:
        run = TopologyRun(
            org_id=org_id, ruleset_id=rid, ruleset_digest=digest,
            ruleset_version=version, rules_snapshot=rules,
            status="pending", bindings=bindings_evidence,
            method_version=METHOD_VERSION,
            project_id=project_id or ruleset_project,
            session_id=session_id, created_by=actor_user_id,
        )
        db.add(run)
        db.commit()
        run_id = run.id

    result: Dict[str, Any] = {"run_id": run_id, "status": "pending"}
    if submit_job:
        job_info = _submit_durable_topology_job(run_id)
        with SessionLocal() as db:
            row = db.get(TopologyRun, run_id)
            if row is not None and job_info.get("job_id"):
                row.job_id = str(job_info["job_id"])
                db.commit()
        result["job"] = job_info
    return result


def binding_of(
    role_bindings: Dict[str, Dict[str, Dict[str, Any]]], role: str
) -> Dict[str, Any]:
    for per_rule in role_bindings.values():
        if role in per_rule:
            return per_rule[role]
    return {}


def _submit_durable_topology_job(run_id: str) -> Dict[str, Any]:
    """durable job 提交（与 data_quality 同模式；eager 模式测试内联执行）。"""
    from app.services.jobs.submit import submit_durable_job
    from app.services.topology_workbench.jobs import topology_run_task

    return submit_durable_job(
        celery_task=topology_run_task,
        task_type="topology_workbench_run",
        display_name="跨图层拓扑检查",
        params={"run_id": run_id},
        task_kwargs={"run_id": run_id},
    )


def get_run(
    run_id: str, *, actor_user_id: Optional[str], is_admin: bool = False,
    anchor_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        row = _get_owned_run(db, run_id, actor_user_id, is_admin, anchor_session_id)
        return _run_view(row)


def list_runs(
    *, ruleset_id: Optional[str] = None, project_id: Optional[str] = None,
    session_id: Optional[str] = None, actor_user_id: Optional[str],
    is_admin: bool = False, limit: int = 50, offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    from app.core.database import SessionLocal
    from app.models.topology_workbench import TopologyRun

    with SessionLocal() as db:
        stmt = select(TopologyRun).order_by(TopologyRun.created_at.desc())
        count_stmt = select(func.count()).select_from(TopologyRun)
        for field, value in (("ruleset_id", ruleset_id), ("project_id", project_id),
                             ("session_id", session_id)):
            if value:
                stmt = stmt.where(getattr(TopologyRun, field) == value)
                count_stmt = count_stmt.where(getattr(TopologyRun, field) == value)
        if actor_user_id and not is_admin:
            stmt = stmt.where(TopologyRun.created_by == actor_user_id)
            count_stmt = count_stmt.where(TopologyRun.created_by == actor_user_id)
        total = int(db.execute(count_stmt).scalar_one())
        rows = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
        return [_run_view(r, detail=False) for r in rows], total


def _get_owned_run(
    db: Any, run_id: str, actor_user_id: Optional[str], is_admin: bool = False,
    anchor_session_id: Optional[str] = None,
):
    from app.models.topology_workbench import TopologyRun

    row = db.get(TopologyRun, run_id)
    if row is None or not (
        (is_admin and actor_user_id)
        or _owned(db, row, actor_user_id, anchor_session_id)
    ):
        raise WorkbenchError("not_owned", "运行不存在或无权访问", status=404, run_id=run_id)
    return row


def _run_view(row: Any, *, detail: bool = True) -> Dict[str, Any]:
    view = {
        "id": row.id, "ruleset_id": row.ruleset_id,
        "ruleset_digest": row.ruleset_digest, "ruleset_version": row.ruleset_version,
        "status": row.status,
        "findings_total": row.findings_total, "findings_open": row.findings_open,
        "computation_crs": row.computation_crs, "method_version": row.method_version,
        "job_id": row.job_id, "recheck_of_run_id": row.recheck_of_run_id,
        "project_id": row.project_id, "session_id": row.session_id,
        "duration_ms": row.duration_ms,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "has_witness": bool(row.witness_key),
    }
    if detail:
        view["rule_results"] = row.rule_results or []
        view["completeness"] = row.completeness or {}
        view["bindings"] = {
            role: {k: v for k, v in ev.items() if k != "binding"}
            for role, ev in (row.bindings or {}).items()
        }
    return view


def list_findings(
    run_id: str, *, rule_id: Optional[str] = None, severity: Optional[str] = None,
    status: Optional[str] = None, actor_user_id: Optional[str],
    is_admin: bool = False, limit: int = 50, offset: int = 0,
    anchor_session_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int, Dict[str, Any]]:
    """问题分页（完整计数披露 —— total 是全量计数，不是当前页）。"""
    from app.core.database import SessionLocal
    from app.models.topology_workbench import TopologyFinding

    with SessionLocal() as db:
        _get_owned_run(db, run_id, actor_user_id, is_admin, anchor_session_id)
        stmt = select(TopologyFinding).where(TopologyFinding.run_id == run_id)
        count_stmt = select(func.count()).select_from(TopologyFinding).where(
            TopologyFinding.run_id == run_id
        )
        if rule_id:
            stmt = stmt.where(TopologyFinding.rule_id == rule_id)
            count_stmt = count_stmt.where(TopologyFinding.rule_id == rule_id)
        if severity:
            stmt = stmt.where(TopologyFinding.severity == severity)
            count_stmt = count_stmt.where(TopologyFinding.severity == severity)
        if status:
            stmt = stmt.where(TopologyFinding.status == status)
            count_stmt = count_stmt.where(TopologyFinding.status == status)
        total = int(db.execute(count_stmt).scalar_one())
        # 稳定排序（身份与分页换序无关，T16）
        stmt = stmt.order_by(TopologyFinding.severity.desc(), TopologyFinding.finding_key)
        rows = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
        # 状态计数（独立于筛选的运行级计数）
        counts = _finding_status_counts(db, run_id)
        return [_finding_view(r, detail=False) for r in rows], total, counts


def get_finding(
    finding_id: str, *, actor_user_id: Optional[str], is_admin: bool = False,
    anchor_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models.topology_workbench import TopologyFinding

    with SessionLocal() as db:
        row = db.get(TopologyFinding, finding_id)
        if row is None:
            raise WorkbenchError("finding_not_found", "问题不存在", status=404)
        _get_owned_run(db, row.run_id, actor_user_id, is_admin, anchor_session_id)
        return _finding_view(row, detail=True)


def get_finding_witness(
    finding_id: str, *, actor_user_id: Optional[str], is_admin: bool = False,
    anchor_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """finding 的完整 witness 几何（BlobStore 读回 + 摘要校验 + stale 判定）。"""
    from app.core.database import SessionLocal
    from app.models.topology_workbench import TopologyFinding

    with SessionLocal() as db:
        row = db.get(TopologyFinding, finding_id)
        if row is None:
            raise WorkbenchError("finding_not_found", "问题不存在", status=404)
        run = _get_owned_run(db, row.run_id, actor_user_id, is_admin, anchor_session_id)
        if not run.witness_key:
            raise WorkbenchError(
                "source_unresolvable",
                "该运行没有可读的 witness 证据（未落盘或被清理）", status=410,
            )
        idx = row.witness_index
        key, sha = run.witness_key, run.witness_sha256

    from app.services.durable_blob_store import get_filesystem_blob_store

    store = get_filesystem_blob_store()
    raw = store.get_blob(key, expected_sha256=sha)
    if raw is None:
        raise WorkbenchError(
            "source_unresolvable",
            "witness 证据读取失败或摘要不匹配（durable 存储异常）", status=410,
        )
    import json as _json

    try:
        blob = _json.loads(raw.decode("utf-8"))
        features = blob.get("features") or []
        if not (0 <= idx < len(features)):
            raise WorkbenchError(
                "source_unresolvable", "witness 索引越界（证据与问题不匹配）", status=410,
            )
        witness = features[idx]
    except (ValueError, UnicodeDecodeError) as exc:
        raise WorkbenchError(
            "source_unresolvable", f"witness 证据不可解析: {exc}", status=410,
        ) from exc
    # locator stale 判定（T15）：源 revision 前进 / snapshot_index 身份过期
    stale = _locator_staleness(run, row)
    return {
        "finding_id": finding_id,
        "witness": witness,
        "witness_digest": row.witness_digest,
        "stale": stale["stale"], "stale_reason": stale.get("reason"),
        "locators": row.locators or [],
    }


def _locator_staleness(run: Any, finding: Any) -> Dict[str, Any]:
    """定位失效判定（T15）：locator 的 revision 与源的**当前** revision 比对。

    运行绑定证据是提交期快照（不可变）—— 与之比较永远不触发；真正的失效
    是源在运行后前进。session_ref：get_ref_descriptor 的当前 content_revision
    （O(1)）；project_artifact：产物最新修订号。读失败按保守不标 stale
    （witness 本身已可读，权限在更外层把关）。
    """
    import asyncio

    session_id = run.session_id
    for loc in (finding.locators or []):
        kind = loc.get("kind")
        ref = str(loc.get("ref") or "")
        rev = loc.get("content_revision")
        if not isinstance(rev, int):
            continue
        try:
            if kind == "session_ref" and session_id and ref.startswith("ref:"):
                from app.services.session_data import session_data_manager

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    # 已在事件循环内（工具路径经 to_thread 时无循环；直接路径罕见）
                    current = None
                else:
                    current = asyncio.run(
                        session_data_manager.get_ref_descriptor(session_id, ref)
                    ).get("content_revision")
            elif kind == "project_artifact" and ref:
                from sqlalchemy import select as sa_select

                from app.core.database import SessionLocal
                from app.models.project import ArtifactRevision

                with SessionLocal() as db:
                    row = db.execute(
                        sa_select(func.max(ArtifactRevision.revision_no)).where(
                            ArtifactRevision.artifact_id == ref)
                    ).scalar_one_or_none()
                current = int(row) if row is not None else None
            else:
                continue
            if isinstance(current, int) and current != rev:
                return {
                    "stale": True,
                    "reason": f"角色 {loc.get('role')} 源 revision 已前进（{rev} → {current}）",
                }
        except Exception:  # noqa: BLE001 — 判定失败不阻断 witness 读取
            continue
    return {"stale": False}


def _finding_status_counts(db: Any, run_id: str) -> Dict[str, int]:

    from app.models.topology_workbench import TopologyFinding

    rows = db.execute(
        select(TopologyFinding.status, func.count())
        .where(TopologyFinding.run_id == run_id)
        .group_by(TopologyFinding.status)
    ).all()
    return {status: int(cnt) for status, cnt in rows}


def _finding_view(row: Any, *, detail: bool) -> Dict[str, Any]:
    view = {
        "id": row.id, "run_id": row.run_id, "rule_id": row.rule_id,
        "rule_type": row.rule_type, "finding_key": row.finding_key,
        "severity": row.severity, "status": row.status,
        "metric": row.metric, "message": row.message,
        "bbox": [row.bbox_w, row.bbox_s, row.bbox_e, row.bbox_n],
        "geometry_type": row.geometry_type,
        "resolved_by_run_id": row.resolved_by_run_id,
        "exception_id": row.exception_id,
    }
    if detail:
        view["locators"] = row.locators or []
        view["witness_digest"] = row.witness_digest
        view["witness_index"] = row.witness_index
    return view


__all__ = [
    "WorkbenchError", "create_ruleset", "update_ruleset", "get_ruleset",
    "list_rulesets", "submit_run", "get_run", "list_runs", "list_findings",
    "get_finding", "get_finding_witness",
]
