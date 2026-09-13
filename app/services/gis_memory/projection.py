"""SpatialMemory → 模型面投影（R5）：有界 ``[GIS_MEMORY]`` 块。

纪律（与 [CARTOGRAPHY_MEMORY]/[CARTOGRAPHY_VERDICT] 同门）：
- 注入的是**先验而非证据**——块头声明，模型不得把它当本轮事实；
- 有界字符预算 + 省略留痕；
- sensitive 行在检索层已被剔除，本层只做渲染；
- 每行带来源与新鲜度（可审计、可被模型自我校准）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from app.services.gis_memory.contract import (
    KIND_ANALYSIS_ARTIFACT,
    KIND_CRS_RESOLUTION,
    KIND_DATASET_SEMANTICS,
    KIND_FIELD_ROLE,
    KIND_PROVIDER_FAILURE,
    KIND_RESOLVED_PLACE,
    KIND_SUCCESSFUL_STRATEGY,
    RetrievedMemory,
)

MEMORY_MARKER = "GIS_MEMORY"
MEMORY_BLOCK_CHAR_BUDGET = 1100

_KIND_LABEL = {
    KIND_RESOLVED_PLACE: "地理范围",
    KIND_DATASET_SEMANTICS: "数据集语义",
    KIND_FIELD_ROLE: "字段角色",
    KIND_CRS_RESOLUTION: "CRS 结论",
    KIND_ANALYSIS_ARTIFACT: "分析产物",
    KIND_SUCCESSFUL_STRATEGY: "成功策略",
    KIND_PROVIDER_FAILURE: "已知失败路径",
}


def _age_text(last_validated_at: Optional[str], now: datetime) -> str:
    if not last_validated_at:
        return "时间未知"
    try:
        validated = datetime.fromisoformat(str(last_validated_at))
    except ValueError:
        return "时间未知"
    if validated.tzinfo:
        validated = validated.replace(tzinfo=None)
    age_days = max((now - validated).total_seconds(), 0.0) / 86400.0
    if age_days < 1.0:
        return "今日验证"
    if age_days < 30.0:
        return f"{age_days:.0f} 天前验证"
    return "较久前验证"


def _render_line(hit: RetrievedMemory, now: datetime) -> Optional[str]:
    rec = hit.record
    label = _KIND_LABEL.get(rec.kind)
    if label is None:
        return None
    value = rec.value if isinstance(rec.value, dict) else {}
    if rec.kind == KIND_RESOLVED_PLACE:
        detail = (
            f"{value.get('name', rec.subject)}"
            f"（{value.get('level', '?')}）"
        )
        bbox = value.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            detail += f" bbox≈[{bbox[0]:.2f},{bbox[1]:.2f},{bbox[2]:.2f},{bbox[3]:.2f}]"
    elif rec.kind in (KIND_DATASET_SEMANTICS, KIND_FIELD_ROLE):
        parts = []
        for key, tag in (("time_field", "时间字段"), ("roles", "角色"), ("fields", "字段")):
            if value.get(key):
                parts.append(f"{tag} {value[key]}")
        detail = f"{rec.subject}" + ("：" + "；".join(parts) if parts else "")
    elif rec.kind == KIND_PROVIDER_FAILURE:
        detail = f"{rec.subject}：{value.get('failure_class', '失败')}（避免重蹈，可直接换路径）"
    elif rec.kind == KIND_SUCCESSFUL_STRATEGY:
        detail = f"{rec.subject}（上次产出过通过评审的结果，可作起点）"
    elif rec.kind == KIND_CRS_RESOLUTION:
        detail = f"{rec.subject}：{value.get('crs', '未知')}"
    elif rec.kind == KIND_ANALYSIS_ARTIFACT:
        refs = "、".join(rec.refs[:2]) if rec.refs else rec.subject
        detail = f"{refs}"
    else:
        detail = rec.subject
    return (
        f"- {label} · {detail}"
        f"（来源 {rec.evidence.get('source', '?')}，"
        f"置信 {rec.confidence:.2f}，{_age_text(rec.last_validated_at, now)}）"
    )


def render_memory_block(
    hits: List[RetrievedMemory],
    *,
    char_budget: int = MEMORY_BLOCK_CHAR_BUDGET,
    now: Optional[datetime] = None,
) -> str:
    """有界 ``[GIS_MEMORY]`` 块；空命中返回空串（不注入空块）。"""
    if not hits:
        return ""
    current = now or datetime.now(timezone.utc).replace(tzinfo=None)
    lines: List[str] = []
    for hit in hits:
        line = _render_line(hit, current)
        if line:
            lines.append(line)
    if not lines:
        return ""
    header = (
        f"[{MEMORY_MARKER}] 以往会话确认的 GIS 事实先验（复用起点，不是本轮"
        "证据——与当前数据/用户表述冲突时以当前为准）：\n"
    )
    ellipsis = "- …（更多记忆已按预算省略）"
    body: List[str] = []
    used = len(header)
    for index, line in enumerate(lines):
        reserve = len(ellipsis) + 1 if index < len(lines) - 1 else 0
        if used + len(line) + 1 + reserve > char_budget:
            body.append(ellipsis)
            break
        body.append(line)
        used += len(line) + 1
    return header + "\n".join(body) + "\n"


__all__ = [
    "MEMORY_MARKER",
    "MEMORY_BLOCK_CHAR_BUDGET",
    "render_memory_block",
]
