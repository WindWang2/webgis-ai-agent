"""时间窗口 → 过滤表达式/指纹编译器（GOAL 03 M2）。

一个帧窗口 [start, end) 在**三种消费面**上由同一编译器产出：
- where AST（服务端 aggregate / features 分页通道 —— feature_pages 词表）；
- MapLibre filter 表达式（前端渲染面 —— 与 selectionFilters 同语法）；
- time_filter_fingerprint（地图/图表/表格同窗断言的锚点）。

科学性前提（与 frames._detect_representation 联合保证）：
- 字符串表示仅在全列同格式+同偏移下被接受，故字典序 = 时间序；
- 数值表示（epoch_s/epoch_ms/year_int）按数值比较；
- 边界值按 time_representation 归一到**数据原始值空间**——比较语义与
  数据存储一致，绝不把字符串年份解释成毫秒。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

WhereAst = Dict[str, Any]


def _parse_iso(instant: str) -> datetime:
    """契约 ISO instant（Z 或偏移）→ aware datetime。"""
    cleaned = instant.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def normalize_boundary_value(
    instant: str, representation: str, granularity: str
) -> Union[int, str]:
    """契约 instant → 数据表示空间的比较值。

    - year_int：边界年份整数；
    - epoch_s / epoch_ms：边界 epoch 整数（秒/毫秒）；
    - date_string：与数据同精度的定宽字符串（year→"YYYY"、month→
      "YYYY-MM"、其余→"YYYY-MM-DD"）。定宽 + 零填充 ⇒ 字典序 = 时间序；
    - iso_string：与数据同构的 ISO 字符串（秒精度，Z 偏移 —— 探测已保证
      数据列统一偏移；若数据偏移非 Z，bind 已在 format 层对齐，此处渲染
      Z 与数据的**相对序**仍一致仅当数据同为 Z；混合偏移在探测期被拒）。
    """
    dt = _parse_iso(instant)
    if representation == "year_int":
        return dt.year
    if representation == "epoch_s":
        return int(dt.timestamp())
    if representation == "epoch_ms":
        return int(dt.timestamp() * 1000)
    if representation == "date_string":
        if granularity == "year":
            return f"{dt.year:04d}"
        if granularity == "month" or granularity == "quarter":
            return f"{dt.year:04d}-{dt.month:02d}"
        return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
    # iso_string：渲染与探测格式同构（秒级 ISO，UTC）。
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compile_where_ast(
    frame: Dict[str, Any],
    time_field: str,
    representation: str,
    granularity: str,
) -> WhereAst:
    """帧窗口 [start, end) → feature_pages where AST。

    词表严格对齐 app/services/feature_pages._LEAF_OPS（gte/lt + and）——
    复用既有服务端过滤通道，零端点协议改动（D2）。
    """
    lo = normalize_boundary_value(frame["start"], representation, granularity)
    hi = normalize_boundary_value(frame["end"], representation, granularity)
    return {
        "op": "and",
        "args": [
            {"op": "gte", "field": time_field, "value": lo},
            {"op": "lt", "field": time_field, "value": hi},
        ],
    }


def compile_maplibre_filter(
    frame: Dict[str, Any],
    time_field: str,
    representation: str,
    granularity: str,
) -> List[Any]:
    """帧窗口 → MapLibre filter 表达式（["all", [">=",…], ["<",…]]）。

    与 selectionFilters 同语法（map-panel 既有应用面）；值与 where AST
    完全同源 —— 地图与统计同窗的结构性保证。
    """
    lo = normalize_boundary_value(frame["start"], representation, granularity)
    hi = normalize_boundary_value(frame["end"], representation, granularity)
    return [
        "all",
        [">=", ["get", time_field], lo],
        ["<", ["get", time_field], hi],
    ]


def time_filter_fingerprint(
    source_ref: str,
    content_revision: Optional[int],
    time_field: str,
    frame_id: str,
    representation: str,
) -> str:
    """时间过滤指纹 —— 地图/图表/表格/导出共用的同窗锚点。

    纳入 ref + content_revision：数据内容世代变化时旧帧指纹失效
    （stale 检测），不会把旧窗冒充新窗。
    """
    payload = "|".join([
        source_ref,
        str(content_revision if content_revision is not None else ""),
        time_field,
        frame_id,
        representation,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def union_window(frame_a: Dict[str, Any], frame_b: Dict[str, Any]) -> Dict[str, Any]:
    """两帧 → 覆盖两者的最小 [start,end) 窗（shared classification 的取值域）。"""
    start = min(frame_a["start"], frame_b["start"])
    end = max(frame_a["end"], frame_b["end"])
    return {"start": start, "end": end, "id": f"union:{frame_a['id']}+{frame_b['id']}"}


def window_where_ast(
    start: str,
    end: str,
    time_field: str,
    representation: str,
    granularity: str,
) -> WhereAst:
    """任意 [start,end) 窗口（非帧）→ where AST（union 窗等场景）。"""
    return compile_where_ast(
        {"start": start, "end": end}, time_field, representation, granularity
    )


def equal_interval_breaks(
    vmin: float, vmax: float, k: int = 5
) -> Optional[List[float]]:
    """[vmin, vmax] → k 段等距 breaks（含两端，长度 k+1）。

    退化域（vmin == vmax 或非有限值）返回 None —— 调用方必须以 typed
    方式披露「无法固定分级」，而不是发明 0 宽分级。
    """
    import math

    if not math.isfinite(vmin) or not math.isfinite(vmax) or vmax <= vmin:
        return None
    step = (vmax - vmin) / k
    return [round(vmin + i * step, 6) for i in range(k)] + [round(vmax, 6)]


def parse_frame_labels(frames: List[Dict[str, Any]]) -> List[str]:
    """帧 label 列表（前端时间轴顺序展示用；契约已保证升序）。"""
    return [f["label"] for f in frames]
