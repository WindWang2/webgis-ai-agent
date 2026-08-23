"""分布漂移检测与制图环境事件（spec P3 / ADR-0069）。

具身语义里最便宜也最真实的一块：**环境独立于 agent 变化**。制图语境下这
个变化就是**数值分布漂移**——分类断点本质是分布的函数，数据刷新后旧断点
对新分布不再成立，即使 agent 什么都没做。

零外部依赖：漂移信号来自系统自身已有的数据变更流（上传/重新摄取产出新的
Spatial Meta Profile），不需要传感器或实时数据源。

纯函数在此，DB 副作用经 ``project_memory.mark_stale`` 落地。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


def _threshold(name: str, default: float) -> float:
    """Operator-tunable threshold (spec open question 1), resolved at import.

    Same rationale as semantic_checks._carto_threshold: the detector stays a
    pure function of (held, incoming, thresholds); config is injected once.
    """
    try:
        from app.core.config import settings

        value = getattr(settings, name, None)
        return float(value) if value is not None else default
    except Exception:  # noqa: BLE001
        return default


#: 分位向量相对偏差阈值：超过即判定漂移（默认 15%，可运维调参）。
DRIFT_RELATIVE_THRESHOLD = _threshold("CARTO_DRIFT_RELATIVE_THRESHOLD", 0.15)

#: 空值率绝对变化阈值：列的“空洞程度”独立于分布形状变化。
NULL_RATIO_ABS_THRESHOLD = _threshold("CARTO_DRIFT_NULL_RATIO_THRESHOLD", 0.10)

#: [ENV_CHANGE] 注入块预算，与记忆块同量级。
ENV_CHANGE_CHAR_BUDGET = 600

ENV_CHANGE_MARKER = "ENV_CHANGE"


def distribution_fingerprint(field_profile: Optional[Dict[str, Any]]) -> Optional[str]:
    """数值字段的分布指纹（分位向量 + 空值率）。

    非数值/无分位证据 → ``None``：不可判定就不假装可判定（fail-closed），
    调用方据此跳过漂移判定而不是当作“未漂移”。
    """
    if not isinstance(field_profile, dict):
        return None
    quantiles = field_profile.get("quantiles")
    if not isinstance(quantiles, list) or not quantiles:
        return None
    payload = {
        "quantiles": [round(float(q), 6) for q in quantiles if _is_number(q)],
        "null_ratio": round(float(field_profile.get("null_ratio") or 0.0), 4),
    }
    if not payload["quantiles"]:
        return None
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def detect_distribution_drift(
    held: Optional[Dict[str, Any]],
    incoming: Optional[Dict[str, Any]],
    *,
    relative_threshold: float = DRIFT_RELATIVE_THRESHOLD,
    null_ratio_threshold: float = NULL_RATIO_ABS_THRESHOLD,
) -> Dict[str, Any]:
    """比较两份分布证据，返回判定结果。

    ``held``/``incoming`` 形态是 ``{"quantiles": [...], "null_ratio": float}``
    （字段 profile 的投影或事实 payload）。返回：

    - ``evaluated=False``：任一侧缺分位证据 → 不可判定（既不判漂移也不判
      稳定），调用方必须保持既有状态不动；
    - ``drifted``：分位向量最大相对偏差超阈值，或空值率绝对变化超阈值；
    - ``max_relative_deviation`` / ``null_ratio_delta``：可审计的证据。

    相对偏差以两侧分位的量级为分母（``max(|held|, |incoming|, eps)``），
    所以“0→0.001”不会因除零变成无穷大漂移，而“1000→1200”能被抓到。
    """
    held_q = _quantile_vector(held)
    incoming_q = _quantile_vector(incoming)
    if not held_q or not incoming_q or len(held_q) != len(incoming_q):
        return {
            "evaluated": False,
            "drifted": False,
            "reason": "insufficient_distribution_evidence",
        }
    span = max(
        max(held_q) - min(held_q),
        max(incoming_q) - min(incoming_q),
    )
    max_dev = 0.0
    for a, b in zip(held_q, incoming_q):
        denominator = max(abs(a), abs(b), span, 1e-9)
        max_dev = max(max_dev, abs(a - b) / denominator)
    held_null = float((held or {}).get("null_ratio") or 0.0)
    incoming_null = float((incoming or {}).get("null_ratio") or 0.0)
    null_delta = abs(incoming_null - held_null)
    drifted = max_dev > relative_threshold or null_delta > null_ratio_threshold
    return {
        "evaluated": True,
        "drifted": bool(drifted),
        "max_relative_deviation": round(max_dev, 6),
        "null_ratio_delta": round(null_delta, 6),
        "thresholds": {
            "relative": relative_threshold,
            "null_ratio": null_ratio_threshold,
        },
        "reason": (
            "quantile_shift" if max_dev > relative_threshold
            else "null_ratio_shift" if null_delta > null_ratio_threshold
            else "stable"
        ),
    }


def _quantile_vector(evidence: Optional[Dict[str, Any]]) -> List[float]:
    if not isinstance(evidence, dict):
        return []
    quantiles = evidence.get("quantiles")
    if not isinstance(quantiles, list):
        return []
    return [float(q) for q in quantiles if _is_number(q)]


def distribution_evidence(field_profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """从字段 profile 提取可存储/可比较的分布证据投影。"""
    if not isinstance(field_profile, dict):
        return None
    quantiles = _quantile_vector(field_profile)
    if not quantiles:
        return None
    return {
        "quantiles": quantiles,
        "null_ratio": round(float(field_profile.get("null_ratio") or 0.0), 6),
    }


def render_env_change_block(
    events: Sequence[Dict[str, Any]],
    *,
    char_budget: int = ENV_CHANGE_CHAR_BUDGET,
) -> str:
    """把漂移事件渲染为有界 ``[ENV_CHANGE]`` 块（不含原始数据）。

    每条事件形态：``{"subject": str, "reason": str, "deviation": float}``。
    空事件返回空串（不注入空块）。
    """
    if not events:
        return ""
    header = (
        f"[{ENV_CHANGE_MARKER}] 数据环境已变化，以下先验已过期，请重新确认"
        f"后再复用：\n"
    )
    ellipsis = "- …（更多环境变化已按预算省略）"
    lines: List[str] = []
    used = len(header)
    rendered = [_render_event(event) for event in events]
    rendered = [line for line in rendered if line]
    for index, line in enumerate(rendered):
        reserve = len(ellipsis) + 1 if index < len(rendered) - 1 else 0
        if used + len(line) + 1 + reserve > char_budget:
            lines.append(ellipsis)
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return header + "\n".join(lines) + "\n"


def _render_event(event: Dict[str, Any]) -> str:
    if not isinstance(event, dict) or not event.get("subject"):
        return ""
    reason = {
        "quantile_shift": "数值分布已偏移",
        "null_ratio_shift": "空值比例已变化",
    }.get(str(event.get("reason")), "分布证据已变化")
    deviation = event.get("deviation")
    detail = (
        f"（偏差 {float(deviation):.0%}）" if _is_number(deviation) else ""
    )
    return f"- 字段 {event['subject']}: {reason}{detail}，原分类断点已过期"


__all__ = [
    "DRIFT_RELATIVE_THRESHOLD",
    "NULL_RATIO_ABS_THRESHOLD",
    "ENV_CHANGE_CHAR_BUDGET",
    "ENV_CHANGE_MARKER",
    "distribution_fingerprint",
    "distribution_evidence",
    "detect_distribution_drift",
    "render_env_change_block",
]
