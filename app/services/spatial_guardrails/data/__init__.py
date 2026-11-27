"""惰性单例数据资产加载（spec §5）：陆块掩膜索引 + 行政区划派生索引。

进程内构建一次、之后只读共享（无锁读）；不触碰文件系统与网络。
"""
from __future__ import annotations

import re
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
    if name.endswith("自治州"):
        stem = name[:-3]
        ethnic = r"蒙古族|蒙古|土家族|苗族|布依族|侗族|白族|傣族|景颇族|傈僳族|哈尼族|彝族|藏族|羌族|回族|哈萨克|柯尔克孜|朝鲜族|壮族"
        return re.split(ethnic, stem, maxsplit=1)[0]
    return name


def get_prefecture_short_names() -> dict[str, str]:
    """地级简称/全称 → 6 位码（名称模糊映射用）。

    全称/简称在多个地级码间重名时（如三省共用的「省直辖县级」：3790 山东 /
    4190 河南 / 4290 湖北）该键不进入映射：归属无法唯一判定时解析失败，
    优于把调用方静默引向任一首码（首码即中 → 错归属山东）。
    """
    global _pref_short
    if _pref_short is None:
        with _lock:
            if _pref_short is None:
                codes: dict[str, str] = {}
                ambiguous: set[str] = set()
                for code, (name, _prov) in _admin.PREFECTURES.items():
                    for key in (name, _prefecture_short_name(name)):
                        if key in codes and codes[key] != code:
                            ambiguous.add(key)
                        else:
                            codes.setdefault(key, code)
                _pref_short = {k: v for k, v in codes.items() if k not in ambiguous}
    return _pref_short
