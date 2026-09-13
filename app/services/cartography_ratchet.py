"""制图质量 ratchet 门禁（任务书 10 线 P2，ADR-0159）。

「只许变好」闸：新 run 的「图型 × 检查项」观测不得劣于入库基线。

- 基线取**分位数**（默认 p66）而非均值，抗离群；
- 方向语义：``high_bad``（越大越糟：load_ratio / label_ink_ratio /
  encoded_field_count…）与 ``low_bad``（越小越糟：min_adjacent_delta_e /
  avg_feature_area_px / gate 得分…）；无法判定方向的检查项兜底 high_bad；
- 劣化容差默认 ±5%（``CARTO_RATCHET_TOLERANCE_PCT`` 可配，CLI 可覆盖）；
- ``provisional`` 基线（首轮无历史）只记录不拦截，显式激活后才拦截；
- waiver 带理由 + 到期日，写入库、报告中高亮、到期自动失效；
- 查询基线先按 scene_id 精确匹配，再回退 ``"*"`` 全局基线。

本模块只读观测、产出裁决 —— 不改任何检查的判定逻辑；P6 校准脚本把
建议值作为基线入库，也不改现有检查的硬编码默认值。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

GLOBAL_SCOPE = "*"

#: 规则级方向表（check_id 前缀匹配）
_RULE_HIGH_BAD = (
    "carto.load.ratio",
    "carto.label.collision_est",
    "carto.visualvar.overload",
)
_RULE_LOW_BAD = (
    "carto.color.separability",
    "carto.scale.svs",
    "carto.legend.completeness",
)
#: 证据键级覆盖（优先于规则表）
_EVIDENCE_LOW_BAD = ("min_adjacent_delta_e", "avg_feature_area_px")
#: 无方向表的兜底
_DEFAULT_DIRECTION = "high_bad"


def resolve_direction(check_id: str) -> str:
    """判定检查项的劣化方向；未知检查项兜底 high_bad（保守拦截）。"""
    text = str(check_id or "")
    for key in _EVIDENCE_LOW_BAD:
        if text.endswith(f".{key}") or text == key:
            return "low_bad"
    if text.startswith("gate.") or text.startswith("eval."):
        # 维度/评估得分：越高越好。
        return "low_bad"
    for prefix in _RULE_LOW_BAD:
        if text.startswith(prefix):
            return "low_bad"
    for prefix in _RULE_HIGH_BAD:
        if text.startswith(prefix):
            return "high_bad"
    return _DEFAULT_DIRECTION


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """线性插值分位（与 calibrate_cartography_thresholds 同款）。"""
    if not sorted_values:
        return float("nan")
    position = q * (len(sorted_values) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(sorted_values) - 1)
    frac = position - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac


@dataclass(frozen=True)
class Observation:
    """一次观测：(图型 scope, 检查项, 数值)。value=None 的行不参与 ratchet。"""

    scene_id: str
    check_id: str
    value: float


@dataclass(frozen=True)
class Baseline:
    scene_id: str
    check_id: str
    value: float
    quantile: float = 0.66
    direction: str = "high_bad"
    tolerance_pct: float = 5.0
    status: str = "provisional"
    sample_n: int = 0


@dataclass
class RatchetViolation:
    scene_id: str
    check_id: str
    baseline_value: float
    observed_value: float
    delta_pct: float
    direction: str
    waived: bool = False
    waiver_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "check_id": self.check_id,
            "baseline_value": self.baseline_value,
            "observed_value": self.observed_value,
            "delta_pct": round(self.delta_pct, 3),
            "direction": self.direction,
            "waived": self.waived,
            "waiver_reason": self.waiver_reason,
        }


def _group_finite_rows(rows: Iterable[Any]) -> Dict[Tuple[str, str], List[float]]:
    """观测行 → (scene, check) → 有限数值列表（bool/非有限/空键剔除）。"""
    grouped: Dict[Tuple[str, str], List[float]] = {}
    for row in rows:
        value = row.get("value") if isinstance(row, dict) else None
        if value is None or isinstance(value, bool):
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(numeric):
            continue
        scene = str(row.get("scene_id") or GLOBAL_SCOPE)
        check = str(row.get("check_id") or "")
        if not check:
            continue
        grouped.setdefault((scene, check), []).append(numeric)
    return grouped


def aggregate_observations(
    rows: Iterable[Any],
    quantile: float = 0.66,
) -> List[Observation]:
    """把 run 观测行（dict 形态：scene_id/check_id/value）聚合成
    「图型 × 检查项」一条分位观测 —— 多样本同键取分位，抗离群。"""
    grouped = _group_finite_rows(rows)
    return [
        Observation(scene_id=scene, check_id=check, value=percentile(sorted(values), quantile))
        for (scene, check), values in sorted(grouped.items())
    ]


def _scene_scoped_baseline(
    baselines: Sequence[Baseline], scene_id: str, check_id: str
) -> Optional[Baseline]:
    scoped = {
        (b.scene_id, b.check_id): b for b in baselines
    }
    exact = scoped.get((scene_id, check_id))
    if exact is not None:
        return exact
    return scoped.get((GLOBAL_SCOPE, check_id))


def _is_regression(
    direction: str, baseline_value: float, observed: float, tolerance_pct: float
) -> bool:
    if tolerance_pct < 0:
        tolerance_pct = 0.0
    factor = tolerance_pct / 100.0
    if direction == "low_bad":
        return observed < baseline_value * (1.0 - factor)
    return observed > baseline_value * (1.0 + factor)


def _delta_pct(direction: str, baseline_value: float, observed: float) -> float:
    if baseline_value == 0:
        return 0.0 if observed == 0 else math.inf
    delta = (observed - baseline_value) / abs(baseline_value) * 100.0
    # low_bad 的"劣化幅度"取负方向量（更小才是更糟）。
    return delta if direction == "high_bad" else -delta


def evaluate_ratchet(
    observations: Sequence[Observation],
    baselines: Sequence[Baseline],
    waivers: Sequence[Dict[str, Any]] = (),
    tolerance_pct: float = 5.0,
) -> List[RatchetViolation]:
    """对照基线裁决观测。只对 ``active`` 基线拦截；waiver 未到期的命中豁免。"""
    now = datetime.now(timezone.utc)
    active = [b for b in baselines if b.status == "active"]
    violations: List[RatchetViolation] = []
    for obs in observations:
        baseline = _scene_scoped_baseline(active, obs.scene_id, obs.check_id)
        if baseline is None:
            continue
        direction = baseline.direction or resolve_direction(obs.check_id)
        # 基线自带容差优先（显式 0 = 零容差，不得回退到调用方默认）。
        tolerance = (
            baseline.tolerance_pct
            if baseline.tolerance_pct is not None
            else tolerance_pct
        )
        if not _is_regression(direction, baseline.value, obs.value, tolerance):
            continue
        violation = RatchetViolation(
            scene_id=obs.scene_id,
            check_id=obs.check_id,
            baseline_value=baseline.value,
            observed_value=obs.value,
            delta_pct=_delta_pct(direction, baseline.value, obs.value),
            direction=direction,
        )
        for waiver in waivers:
            if str(waiver.get("check_id")) != obs.check_id:
                continue
            w_scene = str(waiver.get("scene_id") or GLOBAL_SCOPE)
            if w_scene not in (GLOBAL_SCOPE, obs.scene_id):
                continue
            expires_at = waiver.get("expires_at")
            if isinstance(expires_at, datetime) and expires_at < now:
                continue  # 到期自动失效（naive/aware 混存由入库方统一 UTC）
            violation.waived = True
            violation.waiver_reason = str(waiver.get("reason") or "")
            break
        violations.append(violation)
    return violations


def build_baseline_entries(
    rows: Iterable[Any],
    quantile: float = 0.66,
    tolerance_pct: float = 5.0,
) -> List[Baseline]:
    """从观测行构建基线条目（provisional，待显式激活）。"""
    grouped = _group_finite_rows(rows)
    entries: List[Baseline] = []
    for (scene, check), values in sorted(grouped.items()):
        ordered = sorted(values)
        entries.append(Baseline(
            scene_id=scene,
            check_id=check,
            value=percentile(ordered, quantile),
            quantile=quantile,
            direction=resolve_direction(check),
            tolerance_pct=tolerance_pct,
            status="provisional",
            sample_n=len(ordered),
        ))
    return entries


# ── DB 侧（async，与 metrics store 同一套 AsyncSessionLocal） ─────────────


async def write_baselines(
    entries: Sequence[Baseline],
    *,
    status: Optional[str] = None,
    source: str = "first_run",
) -> int:
    """按 (scene, check) upsert 基线；返回写入条数。绝不抛给调用方。"""
    if not entries:
        return 0
    try:
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.cartography_quality import CartographyQualityBaseline

        if AsyncSessionLocal is None:
            return 0
        written = 0
        async with AsyncSessionLocal() as db:
            for entry in entries:
                existing = (
                    await db.execute(
                        select(CartographyQualityBaseline).where(
                            CartographyQualityBaseline.scene_id == entry.scene_id,
                            CartographyQualityBaseline.check_id == entry.check_id,
                        )
                    )
                ).scalar_one_or_none()
                row = existing or CartographyQualityBaseline(
                    scene_id=entry.scene_id, check_id=entry.check_id,
                )
                row.value = entry.value
                row.quantile = entry.quantile
                row.direction = entry.direction
                row.tolerance_pct = entry.tolerance_pct
                row.status = status or entry.status
                row.source = source
                row.sample_n = entry.sample_n
                db.add(row)
                written += 1
            await db.commit()
        return written
    except Exception:  # noqa: BLE001 — 基线写失败不阻塞门禁主流程
        logger.debug("ratchet: write_baselines failed", exc_info=True)
        return 0


async def load_baselines() -> List[Baseline]:
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.cartography_quality import CartographyQualityBaseline

    if AsyncSessionLocal is None:
        return []
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(select(CartographyQualityBaseline))
        ).scalars().all()
    return [
        Baseline(
            scene_id=row.scene_id,
            check_id=row.check_id,
            value=row.value,
            quantile=row.quantile,
            direction=row.direction,
            tolerance_pct=row.tolerance_pct,
            status=row.status,
            sample_n=row.sample_n,
        )
        for row in rows
    ]


async def activate_baselines(check_ids: Optional[Sequence[str]] = None) -> int:
    """provisional → active（首次建档后的人工/脚本显式动作）。"""
    from sqlalchemy import update

    from app.core.database import AsyncSessionLocal
    from app.models.cartography_quality import CartographyQualityBaseline

    if AsyncSessionLocal is None:
        return 0
    async with AsyncSessionLocal() as db:
        stmt = (
            update(CartographyQualityBaseline)
            .where(CartographyQualityBaseline.status == "provisional")
        )
        if check_ids:
            stmt = stmt.where(
                CartographyQualityBaseline.check_id.in_(list(check_ids))
            )
        result = await db.execute(stmt.values(status="active"))
        await db.commit()
        return int(result.rowcount or 0)


async def add_waiver(
    check_id: str,
    reason: str,
    days: int = 30,
    scene_id: str = GLOBAL_SCOPE,
    created_by: str = "",
) -> bool:
    """登记豁免：带理由 + 到期日；到期由 evaluate_ratchet 自动失效。"""
    from app.core.database import AsyncSessionLocal
    from app.models.cartography_quality import CartographyQualityWaiver

    if AsyncSessionLocal is None:
        return False
    async with AsyncSessionLocal() as db:
        db.add(CartographyQualityWaiver(
            scene_id=scene_id,
            check_id=check_id,
            reason=str(reason)[:500],
            created_by=str(created_by)[:128],
            expires_at=datetime.now(timezone.utc) + timedelta(days=max(1, days)),
        ))
        await db.commit()
    return True


async def load_active_waivers() -> List[Dict[str, Any]]:
    """未到期的豁免（过期行仍留在库里作审计，查询面自动过滤）。"""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.cartography_quality import CartographyQualityWaiver

    if AsyncSessionLocal is None:
        return []
    cutoff = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(CartographyQualityWaiver).where(
                    CartographyQualityWaiver.expires_at >= cutoff
                )
            )
        ).scalars().all()
    return [
        {
            "scene_id": row.scene_id,
            "check_id": row.check_id,
            "reason": row.reason,
            "created_by": row.created_by,
            "expires_at": (
                row.expires_at
                if row.expires_at.tzinfo is not None
                else row.expires_at.replace(tzinfo=timezone.utc)
            ),
        }
        for row in rows
    ]


async def collect_observation_rows(
    lanes: Sequence[str] = ("desired_state", "runtime"),
    since_runs: int = 60,
) -> List[Dict[str, Any]]:
    """从事实库取最近 N 次 run 的 metric 行（ratchet 评价面）。"""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.cartography_quality import (
        CartographyQualityMetric,
        CartographyQualityRun,
    )

    if AsyncSessionLocal is None:
        return []
    async with AsyncSessionLocal() as db:
        run_rows = (
            await db.execute(
                select(
                    CartographyQualityRun.id,
                    CartographyQualityRun.scene_id,
                )
                .where(CartographyQualityRun.lane.in_(list(lanes)))
                .order_by(CartographyQualityRun.ts.desc())
                .limit(max(1, int(since_runs)))
            )
        ).all()
        if not run_rows:
            return []
        metric_rows = (
            await db.execute(
                select(
                    CartographyQualityMetric.run_id,
                    CartographyQualityMetric.check_id,
                    CartographyQualityMetric.value,
                ).where(
                    CartographyQualityMetric.run_id.in_([r.id for r in run_rows])
                )
            )
        ).all()
    scene_by_run: Dict[str, str] = {
        r.id: (r.scene_id or GLOBAL_SCOPE) for r in run_rows
    }
    return [
        {
            "scene_id": scene_by_run.get(row.run_id, GLOBAL_SCOPE),
            "check_id": row.check_id,
            "value": row.value,
        }
        for row in metric_rows
    ]


# ── 同步入口（CLI 脚本侧；与 metrics store 的 *_sync 同款） ────────────────


def _run_coro(coro):
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "sync ratchet helpers cannot be called inside a running loop"
    )


def collect_observation_rows_sync(
    lanes: Sequence[str] = ("desired_state", "runtime"),
    since_runs: int = 60,
) -> List[Dict[str, Any]]:
    return _run_coro(collect_observation_rows(lanes=lanes, since_runs=since_runs))


def write_baselines_sync(
    entries: Sequence[Baseline],
    *,
    status: Optional[str] = None,
    source: str = "first_run",
) -> int:
    return _run_coro(write_baselines(entries, status=status, source=source))


def load_baselines_sync() -> List[Baseline]:
    return _run_coro(load_baselines())


def load_active_waivers_sync() -> List[Dict[str, Any]]:
    return _run_coro(load_active_waivers())


def add_waiver_sync(
    check_id: str,
    reason: str,
    days: int = 30,
    scene_id: str = GLOBAL_SCOPE,
    created_by: str = "",
) -> bool:
    return _run_coro(add_waiver(
        check_id=check_id, reason=reason, days=days,
        scene_id=scene_id, created_by=created_by,
    ))


def activate_baselines_sync(check_ids: Optional[Sequence[str]] = None) -> int:
    return _run_coro(activate_baselines(check_ids))
