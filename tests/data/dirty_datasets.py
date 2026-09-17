"""Dirty dataset corpus（DQH v1）—— 合成脏数据语料（零真实数据依赖）。

每个生成器是纯函数：同参数同输出（E2E digest 确定性的前提）。规模有界，
绝不使用真实业务数据（任务书测试纪律）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict


def _feat(geom: Any, **props: Any) -> Dict[str, Any]:
    return {"type": "Feature", "geometry": geom, "properties": props}


def dirty_crs_unknown_unit_ambiguous(n: int = 12) -> Dict[str, Any]:
    """CRS 缺失 + 面积/人口字段无单位标记（UNIT_AMBIGUOUS 口径）。"""
    return {
        "type": "FeatureCollection",
        "features": [
            _feat({"type": "Point", "coordinates": [116.0 + i * 0.1, 39.0 + i * 0.1]},
                  名称=f"网格{i}", 面积=1200.5 + i * 13.7, 人口=45000 + i * 137)
            for i in range(n)
        ],
    }


def dirty_timezone_naive(n: int = 10) -> Dict[str, Any]:
    """时间字段为 naive 时刻字符串（TIMEZONE_MISSING 口径）。"""
    return {
        "type": "FeatureCollection",
        "features": [
            _feat({"type": "Point", "coordinates": [120.0 + i * 0.01, 30.0 + i * 0.01]},
                  站点=f"站点{i}", 采集时间=f"2024-0{i % 9 + 1}-15 08:30:00")
            for i in range(n)
        ],
    }


def dirty_role_ambiguous(n: int = 8) -> Dict[str, Any]:
    """count 命名字段的样本含负数/非整数（FIELD_ROLE_AMBIGUOUS：名不副实）。"""
    values = [3.5, -2, 7, -1, 4.5, 6, -3, 2.5]
    return {
        "type": "FeatureCollection",
        "features": [
            _feat({"type": "Point", "coordinates": [100.0 + i, 25.0 + i]},
                  地区=f"P{i}", count=values[i % len(values)])
            for i in range(n)
        ],
    }


def dirty_admin_variants() -> Dict[str, Any]:
    """行政区变体名 + 近似码（ADMIN_MISMATCH 口径；至少一个可解析名作覆盖证明）。"""
    names = ["浙江省", "江苏省", "浙扛省", "Zhejiang", "广东省", "广東省"]
    codes = ["330000", "320000", "110004"]
    features = [
        _feat({"type": "Point", "coordinates": [120.0 + i * 0.5, 30.0 + i * 0.3]},
              省份=names[i % len(names)])
        for i in range(9)
    ] + [
        _feat({"type": "Point", "coordinates": [118.0 + i * 0.5, 28.0 + i * 0.3]},
              adcode=codes[i % len(codes)])
        for i in range(3)
    ]
    return {"type": "FeatureCollection", "features": features}


def dirty_geometry(n_duplicates: int = 2) -> Dict[str, Any]:
    """自相交多边形 + 空几何 + 重复要素 + Null Island + 出界坐标。"""
    bowtie = {"type": "Polygon", "coordinates": [[
        [0.0, 0.0], [2.0, 2.0], [2.0, 0.0], [0.0, 2.0], [0.0, 0.0]]]}
    dup = {"type": "Point", "coordinates": [5.0, 5.0]}
    features: list = [
        _feat(bowtie, id=1, v=10),
        _feat(None, id=2, v=20),
        _feat({"type": "Point", "coordinates": [0.0, 0.0]}, id=3, v=30),
        _feat({"type": "Point", "coordinates": [200.0, 95.0]}, id=4, v=40),
    ]
    for k in range(n_duplicates):
        features.append(_feat(dup, id=5 + k, v=50 + k))
    return {"type": "FeatureCollection", "features": features}


def clean_dataset(n: int = 6) -> Dict[str, Any]:
    """干净对照（无 CRS/单位/时区/角色/行政区/几何发现）。"""
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            _feat({"type": "Point", "coordinates": [104.0 + i * 0.1, 35.0 + i * 0.1]},
                  城市名=f"站点{i}", 人口_万人=round(10.5 + i, 1),
                  注册日期=f"2024-01-{i + 1:02d}")
            for i in range(n)
        ],
    }


def corpus() -> Dict[str, Callable[[], Dict[str, Any]]]:
    """name → 生成器（调用方持 payload；生成器本身确定可重放）。"""
    return {
        "crs_unknown_unit_ambiguous": dirty_crs_unknown_unit_ambiguous,
        "timezone_naive": dirty_timezone_naive,
        "role_ambiguous": dirty_role_ambiguous,
        "admin_variants": dirty_admin_variants,
        "geometry_dirty": dirty_geometry,
        "clean": clean_dataset,
    }
