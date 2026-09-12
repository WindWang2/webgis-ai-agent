"""制图质量事实库 —— 写入 / 趋势查询 / 保留策略（ADR-0159，任务书 10 线 P1）。

落库点是**产出面的下游账本**：``_cartographic_review``（runtime lane）、
desired-state 评审（mapspec_store 提交面）、``HarnessEvaluator``（eval lane）
在产出处调用 :func:`record_quality_run`，只加写入钩子、不改判定逻辑。
写入永不抛出、永不阻塞主流程：账本故障不能反过来杀死评审本身。

- SQLite 与 PostGIS 双兼容（与既有模型同风格；删除语句不用方言私有的
  ``LIMIT -1``，age/count 两条保留路径都是双方言 SQL）；
- 证据缺失诚实落 ``value=NULL`` —— 缺观测 ≠ 0 分（与闭环"无证据 ≠ 成功"
  同一语义）；
- 所有投影有界：逐检查项数值字段封顶、逐 run 行数封顶、summary 截断，
  大载荷绝不入行；
- 保留策略默认 90 天 / 5000 run（可配 ``CARTO_METRICS_RETENTION_DAYS`` /
  ``CARTO_METRICS_MAX_RUNS``）。

其他 9 条自适应线的产出处（03/05/06/07/08）按同一契约调用
:func:`record_quality_run` 即可入账 —— 本模块就是那条契约。
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.core.config import settings
from app.models.cartography_quality import (  # noqa: F401 — 顶层注册进 Base.metadata，
    # 使 init_db()/create_all 与 alembic autogenerate 都能看到这两张表
    # （本模块被 cartography_runtime 顶层 import，早于任何启动建表路径）。
    CartographyQualityMetric,
    CartographyQualityRun,
)

logger = logging.getLogger(__name__)

#: 单检查项最多落多少个数值观测（证据字典里偶发的大表不进账本）
_MAX_NUMERIC_PER_CHECK = 8
#: 单 run 最多落多少行 metric（超出截断并在 summary 记录溢出计数）
_MAX_METRICS_PER_RUN = 96
#: summary 投影边界
_MAX_SUMMARY_KEYS = 24
_MAX_SUMMARY_STR = 200

_VALID_VERDICTS = ("pass", "fail", "warning", "not_evaluated")

_RUNS_TABLE = "cartography_quality_runs"
_METRICS_TABLE = "cartography_quality_metrics"


def _app_version() -> str:
    """进程版本横轴：优先 Settings.APP_VERSION，退回仓库 VERSION 文件。"""
    version = str(getattr(settings, "APP_VERSION", "") or "")
    if version:
        return version[:64]
    try:
        from pathlib import Path

        version_file = Path(__file__).resolve().parents[2] / "VERSION"
        return version_file.read_text(encoding="utf-8").strip()[:64]
    except Exception:  # noqa: BLE001 — 版本横轴取不到就用空串
        return ""


def numeric_observations(evidence: Any) -> Dict[str, float]:
    """从单个 check 的 ``evidence`` dict 提取有界数值观测。

    只收有限数值（bool 不算数）；键截到 80 字符；最多
    :data:`_MAX_NUMERIC_PER_CHECK` 个。任何形态不合法都安静跳过。
    """
    out: Dict[str, float] = {}
    if not isinstance(evidence, dict):
        return out
    for key, value in evidence.items():
        if len(out) >= _MAX_NUMERIC_PER_CHECK:
            break
        if not isinstance(key, str) or not key:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(numeric):
            continue
        out[key[:80]] = numeric
    return out


def _normalize_verdict(status: Any) -> str:
    text = str(status or "").lower()
    return text if text in _VALID_VERDICTS else "not_evaluated"


def _bounded_summary(summary: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(summary, dict) or not summary:
        return None
    bounded: Dict[str, Any] = {}
    for key, value in list(summary.items())[:_MAX_SUMMARY_KEYS]:
        if isinstance(value, str):
            bounded[str(key)[:80]] = value[:_MAX_SUMMARY_STR]
        elif isinstance(value, bool) or isinstance(value, (int, float)):
            bounded[str(key)[:80]] = value
        else:
            bounded[str(key)[:80]] = str(value)[:_MAX_SUMMARY_STR]
    return bounded


def _rows_from_checks(checks: Iterable[Any]) -> List[Dict[str, Any]]:
    """把评审 checks（CartographyCheck.to_dict() 形态或同形 dict）转 metric 行。"""
    rows: List[Dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        check_id = str(check.get("rule") or check.get("check") or "")[:128]
        if not check_id:
            continue
        verdict = _normalize_verdict(check.get("status"))
        evidence_class = str(
            check.get("evidence_class") or "deterministic"
        )[:16]
        observations = numeric_observations(check.get("evidence"))
        if observations:
            for key, value in observations.items():
                rows.append({
                    "check_id": f"{check_id}.{key}"[:128],
                    "value": value,
                    "evidence_class": evidence_class,
                    "verdict": verdict,
                })
        else:
            # 无数值观测的检查项也落一行 verdict —— 判定本身是事实。
            rows.append({
                "check_id": check_id,
                "value": None,
                "evidence_class": evidence_class,
                "verdict": verdict,
            })
        if len(rows) >= _MAX_METRICS_PER_RUN:
            break
    return rows[:_MAX_METRICS_PER_RUN]


async def record_quality_run(
    *,
    lane: str,
    source: str,
    checks: Iterable[Any] = (),
    session_id: Optional[str] = None,
    scene_id: Optional[str] = None,
    passed: Optional[bool] = None,
    summary: Optional[Dict[str, Any]] = None,
    gate_scores: Optional[Dict[str, float]] = None,
) -> Optional[str]:
    """落一次质量 run；返回 run_id，失败/停用时返回 None（绝不抛出）。

    ``gate_scores`` 是 HarnessEvaluator 形态的维度得分（{name: score}），
    与 checks 行同账本落库（check_id = 维度名，verdict 按阈值判定由调用方
    归一后传 checks，或此处一律 pass 记值）。
    """
    if not getattr(settings, "CARTO_METRICS_STORE_ENABLED", True):
        return None
    try:
        return await _record_quality_run_inner(
            lane=lane,
            source=source,
            checks=checks,
            session_id=session_id,
            scene_id=scene_id,
            passed=passed,
            summary=summary,
            gate_scores=gate_scores,
        )
    except Exception:  # noqa: BLE001 — 账本故障绝不阻塞评审主流程
        logger.debug("cartography quality store: record failed", exc_info=True)
        return None


async def _record_quality_run_inner(
    *,
    lane: str,
    source: str,
    checks: Iterable[Any],
    session_id: Optional[str],
    scene_id: Optional[str],
    passed: Optional[bool],
    summary: Optional[Dict[str, Any]],
    gate_scores: Optional[Dict[str, float]],
) -> Optional[str]:
    from app.core.database import AsyncSessionLocal

    if AsyncSessionLocal is None:
        return None
    rows = _rows_from_checks(checks)
    if gate_scores:
        remaining = _MAX_METRICS_PER_RUN - len(rows)
        for name, score in list(gate_scores.items())[: max(0, remaining)]:
            try:
                value = float(score)
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(value):
                continue
            rows.append({
                "check_id": f"gate.{str(name)[:120]}"[:128],
                "value": value,
                "evidence_class": "deterministic",
                "verdict": "not_evaluated",
            })
    if not rows and passed is None and not summary:
        return None

    run = CartographyQualityRun(
        app_version=_app_version(),
        session_id=(str(session_id)[:255] if session_id else None),
        scene_id=(str(scene_id)[:128] if scene_id else None),
        lane=str(lane or "runtime")[:20],
        source=str(source or "")[:64],
        passed=passed,
        summary=_bounded_summary(summary),
    )
    run.metrics = [
        CartographyQualityMetric(
            check_id=row["check_id"],
            value=row["value"],
            evidence_class=row["evidence_class"],
            verdict=row["verdict"],
        )
        for row in rows
    ]
    async with AsyncSessionLocal() as db:
        db.add(run)
        await db.commit()
        run_id = run.id
    await maybe_enforce_retention()
    return run_id


async def query_quality_trend(
    check_id: str,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """按检查项检索最近观测（join run 取 ts/lane/scene/version），时间升序。

    ``check_id`` 支持前缀匹配（``carto.load.ratio`` 会同时命中
    ``carto.load.ratio.load_ratio``）—— 检查项落库时是 ``rule.evidence_key``
    复合键，趋势消费者视角是"这条规则的历史"。
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    if AsyncSessionLocal is None:
        return []
    stmt = (
        select(
            CartographyQualityMetric.check_id,
            CartographyQualityMetric.value,
            CartographyQualityMetric.verdict,
            CartographyQualityMetric.evidence_class,
            CartographyQualityRun.ts,
            CartographyQualityRun.lane,
            CartographyQualityRun.scene_id,
            CartographyQualityRun.session_id,
            CartographyQualityRun.app_version,
            CartographyQualityRun.passed,
        )
        .join(
            CartographyQualityRun,
            CartographyQualityMetric.run_id == CartographyQualityRun.id,
        )
        .where(CartographyQualityMetric.check_id.like(f"{check_id}%"))
        .order_by(CartographyQualityRun.ts.asc(), CartographyQualityMetric.id.asc())
        .limit(max(1, min(int(limit), 5000)))
    )
    if since is not None:
        stmt = stmt.where(CartographyQualityRun.ts >= since)
    if until is not None:
        stmt = stmt.where(CartographyQualityRun.ts <= until)
    async with AsyncSessionLocal() as db:
        result = await db.execute(stmt)
        rows = result.all()
    return [
        {
            "check_id": row.check_id,
            "value": row.value,
            "verdict": row.verdict,
            "evidence_class": row.evidence_class,
            "ts": row.ts,
            "lane": row.lane,
            "scene_id": row.scene_id,
            "session_id": row.session_id,
            "app_version": row.app_version,
            "passed": row.passed,
        }
        for row in rows
    ]


async def latest_runs(n: int = 30) -> List[Dict[str, Any]]:
    """最近 n 次 run（含逐 metric 行），新→旧。验收口径：最近 30 次可检索。"""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    if AsyncSessionLocal is None:
        return []
    async with AsyncSessionLocal() as db:
        run_rows = (
            await db.execute(
                select(CartographyQualityRun)
                .order_by(CartographyQualityRun.ts.desc())
                .limit(max(1, min(int(n), 1000)))
            )
        ).scalars().all()
        if not run_rows:
            return []
        metric_rows = (
            await db.execute(
                select(CartographyQualityMetric)
                .where(
                    CartographyQualityMetric.run_id.in_(
                        [r.id for r in run_rows]
                    )
                )
                .order_by(CartographyQualityMetric.id.asc())
            )
        ).scalars().all()
    by_run: Dict[str, List[Dict[str, Any]]] = {}
    for metric in metric_rows:
        by_run.setdefault(metric.run_id, []).append({
            "check_id": metric.check_id,
            "value": metric.value,
            "verdict": metric.verdict,
            "evidence_class": metric.evidence_class,
        })
    return [
        {
            "run_id": run.id,
            "ts": run.ts,
            "lane": run.lane,
            "source": run.source,
            "scene_id": run.scene_id,
            "session_id": run.session_id,
            "app_version": run.app_version,
            "passed": run.passed,
            "summary": run.summary,
            "metrics": by_run.get(run.id, []),
        }
        for run in run_rows
    ]


async def maybe_enforce_retention() -> Dict[str, int]:
    """保留策略：默认 90 天 / 5000 run（可配）。返回删除行数（诊断用）。

    双方言删除：先按 run 子查询删 metrics，再删 runs 本体 —— 不依赖
    SQLite 默认关闭的外键级联，也不用 PG 专有语法。
    """
    from sqlalchemy import delete, func, select

    from app.core.database import AsyncSessionLocal
    if AsyncSessionLocal is None:
        return {"expired_runs": 0, "overflow_runs": 0}
    max_age_days = int(getattr(settings, "CARTO_METRICS_RETENTION_DAYS", 90))
    max_runs = int(getattr(settings, "CARTO_METRICS_MAX_RUNS", 5000))
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    deleted = {"expired_runs": 0, "overflow_runs": 0}
    async with AsyncSessionLocal() as db:
        expired_ids = (
            (
                await db.execute(
                    select(CartographyQualityRun.id).where(
                        CartographyQualityRun.ts < cutoff
                    )
                )
            ).scalars().all()
        )
        if expired_ids:
            await db.execute(
                delete(CartographyQualityMetric).where(
                    CartographyQualityMetric.run_id.in_(expired_ids)
                )
            )
            await db.execute(
                delete(CartographyQualityRun).where(
                    CartographyQualityRun.id.in_(expired_ids)
                )
            )
            deleted["expired_runs"] = len(expired_ids)
        total = (
            await db.execute(select(func.count()).select_from(CartographyQualityRun))
        ).scalar_one()
        if total > max_runs:
            # 超出保留容量的最旧 run：保留容量边界行的 ts（双方言参数化
            # OFFSET —— PG 与 SQLite 都允许省略 LIMIT 的 OFFSET 子句）。
            boundary = (
                await db.execute(
                    select(CartographyQualityRun.ts)
                    .order_by(CartographyQualityRun.ts.desc())
                    .offset(max_runs)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if boundary is not None:
                overflow_ids = (
                    (
                        await db.execute(
                            select(CartographyQualityRun.id).where(
                                CartographyQualityRun.ts <= boundary
                            )
                        )
                    ).scalars().all()
                )
                if overflow_ids:
                    await db.execute(
                        delete(CartographyQualityMetric).where(
                            CartographyQualityMetric.run_id.in_(overflow_ids)
                        )
                    )
                    await db.execute(
                        delete(CartographyQualityRun).where(
                            CartographyQualityRun.id.in_(overflow_ids)
                        )
                    )
                    deleted["overflow_runs"] = len(overflow_ids)
        await db.commit()
    return deleted


def ensure_tables() -> None:
    """脚本侧建表护栏（sync engine，create_all-coexistence guard 同款）。

    供 ``quality_trend_report.py`` / ``calibrate --write`` / ratchet 脚本在
    独立进程里使用：幂等，只建缺失表，不碰既有 schema。
    """
    from app.core.database import Base, Engine
    from app.models.cartography_quality import (  # noqa: F401 — 确保四张表注册
        CartographyQualityBaseline,
        CartographyQualityMetric,
        CartographyQualityRun,
        CartographyQualityWaiver,
    )

    Base.metadata.create_all(
        bind=Engine,
        tables=[
            Base.metadata.tables[_RUNS_TABLE],
            Base.metadata.tables[_METRICS_TABLE],
            Base.metadata.tables["cartography_quality_baselines"],
            Base.metadata.tables["cartography_quality_waivers"],
        ],
    )


def record_quality_run_sync(
    *,
    lane: str,
    source: str,
    checks: Iterable[Any] = (),
    session_id: Optional[str] = None,
    scene_id: Optional[str] = None,
    passed: Optional[bool] = None,
    summary: Optional[Dict[str, Any]] = None,
    gate_scores: Optional[Dict[str, float]] = None,
) -> Optional[str]:
    """同步写入入口（脚本 / 无事件循环上下文）。语义与 async 版一致。"""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        raise RuntimeError(
            "record_quality_run_sync cannot be called inside a running loop; "
            "use record_quality_run"
        )
    return asyncio.run(
        record_quality_run(
            lane=lane,
            source=source,
            checks=checks,
            session_id=session_id,
            scene_id=scene_id,
            passed=passed,
            summary=summary,
            gate_scores=gate_scores,
        )
    )


def query_quality_trend_sync(
    check_id: str,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """同步趋势查询（脚本侧）。行形态与 async 版一致。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.database import settings as _settings
    sync_url = str(_settings.DATABASE_URL)
    engine = create_engine(sync_url, connect_args={"check_same_thread": False}) \
        if sync_url.startswith("sqlite") else create_engine(sync_url)
    Session = sessionmaker(bind=engine)
    rows: List[Dict[str, Any]] = []
    try:
        with Session() as db:
            stmt = (
                db.query(
                    CartographyQualityMetric.check_id,
                    CartographyQualityMetric.value,
                    CartographyQualityMetric.verdict,
                    CartographyQualityMetric.evidence_class,
                    CartographyQualityRun.ts,
                    CartographyQualityRun.lane,
                    CartographyQualityRun.scene_id,
                    CartographyQualityRun.app_version,
                    CartographyQualityRun.passed,
                )
                .join(
                    CartographyQualityRun,
                    CartographyQualityMetric.run_id == CartographyQualityRun.id,
                )
                .filter(CartographyQualityMetric.check_id.like(f"{check_id}%"))
            )
            if since is not None:
                stmt = stmt.filter(CartographyQualityRun.ts >= since)
            if until is not None:
                stmt = stmt.filter(CartographyQualityRun.ts <= until)
            for row in stmt.order_by(
                CartographyQualityRun.ts.asc(), CartographyQualityMetric.id.asc()
            ).limit(max(1, min(int(limit), 5000))).all():
                rows.append({
                    "check_id": row.check_id,
                    "value": row.value,
                    "verdict": row.verdict,
                    "evidence_class": row.evidence_class,
                    "ts": row.ts,
                    "lane": row.lane,
                    "scene_id": row.scene_id,
                    "app_version": row.app_version,
                    "passed": row.passed,
                })
    finally:
        engine.dispose()
    return rows


def utc_now() -> datetime:
    return datetime.fromtimestamp(time.time(), tz=timezone.utc)
