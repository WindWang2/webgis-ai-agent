"""轨迹指标 → 既有 ratchet / 质量事实库（B7，ADR-0183 决策五 / D9）。

**零新表、零新指标判定**：重放观测行（``[{scene_id, check_id, value}]``）
直接流入 ADR-0159 基座 ——

- 基线：``cartography_ratchet.build_baseline_entries``（provisional-first，
  绝不自动激活）→ ``scripts/quality_ratchet_gate.py baseline --from-json``；
- 裁决：``evaluate_ratchet``（方向词表沿用：``gate.*`` = 得分 low_bad、
  ``replay.*`` = 兜底 high_bad）；
- 落账：``cartography_metrics_store.record_quality_run(lane="replay")``
  （fire-and-forget；CARTO_METRICS_STORE_ENABLED 总闸照常生效）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from app.lib.harness.replay.replayer import ScenarioResult

logger = logging.getLogger(__name__)

REPLAY_LANE = "replay"
REPLAY_SOURCE = "replay_bench"


def rows_from_results(results: Iterable[ScenarioResult]) -> List[Dict[str, Any]]:
    """重放结果 → ratchet 观测行（scene_id = scenario#tN）。"""
    rows: List[Dict[str, Any]] = []
    for result in results:
        rows.extend(result.metrics_rows)
    return rows


def baseline_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """quality_ratchet_gate.py ``baseline --from-json`` 直读形态。"""
    return [
        {"scene_id": str(r.get("scene_id")), "check_id": str(r.get("check_id")),
         "value": float(r["value"])}
        for r in rows
        if isinstance(r.get("value"), (int, float))
        and not isinstance(r.get("value"), bool)
    ]


def evaluate_replay_observations(
    rows: Iterable[Dict[str, Any]],
    baselines: Iterable[Any],
    waivers: Iterable[Dict[str, Any]] = (),
    tolerance_pct: float = 5.0,
) -> List[Any]:
    """行契约 → ``evaluate_ratchet``（只对 active 基线拦截）。"""
    from app.services.cartography_ratchet import Observation, evaluate_ratchet

    observations = [
        Observation(scene_id=str(r["scene_id"]), check_id=str(r["check_id"]),
                    value=float(r["value"]))
        for r in rows
        if isinstance(r.get("value"), (int, float))
        and not isinstance(r.get("value"), bool)
    ]
    return evaluate_ratchet(observations, list(baselines), list(waivers),
                            tolerance_pct=tolerance_pct)


def degrade_rows(rows: Iterable[Dict[str, Any]], *,
                 gate_factor: float = 0.5,
                 replay_factor: float = 3.0) -> List[Dict[str, Any]]:
    """故意劣化（intentional degradation，证明 gate 会红）：

    - ``gate.*``（越高越好）→ 得分 × ``gate_factor``（默认腰斩）；
    - ``replay.*``（越高越糟）→ 值 × ``replay_factor``（默认 3 倍）。
    """
    degraded: List[Dict[str, Any]] = []
    for row in rows:
        value = float(row["value"])
        if row["check_id"].startswith("gate."):
            value *= gate_factor
        else:
            value *= replay_factor
        degraded.append({**row, "value": value})
    return degraded


async def record_replay_run(
    rows: Iterable[Dict[str, Any]], *,
    session_id: Optional[str] = None,
    scene_id: Optional[str] = None,
    passed: Optional[bool] = None,
    summary: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """重放 run 入质量事实库（lane="replay"）。绝不抛出（照录 fail 面）。"""
    from app.services.cartography_metrics_store import record_quality_run

    try:
        return await record_quality_run(
            lane=REPLAY_LANE,
            source=REPLAY_SOURCE,
            checks=list(rows),
            session_id=session_id,
            scene_id=scene_id,
            passed=passed,
            summary=summary,
        )
    except Exception:  # noqa: BLE001 — 账本故障绝不阻塞 bench
        logger.debug("replay ratchet: record failed", exc_info=True)
        return None


def results_summary(results: Iterable[ScenarioResult]) -> Dict[str, Any]:
    """bench 汇总（gate_scores 邻接形态，供 quality run summary）。"""
    items = list(results)
    return {
        "scenarios": len(items),
        "green": sum(1 for r in items if r.ok),
        "red": sum(1 for r in items if not r.ok),
        "levels": sorted({lv for r in items for lv in r.levels_run}),
    }
