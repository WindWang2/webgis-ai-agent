"""惰性单例数据资产加载（spec §5）：陆块掩膜索引 + 行政区划派生索引。

进程内构建一次、之后只读共享（无锁读）；不触碰文件系统与网络。
"""
from __future__ import annotations

import threading

from app.services.spatial_guardrails.data import admin_divisions as _admin
from app.services.spatial_guardrails.data import world_landmask as _world
from app.services.spatial_guardrails.geo_index import BboxSpatialHash

_lock = threading.Lock()
_land_index: BboxSpatialHash | None = None
_water_rings: list[tuple[str, list[tuple[float, float]]]] | None = None
_pref_short: dict[str, str] | None = None


def get_landmask_index() -> BboxSpatialHash:
    global _land_index
    if _land_index is None:
        with _lock:
            if _land_index is None:
                index = BboxSpatialHash(cell_deg=10.0)
                for _name, ring in _world.LAND_POLYGONS:
                    index.register(ring)
                _land_index = index
    return _land_index


def get_water_rings() -> list[tuple[str, list[tuple[float, float]]]]:
    global _water_rings
    if _water_rings is None:
        with _lock:
            if _water_rings is None:
                _water_rings = list(_world.WATER_POLYGONS)
    return _water_rings


def landmask_version() -> str:
    return _world.LANDMASK_VERSION


def admin_table_edition() -> str:
    return _admin.ADMIN_TABLE_EDITION


def _prefecture_short_name(name: str) -> str:
    for suffix in ("市", "地区", "盟"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    # 自治州：取首两字简称（如 "海西蒙古族藏族自治州" → "海西"）
    if name.endswith("自治州") or "自治州" in name:
        return name[:2]
    return name


def get_prefecture_short_names() -> dict[str, str]:
    """地级简称/全称 → 6 位码（名称模糊映射用）。"""
    global _pref_short
    if _pref_short is None:
        with _lock:
            if _pref_short is None:
                table: dict[str, str] = {}
                for code, (name, _prov) in _admin.PREFECTURES.items():
                    table.setdefault(name, code)
                    table.setdefault(_prefecture_short_name(name), code)
                _pref_short = table
    return _pref_short
