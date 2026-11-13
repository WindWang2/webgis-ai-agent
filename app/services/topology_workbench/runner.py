"""Topology Workbench —— 运行执行体（WP07）。

``execute_topology_run``：durable job 的 worker 侧执行体（测试中可直接调用）。

执行序（恢复以已落地事实为准，任务书 §六）：

1. 读取 run 行（pending → running）；加载**运行时规则快照**（rules_snapshot，
   与 ruleset 当前编辑解耦）；
2. 逐角色再解析快照：内容指纹与**提交期指纹**逐一比对 —— 任一漂移即 typed
   failed（``source_changed_after_submission``；绝不悄悄换 latest 续算）；
   不可读 → 明确 failed + 保留已有证据；
3. 关系统一（关系 CRS）→ 预检（类型/有限性/有效性，剔除计数）→ 计算 CRS
   选择（不支持区域 → 该规则 blocked，typed 原因）；
4. 逐规则求值（预算/取消；维度分离结论）；
5. witness 集合 → BlobStore 单 blob；findings + run 终态**单事务**落库
   （客户端绝不先见 completed 再读半份问题）；
6. 例外应用（锚点全匹配才生效；漂移 → 例外 stale，绝不自动延续）。

复用（缓存复用必须覆盖完整输入身份）：``input_digest`` =
sha256(全部源内容指纹 + 规则 digest + 方法版本) —— 同 ref 字符串内容变更
不复用；已取消/部分计算/预算截断的报告**不作为**复用缓存。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from sqlalchemy import select
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.topology_workbench.contracts import (
    METHOD_VERSION,
    MAX_WITNESS_BLOB_BYTES,
)
from app.services.topology_workbench.evaluators import (
    EVALUATORS,
    RULE_EXPECTED_FAMILY,
    MeasureSpace,
    RuleEvalOutput,
    _Budget,
)
from app.services.topology_workbench.geometry_core import (
    PreparedSource,
    RegionUnsupported,
    choose_computation_crs,
    geometry_to_geojson,
    prepare_source,
    transform_source_in_place,
)

logger = logging.getLogger(__name__)


class RunExecutionError(Exception):
    """执行期 typed 失败（code + message；run 落 failed，不虚构结论）。"""

    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def compute_input_digest(
    ruleset_digest: str, role_fingerprints: Dict[str, str], scopes: Dict[str, Any]
) -> str:
    """复用键：全部源内容指纹 + 规则 digest + scope + 方法版本。"""
    material = json.dumps(
        {
            "roles": {r: role_fingerprints[r] for r in sorted(role_fingerprints)},
            "scopes": {r: scopes.get(r) for r in sorted(scopes)},
            "ruleset_digest": ruleset_digest,
            "method_version": METHOD_VERSION,
        },
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:48]


async def execute_topology_run(
    run_id: str,
    *,
    should_cancel: Callable[[], bool] = lambda: False,
    allow_source_refresh: bool = False,
    recheck_of_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """执行一次运行（幂等：terminal 状态直接返回；失败路径落 failed 行）。"""

    from app.core.database import SessionLocal
    from app.models.topology_workbench import (
        TopologyRun,
    )

    started = time.monotonic()
    with SessionLocal() as db:
        run = db.get(TopologyRun, run_id)
        if run is None:
            raise RunExecutionError("run_not_found", f"运行不存在: {run_id}")
        if run.status in ("completed", "failed", "cancelled"):
            return {"status": run.status, "run_id": run_id, "already_terminal": True}
        run.status = "running"
        run.started_at = run.started_at or datetime.now(timezone.utc)
        db.commit()

        rules: List[Dict[str, Any]] = list(run.rules_snapshot or [])
        bindings_evidence: Dict[str, Dict[str, Any]] = dict(run.bindings or {})
        try:
            result = await _execute(
                db, run, rules, bindings_evidence,
                should_cancel=should_cancel,
                allow_source_refresh=allow_source_refresh,
                recheck_of_run_id=recheck_of_run_id,
            )
        except RunExecutionError as exc:
            run.status = "failed"
            run.completeness = {
                **(run.completeness or {}),
                "failure_code": exc.code,
                "failure_message": exc.message[:300],
                **({"failure_detail": exc.detail} if exc.detail else {}),
            }
            run.finished_at = datetime.now(timezone.utc)
            run.duration_ms = int((time.monotonic() - started) * 1000)
            db.commit()
            return {"status": "failed", "run_id": run_id, "code": exc.code,
                    "message": exc.message}
        except Exception as exc:  # noqa: BLE001 — 未知异常也落 failed（诚实）
            logger.exception("[topology-run] unexpected failure run=%s", run_id)
            run.status = "failed"
            run.completeness = {
                **(run.completeness or {}), "failure_code": "internal_error",
                "failure_message": str(exc)[:300],
            }
            run.finished_at = datetime.now(timezone.utc)
            run.duration_ms = int((time.monotonic() - started) * 1000)
            db.commit()
            return {"status": "failed", "run_id": run_id, "code": "internal_error"}

        cancelled = result.get("status") == "cancelled"
        run.status = "cancelled" if cancelled else "completed"
        run.finished_at = datetime.now(timezone.utc)
        run.duration_ms = int((time.monotonic() - started) * 1000)
        db.commit()
        return result


async def _execute(
    db: Any, run: Any, rules: List[Dict[str, Any]],
    bindings_evidence: Dict[str, Dict[str, Any]],
    *, should_cancel: Callable[[], bool],
    allow_source_refresh: bool, recheck_of_run_id: Optional[str],
) -> Dict[str, Any]:
    """核心执行（在 run.status=running 下；异常由调用方落 failed）。"""

    enabled_rules = [r for r in rules if r.get("enabled")]
    if not enabled_rules:
        raise RunExecutionError(
            "no_enabled_rules", "规则集没有启用中的规则 —— 拒绝空运行（不得显示全部通过）"
        )

    # ── 1. 逐角色再解析 + 指纹比对（提交期 vs 执行期） ──────────────────
    # worker 侧鉴权与 data_quality 同纪律：提交者（created_by）必须仍是会话
    # 所有者（Conversation.user_id 匹配）；伪造任务参数不能读他人会话 ref。
    role_snapshots: Dict[str, Any] = {}
    role_fingerprints: Dict[str, str] = {}
    role_scopes: Dict[str, Any] = {}
    for role, ev in bindings_evidence.items():
        binding = ev.get("binding") or {}
        snap = await _resolve_binding_for_execution(role, binding, run)
        submitted_fp = str(ev.get("fingerprint") or "")
        if snap.fingerprint != submitted_fp and not allow_source_refresh:
            raise RunExecutionError(
                "source_changed_after_submission",
                (
                    f"角色 '{role}' 内容在提交后发生变化"
                    f"（提交 {submitted_fp[:12]} → 现在 {snap.fingerprint[:12]}）"
                    "—— 拒绝在移动的地基上计算；请重新提交运行"
                ),
                role=role,
            )
        role_snapshots[role] = snap
        role_fingerprints[role] = snap.fingerprint_full()
        role_scopes[role] = snap.scope

    run.input_digest = compute_input_digest(
        run.ruleset_digest, role_fingerprints, role_scopes
    )
    db.commit()

    # ── 2. 复用检查（完整输入身份；非完整结论不作为缓存） ────────────────
    reused = _find_reusable_run(db, run)
    if reused is not None:
        return _clone_reused_run(db, run, reused)

    # ── 3. 关系统一 + 预检 + 计算 CRS ───────────────────────────────────
    prepared: Dict[str, PreparedSource] = {}
    for role, snap in role_snapshots.items():
        prepared[role] = prepare_source(
            role=role, source_kind=snap.kind, ref=snap.ref,
            content_revision=snap.content_revision, fingerprint=snap.fingerprint,
            crs=snap.crs or "EPSG:4326", features=snap.features,
            expected_family=_union_expected_family(enabled_rules, role),
        )

    # 度量 CRS：联合范围（WGS84 bbox；非 4326 源先行转换范围）
    bboxes = []
    for role, prep in prepared.items():
        bb = _source_bbox_wgs84(prep)
        if bb:
            bboxes.append(bb)
    try:
        measure_crs = choose_computation_crs(*bboxes) if bboxes else "EPSG:4326"
    except RegionUnsupported as exc:
        # 区域不支持：逐规则 blocked（typed 原因），不虚构任何通过
        rule_results = [
            _blocked_result(r, exc.code, exc.message) for r in enabled_rules
        ]
        return await _finalize_run(
            db, run, rule_results, [], prepared,
            measure_crs=None, should_cancel=should_cancel,
            region_blocked=exc.code,
        )
    run.computation_crs = measure_crs

    # 关系 CRS = 首个（角色序）源的声明 CRS；其余源变换对齐（T07：可转换 CRS
    # 的同一物理关系判定一致 —— 关系运算在统一空间进行）
    rel_crs = next(iter(prepared.values())).crs or "EPSG:4326"
    for role, prep in prepared.items():
        transform_source_in_place(prep, rel_crs)
    measure = MeasureSpace(rel_crs, measure_crs)

    # ── 4. 逐规则求值 ──────────────────────────────────────────────────
    rule_results: List[RuleEvalOutput] = []
    for rule in enabled_rules:
        if should_cancel():
            break
        try:
            out = await _eval_rule(
                rule, prepared, measure, run.ruleset_digest, should_cancel
            )
        except RegionUnsupported as exc:
            out = _blocked_result(rule, exc.code, exc.message)
        rule_results.append(out)

    # ── 5/6. 终态落库（witness blob + findings + run 单事务） ───────────
    return await _finalize_run(
        db, run, rule_results, enabled_rules, prepared, measure_crs=measure_crs,
        should_cancel=should_cancel, measure=measure, recheck_of=recheck_of_run_id,
    )


# ─── 内部：绑定再解析 / 复用 / 预检族 / 求值分派 ─────────────────────────


async def _resolve_binding_for_execution(role: str, binding: Dict[str, Any], run: Any):
    """worker 侧再解析（同一服务面；鉴权 = 提交者仍拥有该会话/项目）。"""
    from app.services.topology_workbench.snapshots import (
        SnapshotError,
        resolve_role_snapshot,
    )

    # 鉴权链：run 行只在提交期校验后创建 —— run.session_id 即提交期已验证
    # 的锚定会话（用户匹配或 turn token）。执行期以它为锚：绑定同会话的
    # session_ref 免二次校验（与 data_quality 信任 job kwargs 的模型一致，
    # 信任源 = 提交期守卫）；跨会话/项目绑定仍走显式鉴权。
    owner = run.created_by if run.session_id else None
    try:
        return await resolve_role_snapshot(
            role=role, binding=binding, rule_type="",
            run_session_id=run.session_id,
            actor_user_id=owner, actor_owner_token=None,
            max_features=0,
            anchor_session_id=run.session_id,
        )
    except SnapshotError as exc:
        raise RunExecutionError(
            "source_unresolvable" if exc.code == "source_unresolvable"
            else exc.code,
            f"角色 '{role}' 快照不可读: {exc.message}",
            role=role,
        ) from exc


def _find_reusable_run(db: Any, run: Any) -> Optional[Any]:
    from sqlalchemy import select as sa_select

    from app.models.topology_workbench import TopologyRun

    row = db.execute(
        sa_select(TopologyRun)
        .where(
            TopologyRun.input_digest == run.input_digest,
            TopologyRun.ruleset_digest == run.ruleset_digest,
            TopologyRun.method_version == METHOD_VERSION,
            TopologyRun.status == "completed",
            TopologyRun.id != run.id,
        )
        .order_by(TopologyRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    # 完整性门：复用缓存必须是完整结论（无预算截断）
    completeness = row.completeness or {}
    if completeness.get("budget_truncated"):
        return None
    return row


def _clone_reused_run(db: Any, run: Any, reused: Any) -> Dict[str, Any]:
    """复用运行 = **同一份结论的完整投影**：逐条克隆 findings（同 key 同证据，
    新 run_id 归属），规则结果/完整性/witness 指针随行复制；例外按锚点重新
    应用（复用不虚构处理状态）。这保证 findings 查询在新 run 上有完整问题集
    （复用是计算缓存，不是结论隐藏）。"""
    from app.models.topology_workbench import TopologyFinding

    exception_by_key: Dict[str, Any] = {}
    for exc in _load_active_exceptions(db, run.ruleset_id):
        anchor = exc.anchor or {}
        if anchor.get("ruleset_digest") == run.ruleset_digest:
            exception_by_key[exc.finding_key] = exc

    src_findings = db.execute(
        select(TopologyFinding).where(TopologyFinding.run_id == reused.id)
    ).scalars().all()
    open_count = 0
    for f in src_findings:
        status = f.status
        exception_id = f.exception_id if status == "accepted_exception" else None
        # 处理状态不跨运行虚构：resolved 归属原复验链；例外按当前锚点重新应用
        if status in ("resolved", "dismissed"):
            status = "open"
            exception_id = None
        elif status == "accepted_exception":
            exc = exception_by_key.get(f.finding_key)
            if exc is None:
                status = "open"
                exception_id = None
            else:
                exception_id = exc.id
        if status == "open":
            open_count += 1
        db.add(TopologyFinding(
            run_id=run.id, rule_id=f.rule_id, rule_type=f.rule_type,
            finding_key=f.finding_key, severity=f.severity, status=status,
            metric=f.metric, locators=f.locators,
            witness_index=f.witness_index, witness_digest=f.witness_digest,
            bbox_w=f.bbox_w, bbox_s=f.bbox_s, bbox_e=f.bbox_e, bbox_n=f.bbox_n,
            geometry_type=f.geometry_type, message=f.message,
            exception_id=exception_id,
        ))
    run.rule_results = reused.rule_results
    run.completeness = {
        **(reused.completeness or {}),
        "reused_from": reused.id,
        "reused": True,
    }
    run.findings_total = len(src_findings)
    run.findings_open = open_count
    run.witness_key = reused.witness_key
    run.witness_sha256 = reused.witness_sha256
    run.witness_feature_count = reused.witness_feature_count
    run.computation_crs = reused.computation_crs
    db.commit()
    return {
        "status": "completed", "run_id": run.id, "reused_from": reused.id,
        "findings_total": run.findings_total,
        "message": "相同输入身份（源内容指纹 + 规则 digest + 方法版本）的完整"
                   "运行已存在 —— 复用其结论与问题集",
    }


def _union_expected_family(rules: List[Dict[str, Any]], role: str) -> str:
    """跨规则合并角色期望族（any 为并集下限）。"""
    families = set()
    for rule in rules:
        spec = RULE_EXPECTED_FAMILY.get(rule["rule_type"], {})
        fam = spec.get(role)
        if fam is None:
            continue
        families.add(fam)
    if not families or "any" in families:
        return "any"
    if families == {"polygon"}:
        return "polygon"
    if families == {"line"}:
        return "line"
    if families == {"point"}:
        return "point"
    return "any"


def _source_bbox_wgs84(prep: PreparedSource) -> Optional[Tuple[float, float, float, float]]:
    from app.services.topology_workbench.geometry_core import source_bbox

    bb = source_bbox(prep)
    if bb is None:
        return None
    crs = (prep.crs or "EPSG:4326").upper()
    if crs in ("EPSG:4326", "WGS84"):
        return bb
    try:
        from pyproj import Transformer
        from shapely.geometry import box
        from shapely.ops import transform as shp_transform

        t = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
        g = shp_transform(t, box(*bb))
        w, s, e, n = g.bounds
        return (min(w, e), min(s, n), max(w, e), max(s, n))  # 跨经线坍缩成保守框
    except Exception:  # noqa: BLE001 — 转换失败按不支持区域处理
        raise RegionUnsupported(
            "crs_unsupported", f"无法把源范围从 {crs} 转换到 WGS84（角色 {prep.role}）"
        )


async def _eval_rule(
    rule: Dict[str, Any], prepared: Dict[str, PreparedSource],
    measure: MeasureSpace, ruleset_digest: str,
    should_cancel: Callable[[], bool],
) -> RuleEvalOutput:
    rt = rule["rule_type"]
    fn = EVALUATORS[rt]
    roles = RULE_EXPECTED_FAMILY[rt]
    from app.services.topology_workbench.contracts import RULE_ROLE_SPECS

    required = {r for r, req in RULE_ROLE_SPECS[rt].items() if req.startswith("required")}
    budget = _Budget()
    kwargs: Dict[str, Any] = {
        "rule": rule, "measure": measure, "ruleset_digest": ruleset_digest,
        "budget": budget, "should_cancel": should_cancel,
    }
    for role in roles:
        src = prepared.get(role)
        if src is None:
            if role in required:
                out = RuleEvalOutput(rule_id=rule["rule_id"], rule_type=rt)
                return out.blocked(
                    "role_unbound", f"必需角色 '{role}' 未绑定（装配缺陷）"
                )
            continue  # 可选角色（exclusions/exit_zones）缺省合法
        kwargs[role] = src
    # 重负荷 GEOS 核卸载线程（事件循环纪律：不在 async 循环里做全载荷几何）
    import asyncio

    return await asyncio.to_thread(fn, **kwargs)


def _blocked_result(rule: Dict[str, Any], code: str, message: str) -> RuleEvalOutput:
    out = RuleEvalOutput(rule_id=rule["rule_id"], rule_type=rule["rule_type"])
    return out.blocked(code, message)


# ─── 终态落库（单事务：witness blob → findings → run completed） ─────────


async def _finalize_run(
    db: Any, run: Any, rule_results: List[RuleEvalOutput],
    enabled_rules: List[Dict[str, Any]], prepared: Dict[str, PreparedSource],
    *, measure_crs: Optional[str], should_cancel: Callable[[], bool],
    measure: Optional[MeasureSpace] = None,
    region_blocked: Optional[str] = None,
    recheck_of: Optional[str] = None,
) -> Dict[str, Any]:
    from app.models.topology_workbench import (
        TopologyFinding,
    )

    cancelled = should_cancel()
    findings_rows: List[Dict[str, Any]] = []
    witness_features: List[Dict[str, Any]] = []

    for out in rule_results:
        for i, f in enumerate(out.findings):
            gj = geometry_to_geojson(f.witness_geom)
            bbox = _bbox_of_gj(gj)
            findings_rows.append({
                "run_id": run.id,
                "rule_id": f.rule_id,
                "rule_type": f.rule_type,
                "finding_key": f.key(run.ruleset_digest),
                "severity": f.severity,
                "status": "open",
                "metric": f.metric,
                "locators": f.locators,
                "witness_index": len(witness_features),
                "witness_digest": f.witness_digest,
                "bbox": bbox,
                "geometry_type": f.geometry_type,
                "message": f.message,
            })
            witness_features.append({
                "type": "Feature",
                "properties": {
                    "finding_key": f.key(run.ruleset_digest),
                    "rule_id": f.rule_id, "rule_type": f.rule_type,
                    "witness_digest": f.witness_digest,
                },
                "geometry": gj,
            })

    # witness blob（BlobStore 内容寻址；超预算 → 如实失败，不丢证据也不假完整）
    witness_key = witness_sha = None
    if witness_features:
        blob = {
            "type": "FeatureCollection",
            "crs": {"code": "EPSG:4326"},
            "features": witness_features,
            "_meta": {
                "run_id": run.id, "ruleset_digest": run.ruleset_digest,
                "computation_crs": measure_crs, "method_version": METHOD_VERSION,
            },
        }
        raw = json.dumps(blob, ensure_ascii=False, separators=(",", ":"))
        size = len(raw.encode("utf-8"))
        if size > MAX_WITNESS_BLOB_BYTES:
            raise RunExecutionError(
                "witness_budget_exceeded",
                f"witness 集合 {size} 字节超出预算 {MAX_WITNESS_BLOB_BYTES} —— "
                "请缩小检查范围后重试",
                size=size,
            )
        import asyncio as _aio

        witness_key, witness_sha = await _aio.to_thread(_put_witness_blob, raw)

    # 例外应用 + 例外 stale 判定（锚点全匹配才生效）
    active_exceptions = _load_active_exceptions(db, run.ruleset_id)
    anchor_sources = {
        role: snap.fingerprint for role, snap in prepared.items()
    }
    exception_by_key: Dict[str, Any] = {}
    for exc in active_exceptions:
        anchor = exc.anchor or {}
        if anchor.get("ruleset_digest") != run.ruleset_digest:
            _mark_exception_stale(db, exc, "ruleset_changed")
            continue
        src_ok = all(
            anchor_sources.get(r) == fp
            for r, fp in (anchor.get("source_fingerprints") or {}).items()
        )
        if not src_ok:
            _mark_exception_stale(db, exc, "source_changed")
            continue
        exception_by_key[exc.finding_key] = exc

    open_count = 0
    for row in findings_rows:
        exc = exception_by_key.get(row["finding_key"])
        if exc is not None:
            row["status"] = "accepted_exception"
            row["exception_id"] = exc.id
        else:
            open_count += 1

    # 复验溯源：本运行如果是 recheck，旧运行的 resolved 判定在 recheck 服务
    # 中完成（需要新运行完整落库后比对）；这里只记录 lineage。
    all_skipped = all(
        o.applicability == "blocked" or o.outcome == "unknown" for o in rule_results
    )
    budget_truncated = any(o.computation == "partial" for o in rule_results)

    run.rule_results = [
        {
            "rule_id": o.rule_id, "rule_type": o.rule_type,
            "applicability": o.applicability,
            "blocked_code": o.blocked_code, "blocked_message": o.blocked_message,
            "input_completeness": o.input_completeness,
            "computation": o.computation, "outcome": o.outcome,
            "counts": o.counts, "notes": [n[:300] for n in o.notes][:12],
            "duration_ms": o.duration_ms,
        }
        for o in rule_results
    ]
    run.completeness = {
        "all_skipped_or_unknown": all_skipped,
        "budget_truncated": budget_truncated,
        "region_blocked": region_blocked,
        "excluded_summary": {
            role: {"total": p.total_input, "examined": p.examined,
                   "excluded": dict(p.excluded)}
            for role, p in prepared.items()
        },
        "cancelled_midway": cancelled,
        **({"recheck_of_run_id": recheck_of} if recheck_of else {}),
    }
    run.findings_total = len(findings_rows)
    run.findings_open = open_count
    run.witness_key = witness_key
    run.witness_sha256 = witness_sha
    run.witness_feature_count = len(witness_features)

    # findings 落库（同事务：终态与问题集一致可见）
    if findings_rows:
        db.add_all([
            TopologyFinding(
                run_id=r["run_id"], rule_id=r["rule_id"], rule_type=r["rule_type"],
                finding_key=r["finding_key"], severity=r["severity"],
                status=r["status"], metric=r["metric"], locators=r["locators"],
                witness_index=r["witness_index"], witness_digest=r["witness_digest"],
                bbox_w=r["bbox"][0], bbox_s=r["bbox"][1],
                bbox_e=r["bbox"][2], bbox_n=r["bbox"][3],
                geometry_type=r["geometry_type"], message=r["message"],
                resolved_by_run_id=r.get("resolved_by_run_id"),
                exception_id=r.get("exception_id"),
            )
            for r in findings_rows
        ])
    db.commit()

    return {
        "status": "cancelled" if cancelled else "completed",
        "run_id": run.id,
        "findings_total": run.findings_total,
        "findings_open": run.findings_open,
        "rule_results": run.rule_results,
        "completeness": run.completeness,
    }


def _bbox_of_gj(gj: Dict[str, Any]) -> Tuple[float, float, float, float]:
    xs: List[float] = []
    ys: List[float] = []

    def _walk(obj: Any) -> None:
        if isinstance(obj, (int, float)):
            return
        if isinstance(obj, (list, tuple)):
            if len(obj) == 2 and all(isinstance(v, (int, float)) for v in obj):
                xs.append(float(obj[0]))
                ys.append(float(obj[1]))
            else:
                for o in obj:
                    _walk(o)

    _walk(gj.get("coordinates"))
    if not xs or not ys:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _put_witness_blob(raw: str) -> Tuple[str, str]:
    """witness JSON → BlobStore 内容寻址 blob（put-if-absent，幂等）。"""
    import hashlib

    from app.services.durable_blob_store import get_filesystem_blob_store

    data = raw.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    store = get_filesystem_blob_store()
    store.put_blob(digest, data, "json")
    return digest, digest


def _load_active_exceptions(db: Any, ruleset_id: str) -> List[Any]:
    from sqlalchemy import select as sa_select

    from app.models.topology_workbench import TopologyException

    return list(db.execute(
        sa_select(TopologyException).where(
            TopologyException.ruleset_id == ruleset_id,
            TopologyException.status == "active",
        )
    ).scalars().all())


def _mark_exception_stale(db: Any, exc: Any, reason: str) -> None:
    exc.status = "stale"
    anchor = dict(exc.anchor or {})
    anchor["stale_reason"] = reason
    exc.anchor = anchor


__all__ = ["execute_topology_run", "compute_input_digest", "RunExecutionError"]
