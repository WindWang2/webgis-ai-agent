"""离线演示 #1：成都市学校 POI 点 + 行政区聚合（常规空间分析）。

真实链路：ToolRegistry → dispatch(``spatial_aggregate``) → 分区数值 →
可审计引用 → MapSpec 消费边界（只打印边界，不新建 MapSpec / 渲染器）。

无 API Key、无 PostGIS、无网络。运行::

    python examples/gis_capability_ecosystem/demo_school_poi.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tools import init_tools  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402

# 三个行政区（投影米制 EPSG:3857 的示意方块）+ 5 个学校 POI。
DISTRICTS = {
    "type": "FeatureCollection",
    "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
    "features": [
        {"type": "Feature", "properties": {"name": "锦江区"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}},
        {"type": "Feature", "properties": {"name": "武侯区"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [10, 0], [20, 0], [20, 10], [10, 10], [10, 0]]]}},
        {"type": "Feature", "properties": {"name": "青羊区"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [0, 10], [10, 10], [10, 20], [0, 20], [0, 10]]]}},
    ],
}
SCHOOLS = {
    "type": "FeatureCollection",
    "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
    "features": [
        {"type": "Feature", "properties": {"name": "学校A", "students": 1200},
         "geometry": {"type": "Point", "coordinates": [2, 2]}},
        {"type": "Feature", "properties": {"name": "学校B", "students": 800},
         "geometry": {"type": "Point", "coordinates": [5, 6]}},
        {"type": "Feature", "properties": {"name": "学校C", "students": 1500},
         "geometry": {"type": "Point", "coordinates": [12, 3]}},
        {"type": "Feature", "properties": {"name": "学校D", "students": 900},
         "geometry": {"type": "Point", "coordinates": [18, 8]}},
        {"type": "Feature", "properties": {"name": "学校E", "students": 1100},
         "geometry": {"type": "Point", "coordinates": [4, 14]}},
    ],
}


def _numeric_values(obj, depth=0):
    out = []
    if depth > 8:
        return out
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        for v in obj.values():
            out.extend(_numeric_values(v, depth + 1))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_numeric_values(v, depth + 1))
    return out


def run() -> dict:
    reg = ToolRegistry()
    init_tools(reg)
    assert "spatial_aggregate" in reg.tool_names(), "真实工具必须已注册"
    result = asyncio.run(reg.dispatch("spatial_aggregate", {
        "points": SCHOOLS, "polygons": DISTRICTS,
    }))
    if not isinstance(result, dict):
        raise RuntimeError(f"unexpected dispatch result: {type(result).__name__}")

    # G06 CAP-06：结果摘要必须含关键统计数值或指向可分页 ref，不能只剩「有 N 区」。
    blob = json.dumps(result, ensure_ascii=False, default=str)
    numbers = _numeric_values(result)
    has_numbers = bool(numbers)
    has_ref = any(k in blob for k in ("ref", "artifact", "geojson_ref"))
    summary_ok = bool(has_numbers or has_ref)

    return {
        "scenario": "成都市学校分布 → 按区汇总并说明单位（离线示意数据）",
        "dispatch_tool": "spatial_aggregate",
        "dispatch_via": "ToolRegistry.dispatch（唯一执行入口）",
        "result_keys": sorted(result.keys())[:16],
        "numeric_values_present": has_numbers,
        "numeric_value_count": len(numbers),
        "has_ref": has_ref,
        "summary_value_contract_ok": summary_ok,
        "units_disclosure": (
            "统计量纲 = count（学校数）/ 人数（人）；"
            "距离为投影米制 EPSG:3857，秒与米绝不混用"),
        "data_source_disclosure": (
            "本地 fixture（示意数据，非真实统计年鉴）；"
            "外部数据缺席时标 not_evaluated，不得宣称真实业务结论"),
        "mapspec_consumption_boundary": (
            "本示范只输出分区数值 + ref；MapSpec 组装/渲染由 G04/G05 负责，"
            "本线不新建 MapSpec 或渲染器"),
        "artifact_ref": result.get("ref") or result.get("geojson_ref") or None,
    }


if __name__ == "__main__":
    sys.stdout.write(
        json.dumps(run(), ensure_ascii=False, indent=2, default=str) + "\n")
