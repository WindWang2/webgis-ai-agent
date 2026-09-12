"""Data Quality V9 —— 评估引擎（同步小数据集 + durable job 大数据集双路径）。

P1（任务书 §2）编排面：

- :func:`evaluate_payload` —— 纯内存评估（同步小数据集路径；有界扫描）；
- :func:`persist_report` —— 报告 + 逐规则行落库（``quality_reports`` /
  ``quality_rule_results``，migration 0046）；
- :func:`run_quality_evaluate` —— durable job 执行体（worker 侧调用；
  幂等：同 (session, ref, ruleset) 存在 completed 报告时复用）。

红线：

- 评估绝不改写源数据（修复走 autofix 显式管线，new-ref 语义）；
- 落库行有界（≤64 结果行 / metric 投影截断），大载荷走 job result_ref；
- 规则异常单条隔离（error 状态），绝不让一条规则炸掉整场评估。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

from app.services.data_quality import metrics as dq_metrics
from app.services.data_quality.rule_functions import (
    RULE_FUNCTIONS,
    RULE_INPUTS,
    RuleEvalContext,
    RuleOutcome,
)
from app.services.data_quality.rules import (
    DEFAULT_RULES,
    RuleSpec,
    parse_rule_defs,
    ruleset_digest,
)

logger = logging.getLogger(__name__)

#: 单场评估落库的逐规则行上限（超出部分并入 summary.truncated_results）。
_MAX_RESULT_ROWS = 64
#: 同步路径默认扫描上限（大数据集应走 durable job 路径）。
_DEFAULT_MAX_SCAN = 20000

_OVERALL_RANK = {"fail": 2, "warn": 1, "pass": 0}


def _payload_fingerprint(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _extract_inputs(payload: Dict[str, Any]) -> tuple[str, List[Dict[str, Any]], Dict[str, Any], str]:
    """payload → (kind, features, raster_stats, crs)。诚实识别，不猜。"""
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是 dict")
    geojson = payload.get("geojson")
    raster_stats = payload.get("raster_stats")
    crs = str(payload.get("crs") or "")
    if isinstance(raster_stats, dict) and raster_stats:
        return "raster", [], raster_stats, str(raster_stats.get("crs") or crs)
    if isinstance(geojson, dict) and geojson.get("type") == "FeatureCollection":
        features = geojson.get("features")
        if not isinstance(features, list):
            features = []
        fc_crs = ""
        if isinstance(geojson.get("crs"), dict):
            fc_crs = str(geojson["crs"].get("properties", {}).get("name") or "")
        return "vector", features, {}, crs or fc_crs
    if isinstance(geojson, dict) and "features" in geojson:
        # 容错：无 type 声明但有 features 的形状（诚实标注 table 口径）
        features = geojson.get("features")
        return "table", features if isinstance(features, list) else [], {}, crs
    raise ValueError("payload 需含 geojson FeatureCollection 或 raster_stats")


def _applies_to_kind(spec: RuleSpec, kind: str) -> bool:
    inputs = RULE_INPUTS.get(spec.rule_type, ("features",))
    if inputs == ("raster_stats",):
        return kind == "raster"
    if inputs == ("crs",):
        return True
    return kind in ("vector", "table")


def evaluate_payload(
    payload: Dict[str, Any],
    rule_defs: Optional[List[Any]] = None,
    *,
    max_scan: int = _DEFAULT_MAX_SCAN,
) -> Dict[str, Any]:
    """纯内存评估 → 有界报告 dict（不落库；持久化见 :func:`persist_report`）。

    ``rule_defs`` 缺席 = 内置默认规则集；dict/YAML 节点经 parse_rule_defs。
    """
    specs: List[RuleSpec] = (
        parse_rule_defs(rule_defs) if rule_defs else list(DEFAULT_RULES)
    )
    kind, features, raster_stats, crs = _extract_inputs(payload)
    started = time.perf_counter()
    results: List[Dict[str, Any]] = []
    diagnostics: List[str] = []

    for spec in specs:
        if not spec.enabled:
            continue
        if not _applies_to_kind(spec, kind):
            results.append({
                "rule_id": spec.rule_id, "rule_type": spec.rule_type,
                "severity": spec.severity, "status": "skipped",
                "message": f"not_applicable:{kind}", "affected_count": 0,
                "metric": {}, "autofixable": False, "fix_operations": [],
                "duration_ms": 0,
            })
            continue
        fn = RULE_FUNCTIONS.get(spec.rule_type)
        if fn is None:  # 词表与注册表漂移防御（import 期另有 assert 兜底）
            diagnostics.append(f"no_impl:{spec.rule_type}")
            continue
        ctx = RuleEvalContext(
            kind=kind, features=features, raster_stats=raster_stats,
            crs=crs, params=dict(spec.params), max_scan=max_scan,
        )
        t0 = time.perf_counter()
        try:
            outcome = fn(ctx)
            error = None
        except Exception as exc:  # noqa: BLE001 — 单规则隔离（诚实 error 态）
            outcome = RuleOutcome(status="error", message=f"rule raised: {type(exc).__name__}")
            error = str(exc)[:200]
        duration_ms = int((time.perf_counter() - t0) * 1000)
        dq_metrics.observe_rule(spec.rule_id, outcome.status, duration_ms)
        entry = {
            "rule_id": spec.rule_id,
            "rule_type": spec.rule_type,
            "severity": spec.severity,
            "status": outcome.status,
            "message": outcome.message[:500],
            "affected_count": int(outcome.affected_count),
            "metric": outcome.bounded_metric(),
            "autofixable": bool(outcome.autofixable),
            "fix_operations": [str(o) for o in outcome.fix_operations],
            "duration_ms": duration_ms,
        }
        if error:
            entry["metric"]["error"] = error
        results.append(entry)

    duration_ms = int((time.perf_counter() - started) * 1000)
    counted = [r for r in results if r["status"] in ("pass", "warn", "fail", "error")]
    failed = sum(1 for r in results if r["status"] in ("fail", "error"))
    warned = sum(1 for r in results if r["status"] == "warn")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    worst = "pass"
    for r in counted:
        st = "fail" if r["status"] == "error" else r["status"]
        if _OVERALL_RANK.get(st, 0) > _OVERALL_RANK.get(worst, 0):
            worst = st
    report: Dict[str, Any] = {
        "target_kind": kind,
        "feature_count": len(features),
        "overall_status": worst,
        "rule_count": len(counted),
        "failed_count": failed,
        "warn_count": warned,
        "skipped_count": skipped,
        "duration_ms": duration_ms,
        "results": results[:_MAX_RESULT_ROWS],
        "truncated_results": max(0, len(results) - _MAX_RESULT_ROWS),
        "diagnostics": diagnostics[:16],
    }
    if rule_defs:
        report["ruleset_digest"] = ruleset_digest(specs)
    else:
        report["ruleset_digest"] = ruleset_digest(list(DEFAULT_RULES))
    report["dataset_identity"] = _payload_fingerprint(payload)
    return report


# ── 落库 ─────────────────────────────────────────────────────────────


def persist_report(
    db: Any,
    report: Dict[str, Any],
    *,
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    created_by: Optional[str] = None,
    target_ref: str = "",
    job_id: Optional[str] = None,
    org_id: Optional[int] = None,
) -> Any:
    """报告 dict → QualityReport + QualityRuleResult 行（有界）。"""
    from app.models.data_quality import QualityReport, QualityRuleResult

    row = QualityReport(
        org_id=org_id,
        project_id=(str(project_id)[:255] if project_id else None),
        session_id=(str(session_id)[:255] if session_id else None),
        created_by=(str(created_by)[:255] if created_by else None),
        target_ref=str(target_ref or "")[:255],
        target_kind=str(report.get("target_kind") or "vector")[:16],
        dataset_identity=str(report.get("dataset_identity") or "")[:128],
        ruleset_digest=str(report.get("ruleset_digest") or "")[:64],
        status="completed",
        overall_status=str(report.get("overall_status") or "pass")[:16],
        rule_count=int(report.get("rule_count") or 0),
        failed_count=int(report.get("failed_count") or 0),
        warn_count=int(report.get("warn_count") or 0),
        skipped_count=int(report.get("skipped_count") or 0),
        duration_ms=int(report.get("duration_ms") or 0),
        summary={
            "feature_count": int(report.get("feature_count") or 0),
            "truncated_results": int(report.get("truncated_results") or 0),
            "top_failures": [
                {"rule_id": r["rule_id"], "status": r["status"],
                 "message": r["message"][:200]}
                for r in (report.get("results") or [])
                if r.get("status") in ("fail", "error")
            ][:8],
        },
        diagnostics=[str(d)[:128] for d in (report.get("diagnostics") or [])][:16],
        job_id=(str(job_id)[:64] if job_id else None),
    )
    db.add(row)
    db.flush()
    for r in (report.get("results") or [])[:_MAX_RESULT_ROWS]:
        db.add(QualityRuleResult(
            report_id=row.id,
            rule_id=str(r.get("rule_id") or "")[:64],
            rule_type=str(r.get("rule_type") or "")[:48],
            severity=str(r.get("severity") or "warn")[:16],
            status=str(r.get("status") or "pass")[:16],
            message=str(r.get("message") or "")[:500],
            affected_count=int(r.get("affected_count") or 0),
            duration_ms=int(r.get("duration_ms") or 0),
            metric=r.get("metric") or {},
            autofixable=bool(r.get("autofixable")),
            fix_operations=[str(o)[:32] for o in (r.get("fix_operations") or [])][:8],
        ))
    db.commit()
    return row


# ── durable job 执行体（worker 侧；也可在测试中直接调用） ─────────────


def run_quality_evaluate(
    job_id: int,
    *,
    session_id: str,
    ref: str,
    project_id: Optional[str] = None,
    rule_defs: Optional[List[Any]] = None,
    max_scan: int = 200000,
    created_by: Optional[str] = None,
    org_id: Optional[int] = None,
) -> Dict[str, Any]:
    """大数据集评估执行体：session ref 载荷 → 评估 → 落库 → job 回写。

    - **恢复语义**：入口先查同 (session, ref, ruleset) 的 completed 报告，
      命中则复用（幂等，worker 重试不重复评估）；
    - 载荷不可读 → 落 failed 报告行（status=failed）+ job 置失败；
    - job 回写经 DurableJobStore（progress/result_ref），但 store 异常
      不吞评估结果 —— 报告已落库，job 状态由 stale 清扫兜底。
    """
    from app.core.database import SessionLocal
    from app.models.data_quality import QualityReport
    from sqlalchemy import select

    ruleset = ruleset_digest(
        parse_rule_defs(rule_defs) if rule_defs else list(DEFAULT_RULES)
    )
    with SessionLocal() as db:
        existing = db.execute(
            select(QualityReport)
            .where(
                QualityReport.session_id == (str(session_id)[:255] if session_id else None),
                QualityReport.target_ref == str(ref)[:255],
                QualityReport.ruleset_digest == ruleset,
                QualityReport.status == "completed",
            )
            .order_by(QualityReport.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            dq_metrics.observe_reuse()
            return {
                "status": "completed", "reused": True,
                "report_id": existing.id,
                "overall_status": existing.overall_status,
            }

    # 载荷读取（session 域；失败 → failed 报告 + job 失败）
    import asyncio

    from app.services.session_data import session_data_manager

    async def _load() -> Any:
        return await session_data_manager.get(session_id, ref)

    try:
        payload_raw = asyncio.run(_load())
    except Exception as exc:  # noqa: BLE001
        with SessionLocal() as db:
            row = QualityReport(
                org_id=org_id,
                project_id=(str(project_id)[:255] if project_id else None),
                session_id=str(session_id)[:255],
                created_by=(str(created_by)[:255] if created_by else None),
                target_ref=str(ref)[:255],
                status="failed",
                overall_status="fail",
                diagnostics=[f"payload_unreadable: {type(exc).__name__}"],
                job_id=str(job_id)[:64] if job_id else None,
            )
            db.add(row)
            db.commit()
            report_id = row.id
        if job_id:
            _mark_job_failed(job_id, f"payload unreadable: {type(exc).__name__}")
        return {"status": "failed", "report_id": report_id}

    if payload_raw is None:
        # ref 缺失（store 语义：不存在返回 None）→ 诚实失败，不虚构评估
        with SessionLocal() as db:
            row = QualityReport(
                org_id=org_id,
                project_id=(str(project_id)[:255] if project_id else None),
                session_id=str(session_id)[:255],
                created_by=(str(created_by)[:255] if created_by else None),
                target_ref=str(ref)[:255],
                status="failed",
                overall_status="fail",
                diagnostics=["payload_unreadable: ref_not_found"],
                job_id=str(job_id)[:64] if job_id else None,
            )
            db.add(row)
            db.commit()
            report_id = row.id
        if job_id:
            _mark_job_failed(job_id, "payload unreadable: ref not found")
        return {"status": "failed", "report_id": report_id}

    # 载荷归一化：session ref 既可能存信封 {"geojson": ...}，也可能直接存
    # FeatureCollection / 栅格 dict —— 两种都接受（存的是什么就是什么）。
    if isinstance(payload_raw, dict) and ("geojson" in payload_raw or "raster_stats" in payload_raw):
        payload = payload_raw
    else:
        payload = {"geojson": payload_raw}
    try:
        report = evaluate_payload(payload, rule_defs, max_scan=max_scan)
    except Exception as exc:  # noqa: BLE001 — 评估器自身异常也落 failed 报告
        logger.error("quality evaluate failed job=%s: %s", job_id, exc)
        with SessionLocal() as db:
            row = QualityReport(
                org_id=org_id,
                project_id=(str(project_id)[:255] if project_id else None),
                session_id=str(session_id)[:255],
                created_by=(str(created_by)[:255] if created_by else None),
                target_ref=str(ref)[:255],
                status="failed",
                overall_status="fail",
                diagnostics=[f"evaluate_error: {type(exc).__name__}: {exc}"[:400]],
                job_id=str(job_id)[:64] if job_id else None,
            )
            db.add(row)
            db.commit()
            report_id = row.id
        if job_id:
            _mark_job_failed(job_id, str(exc)[:200])
        return {"status": "failed", "report_id": report_id}

    with SessionLocal() as db:
        row = persist_report(
            db, report,
            project_id=project_id, session_id=session_id,
            created_by=created_by, target_ref=ref, job_id=job_id, org_id=org_id,
        )
        report_id = row.id
    if job_id:
        _mark_job_completed(job_id, report_id, report.get("overall_status") or "pass")
    dq_metrics.observe_job_completed(str(report.get("overall_status") or "pass"))
    return {
        "status": "completed", "reused": False, "report_id": report_id,
        "overall_status": report.get("overall_status"),
    }


def _mark_job_completed(job_id: int, report_id: Any, overall: str) -> None:
    try:
        from app.core.database import SessionLocal
        from app.services.jobs.lifecycle import JobStatus
        from app.services.jobs.store import DurableJobStore

        with SessionLocal() as db:
            job = DurableJobStore.get_sync(db, int(job_id))
            if job is None:
                return
            DurableJobStore.transition_sync(
                db, int(job_id), JobStatus.completed, expected=None,
            )
            job.result_ref = f"data-quality:report:{report_id}"
            job.result_summary = {"report_id": str(report_id), "overall_status": overall}
            db.commit()
    except Exception:  # noqa: BLE001 — job 回写失败不吞评估结果（stale 清扫兜底）
        logger.warning("quality job %s completion writeback failed", job_id, exc_info=True)


def _mark_job_failed(job_id: int, message: str) -> None:
    try:
        from app.core.database import SessionLocal
        from app.services.jobs.store import DurableJobStore

        with SessionLocal() as db:
            DurableJobStore.mark_failed_sync(db, int(job_id), error=message,
                                             message="quality evaluate failed")
            db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("quality job %s failure writeback failed", job_id, exc_info=True)


__all__ = [
    "evaluate_payload",
    "persist_report",
    "run_quality_evaluate",
]
