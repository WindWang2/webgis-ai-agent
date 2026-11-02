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


def render_iso_like(dt: "datetime", format_hint: str) -> str:
    """按数据字面形状渲染 ISO 边界（R2-N1 修正）。

    format_hint = 探测期样例值：偏移族（Z / 无 / ±HH:MM）与小数秒宽度
    随数据渲染 —— 边界串与数据串**同构**，字典序 = 时间序在任何统一
    形状下成立（naive 渲染 naive、+08:00 渲染 +08:00、.123 渲染 .123）。
    """
    import re as _re
    from datetime import timedelta as _td

    frac_width = 0
    m = _re.search(r"\.(\d+)(Z|[+-]\d{2}:?\d{2})?$", format_hint)
    if m:
        frac_width = len(m.group(1))
        offset = m.group(2) or ""
    else:
        tail = _re.search(r"(Z|[+-]\d{2}:?\d{2})$", format_hint)
        # 空 hint 缺省 Z 语义（契约帧 instant 本就是 Z 口径）。
        offset = tail.group(1) if tail else ("Z" if not format_hint else "")

    local = dt
    if offset and offset != "Z":
        sign = -1 if offset[0] == "-" else 1
        hh = int(offset[1:3])
        mm = int(offset[-2:]) if len(offset) > 3 else 0
        local = dt.astimezone(timezone(sign * _td(hours=hh, minutes=mm)))

    base = local.strftime("%Y-%m-%dT%H:%M:%S")
    if frac_width:
        base += "." + f"{local.microsecond:06d}"[:frac_width]
    if offset == "Z":
        return base + "Z"
    return base + offset


def normalize_boundary_value(
    instant: str, representation: str, granularity: str, tz_name: str = "UTC",
    format_hint: str = "",
) -> Union[int, str]:
    """契约 instant → 数据表示空间的比较值（契约时区口径）。

    日历字段（年/月/日）一律先转到**契约时区**再提取（P0 修正：帧桶在
    契约时区划分，归一若取 UTC 字段会把「2023」帧错配成 2022 数据 ——
    时区是数据锚定的解释参数，两次转换不得丢它）。
    - year_int：契约时区年份整数；
    - epoch_s / epoch_ms：epoch 数值（时区无关，绝对时间）；
    - date_string：与数据同精度的定宽字符串（year→"YYYY"、month→
      "YYYY-MM"、其余→"YYYY-MM-DD"）。定宽 + 零填充 ⇒ 字典序 = 时间序；
    - iso_string：探测只接受统一 Z 偏移（frames._detect_representation），
      故渲染 Z 与数据同构。
    """
    from zoneinfo import ZoneInfo

    dt = _parse_iso(instant)
    if representation == "year_int":
        return dt.astimezone(ZoneInfo(tz_name)).year
    if representation == "epoch_s":
        return int(dt.timestamp())
    if representation == "epoch_ms":
        return int(dt.timestamp() * 1000)
    if representation == "date_string":
        local = dt.astimezone(ZoneInfo(tz_name))
        if granularity == "year":
            return f"{local.year:04d}"
        if granularity == "month" or granularity == "quarter":
            return f"{local.year:04d}-{local.month:02d}"
        return f"{local.year:04d}-{local.month:02d}-{local.day:02d}"
    # iso_string：按数据形状渲染（R2-N1：naive/偏移/小数秒同构）。
    return render_iso_like(dt, format_hint)


def compile_where_ast(
    frame: Dict[str, Any],
    time_field: str,
    representation: str,
    granularity: str,
    tz_name: str = "UTC",
    format_hint: str = "",
) -> WhereAst:
    """帧窗口 [start, end) → feature_pages where AST。

    词表严格对齐 app/services/feature_pages（叶算子 gte/lt，组合节点
    ``terms`` —— 与 evaluate_where/parse_where_param 同语法）—— 复用既有
    服务端过滤通道，零端点协议改动（D2）。
    """
    lo = normalize_boundary_value(frame["start"], representation, granularity, tz_name, format_hint)
    hi = normalize_boundary_value(frame["end"], representation, granularity, tz_name, format_hint)
    return {
        "op": "and",
        "terms": [
            {"op": "gte", "field": time_field, "value": lo},
            {"op": "lt", "field": time_field, "value": hi},
        ],
    }


def compile_maplibre_filter(
    frame: Dict[str, Any],
    time_field: str,
    representation: str,
    granularity: str,
    tz_name: str = "UTC",
    format_hint: str = "",
) -> List[Any]:
    """帧窗口 → MapLibre filter 表达式（["all", [">=",…], ["<",…]]）。

    与 selectionFilters 同语法（map-panel 既有应用面）；值与 where AST
    完全同源 —— 地图与统计同窗的结构性保证。
    """
    lo = normalize_boundary_value(frame["start"], representation, granularity, tz_name, format_hint)
    hi = normalize_boundary_value(frame["end"], representation, granularity, tz_name, format_hint)
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
    tz_name: str = "UTC",
    format_hint: str = "",
) -> WhereAst:
    """任意 [start,end) 窗口（非帧）→ where AST（union 窗等场景）。"""
    return compile_where_ast(
        {"start": start, "end": end}, time_field, representation, granularity,
        tz_name, format_hint,
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
    breaks = [round(vmin + i * step, 6) for i in range(k)] + [round(vmax, 6)]
    # 舍入可能把极小值域压成相等 breaks（schema 严格递增会拒）—— 视为退化。
    if any(b <= a for a, b in zip(breaks, breaks[1:])):
        return None
    return breaks


def parse_frame_labels(frames: List[Dict[str, Any]]) -> List[str]:
    """帧 label 列表（前端时间轴顺序展示用；契约已保证升序）。"""
    return [f["label"] for f in frames]


def apply_time_window_to_doc(
    doc: Dict[str, Any], time_window: Dict[str, Any]
) -> Dict[str, Any]:
    """atlas time 页有效文档：绑定层内联要素按 [start,end) 过滤 + 页帧 cursor 盖章。

    纯函数（触达路径浅拷，与 _page_filtered_doc 同纪律）：
    - 过滤值经 normalize_boundary_value 归一到数据表示空间（与服务端
      where AST 同语义）；
    - ``temporal.cursor_frame`` 盖为本页帧 → SVG 时间注记显示本页期间；
    - 无匹配要素 → 空集（诚实空页，不借用相邻帧）。
    """
    field = str(time_window.get("time_field") or "")
    representation = str(time_window.get("time_representation") or "")
    granularity = str(time_window.get("granularity") or "")
    layer_id = str(time_window.get("layer_id") or "")
    tz_name = str(time_window.get("timezone") or "UTC")
    fmt_hint = str(time_window.get("time_format_hint") or "")
    if not field or not representation:
        return doc

    lo = normalize_boundary_value(time_window["start"], representation, granularity, tz_name, fmt_hint)
    hi = normalize_boundary_value(time_window["end"], representation, granularity, tz_name, fmt_hint)
    numeric = isinstance(lo, (int, float)) and not isinstance(lo, bool)

    def _in_window(value: Any) -> bool:
        if numeric:
            return (
                isinstance(value, (int, float)) and not isinstance(value, bool)
                and float(lo) <= float(value) < float(hi)
            )
        return isinstance(value, str) and str(lo) <= value < str(hi)

    layers = doc.get("layers")
    sources = doc.get("sources") if isinstance(doc.get("sources"), dict) else None
    if not isinstance(layers, list) or sources is None:
        return doc

    # 解析绑定层 → source（无层 id 时取 temporal.layers[0]）。
    target_sid = None
    for layer in layers:
        if isinstance(layer, dict):
            lid = str(layer.get("id") or "")
            if lid and (not layer_id or lid == layer_id):
                target_sid = layer.get("source")
                break
    if not isinstance(target_sid, str) or target_sid not in sources:
        return doc
    src = sources[target_sid]
    if not isinstance(src, dict):
        return doc
    payload = src.get("inlineData") or src.get("data")
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        return doc

    kept = [
        f for f in payload["features"]
        if isinstance(f, dict)
        and _in_window((f.get("properties") or {}).get(field))
    ]

    new_payload = {**payload, "features": kept}
    new_src = {**src}
    if "inlineData" in new_src:
        new_src["inlineData"] = new_payload
    else:
        new_src["data"] = new_payload
    new_sources = {**sources, target_sid: new_src}

    # temporal cursor 盖章（本页帧）—— SVG 时间注记/导出证据随页正确。
    temporal = doc.get("temporal")
    new_temporal = dict(temporal) if isinstance(temporal, dict) else {}
    new_temporal["cursor_frame"] = time_window.get("frame_id")
    if new_temporal.get("enabled"):
        new_temporal["enabled"] = True

    return {**doc, "sources": new_sources, "temporal": new_temporal}
