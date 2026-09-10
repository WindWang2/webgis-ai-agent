"""STAC projection — Spatial Lakehouse V7 (ADR-0119, Scope F).

catalog 投影行 → **STAC 1.0.0** Item / Collection JSON（纯函数；只读
投影，不落库、不产生第二事实源 —— STAC 是交换格式，不是存储形态）。

字段映射（固定版本 ``stac_version: "1.0.0"``；必填字段缺失 = typed
拒绝 —— 绝不输出半真 Item）：

- ``geometry``：bbox → Polygon（世界坐标；跨经度 180 的对象如实输出
  原 bbox 多边形 —— 不做拆分，STAC 侧由 bbox 判相交）；
- ``properties.datetime``：time_start == time_end 时必填；跨时段对象
  用 ``start_datetime``/``end_datetime``（STAC 规范允许的替身组合）；
- ``assets.data``：content_location 指针 + ``webgis:data_object_id``
  扩展字段（非注册的 vendor 扩展前缀，仅标识用）；
- 分页响应（Collection + item links next/prev 有界）。
"""
from __future__ import annotations

import json
from urllib.parse import quote
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

STAC_VERSION = "1.0.0"
STAC_LICENSE = "proprietary"


class StacProjectionError(ValueError):
    """STAC 投影前提不成立（必填字段缺失）。"""

    code = "LAKEHOUSE_STAC_INVALID"


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    text = str(value)
    return text or None


def _bbox_polygon(bbox: Sequence[float]) -> Dict[str, Any]:
    minx, miny, maxx, maxy = (float(v) for v in bbox[:4])
    return {
        "type": "Polygon",
        "coordinates": [[
            [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy],
            [minx, miny],
        ]],
    }


def entry_to_stac_item(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """catalog 投影行（``to_dict()`` 形态）→ STAC Item。"""
    bbox = entry.get("bbox")
    if not bbox or len(bbox) != 4:
        raise StacProjectionError(
            f"STAC Item requires a bbox; entry {str(entry.get('object_id'))[:32]!r} "
            "has none (non-georeferenced objects are not STAC-projectable)"
        )
    time_start = _iso(entry.get("time_start"))
    time_end = _iso(entry.get("time_end"))
    properties: Dict[str, Any] = {}
    if time_start and time_end:
        if time_start == time_end:
            properties["datetime"] = time_start
        else:
            properties["datetime"] = time_start
            properties["start_datetime"] = time_start
            properties["end_datetime"] = time_end
    elif time_start:
        properties["datetime"] = time_start
    else:
        raise StacProjectionError(
            "STAC Item requires a datetime (or start/end pair); entry has "
            "no time extent"
        )
    object_id = str(entry.get("object_id") or entry.get("content_sha256") or "")
    properties.update({
        "webgis:kind": entry.get("kind"),
        "webgis:owner_type": entry.get("owner_type"),
        "webgis:owner_id": entry.get("owner_id"),
        "webgis:content_sha256": entry.get("content_sha256"),
        "webgis:byte_size": entry.get("byte_size"),
    })
    item: Dict[str, Any] = {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "id": object_id,
        "geometry": _bbox_polygon(bbox),
        "bbox": [float(v) for v in bbox[:4]],
        "properties": properties,
        "assets": {
            "data": {
                "href": str(entry.get("content_location")
                            or f"webgis://data-object/{object_id}"),
                "title": entry.get("title") or object_id,
                "roles": ["data"],
                "webgis:data_object_id": entry.get("object_id"),
            },
            "metadata": {
                "href": f"webgis://manifest/{entry.get('object_id')}",
                "title": "DataObject manifest",
                "roles": ["metadata"],
                "webgis:data_object_id": entry.get("object_id"),
            },
        },
    }
    if entry.get("tags"):
        item["properties"]["webgis:tags"] = list(entry["tags"])
    links: List[Dict[str, Any]] = [
        {"rel": "root", "href": "./collection.json", "type": "application/json"},
    ]
    item["links"] = links
    return item


def entries_to_stac_collection(
    entries: Sequence[Mapping[str, Any]],
    *,
    owner_type: str,
    owner_id: str,
    next_offset: Optional[int] = None,
    prev_offset: Optional[int] = None,
) -> Dict[str, Any]:
    """分页的 STAC Collection（items = 可投影条目；不可投影条目在
    ``skipped`` 披露 —— 绝不静默丢弃）。"""
    items: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for entry in entries:
        try:
            items.append(entry_to_stac_item(entry))
        except StacProjectionError:
            skipped.append(str(entry.get("object_id"))[:64])
    collection: Dict[str, Any] = {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": f"webgis-lakehouse-{owner_type}-{owner_id}",
        "description": (
            f"WebGIS lakehouse catalog ({owner_type} scope)"
        ),
        "license": STAC_LICENSE,
        "extent": _extent_of(entries),
        "links": _links(owner_type, owner_id, next_offset, prev_offset),
    }
    return {"collection": collection, "items": items, "skipped": skipped}


def _extent_of(entries: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    spatial: Optional[List[float]] = None
    temporal: List[Optional[str]] = [None, None]
    for entry in entries:
        bbox = entry.get("bbox")
        if bbox and len(bbox) == 4:
            box = [float(v) for v in bbox[:4]]
            spatial = box if spatial is None else [
                min(spatial[0], box[0]), min(spatial[1], box[1]),
                max(spatial[2], box[2]), max(spatial[3], box[3]),
            ]
        for key, slot in (("time_start", 0), ("time_end", 1)):
            iso = _iso(entry.get(key))
            if iso is None:
                continue
            if temporal[slot] is None:
                temporal[slot] = iso
            elif slot == 0:
                temporal[slot] = min(temporal[slot], iso)
            else:
                temporal[slot] = max(temporal[slot], iso)
    return {
        "spatial": {"bbox": [spatial] if spatial else []},
        "temporal": {"interval": [temporal]},
    }


def _links(owner_type: str, owner_id: str,
           next_offset: Optional[int], prev_offset: Optional[int]) -> List[Dict[str, Any]]:
    owner = quote(str(owner_id), safe="")
    base = (
        f"/api/v1/lakehouse/catalog/stac?owner_type={owner_type}"
        f"&owner_id={owner}"
    )
    links: List[Dict[str, Any]] = [{
        "rel": "root", "href": base, "type": "application/json",
    }]
    if next_offset is not None:
        links.append({
            "rel": "next", "href": f"{base}&offset={next_offset}",
            "type": "application/json",
        })
    if prev_offset is not None:
        links.append({
            "rel": "prev", "href": f"{base}&offset={prev_offset}",
            "type": "application/json",
        })
    return links


def item_to_json(item: Mapping[str, Any]) -> str:
    return json.dumps(dict(item), ensure_ascii=False, sort_keys=False)
