"""中国境内地理查询的本地优先路由。

出网前先试本地库，链路：**gd_poi（高德 POI，5100 万点，WGS84）→ OSM 主题
GPKG → 在线 API**。本地命中（count>0）即返回并打 ``source=local_*``；
本地未命中（0 条或库不可用）、或 ``LOCAL_QUERY_FIRST=false`` 时返回
None，调用方走原在线路径——即「本地没有对应资料才用网络搜索」。
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

# G-4（#868）：中文类别 → OSM 标签的单一事实来源在 app/lib/osm_category_map.py，
# 本表由其派生（此前两处各自维护已失同步：小学/中学/超市/商场/车站等
# #694 新增词条本地链缺失）。新增类别只改 osm_category_map。
from app.lib.osm_category_map import CHINESE_CATEGORY_TAGS

_CATEGORY_TO_TAG: Dict[str, str] = {
    _zh: f"{k}={v}"
    for _zh, (k, v) in CHINESE_CATEGORY_TAGS.items()
}
# 英文直查别名（本地 GPKG 的 fclass 值域）
for _en, _kv in (
    ("university", "amenity=university"), ("school", "amenity=school"),
    ("hospital", "amenity=hospital"), ("clinic", "amenity=clinic"),
    ("restaurant", "amenity=restaurant"), ("bank", "amenity=bank"),
    ("cafe", "amenity=cafe"), ("bar", "amenity=bar"),
    ("park", "leisure=park"), ("garden", "leisure=garden"),
    ("hotel", "tourism=hotel"), ("museum", "tourism=museum"),
    ("library", "amenity=library"), ("pharmacy", "amenity=pharmacy"),
    ("fuel", "amenity=fuel"), ("parking", "amenity=parking"),
    ("bus_station", "amenity=bus_station"), ("police", "amenity=police"),
    ("fire_station", "amenity=fire_station"), ("post_office", "amenity=post_office"),
    ("theatre", "amenity=theatre"), ("cinema", "amenity=cinema"),
    ("sports_centre", "leisure=sports_centre"), ("stadium", "leisure=stadium"),
    ("swimming_pool", "leisure=swimming_pool"),
    ("kindergarten", "amenity=kindergarten"), ("college", "amenity=college"),
    ("supermarket", "shop=supermarket"),
):
    _CATEGORY_TO_TAG[_en] = _kv

# 口语/报告用词往往不是 OSM 标签原词（「高等院校」不含「高校」子串）。
# G-4：与 osm_category_map 同步——补齐学段/购物/车站词条（此前本地链与
# 出网链对同一中文词解析到不同 OSM tag，跨源计数不可比）。
_SYNONYM_TAGS: List[Tuple[Tuple[str, ...], Tuple[str, ...]]] = [
    (
        ("高等院校", "高等学校", "高校", "大学", "院校", "大专", "本科",
         "university", "college"),
        ("amenity=university", "amenity=college"),
    ),
    (("学校", "中小学", "中学", "小学", "school"), ("amenity=school",)),
    (("幼儿园", "托儿所", "kindergarten"), ("amenity=kindergarten",)),
    (("医院", "三甲", "诊所", "hospital", "clinic"),
     ("amenity=hospital", "amenity=clinic")),
    (("餐厅", "餐馆", "饭店", "餐饮", "火锅", "restaurant"),
     ("amenity=restaurant",)),
    (("咖啡", "cafe"), ("amenity=cafe",)),
    (("酒店", "宾馆", "旅馆", "hotel"), ("tourism=hotel",)),
    (("公园", "park"), ("leisure=park",)),
    (("银行", "bank"), ("amenity=bank",)),
    (("药店", "药房", "pharmacy"), ("amenity=pharmacy",)),
    (("加油站", "fuel"), ("amenity=fuel",)),
    (("停车场", "parking"), ("amenity=parking",)),
    (("超市", "supermarket"), ("shop=supermarket",)),
    (("商场", "mall"), ("shop=mall",)),
    (("菜市场", "marketplace"), ("amenity=marketplace",)),
    (("地铁站", "地铁"), ("railway=station",)),
    (("火车站", "高铁站", "动车站"), ("railway=station",)),
]

# 高德 POI 检索提示：中文关键词 → amap 一级大类(category)/二三级(subtype)。
# gd 库与 amap 同源同分类法，比 OSM 标签映射更直接；顺序即优先级（长词在前）。
_GD_KEYWORD_HINTS: List[Tuple[Tuple[str, ...], Dict[str, Any]]] = [
    (("三级甲等", "三甲"), {"category": "医疗保健服务", "subtype": "三级甲等"}),
    # G-8（#872）：复合学段词专属词条——此前"中小学"按子串顺序先命中
    # ("小学",) 只查小学（漏掉中学），而 OSM 出网链解析为全学段 school，
    # 本地有数/无数两种情况下集合语义不同，跨源计数不可比。
    (("中小学", "初高中"), {"category": "科教文化服务", "subtypes": ["小学", "中学"]}),
    (("小学",), {"category": "科教文化服务", "subtype": "小学"}),
    (("幼儿园",), {"category": "科教文化服务", "subtype": "幼儿园"}),
    (("中学", "初中", "高中", "完中"), {"category": "科教文化服务", "subtype": "中学"}),
    (("高等院校", "高等学校", "高校", "大学", "院校", "大专", "本科"),
     {"category": "科教文化服务", "subtype": "高等院校"}),
    (("机场",), {"category": "交通设施服务", "subtype": "机场"}),
    (("火车站", "高铁站", "动车站"), {"category": "交通设施服务", "subtype": "火车站"}),
    (("地铁站", "地铁"), {"category": "交通设施服务", "subtype": "地铁站"}),
    (("加油站",), {"category": "汽车服务", "subtype": "加油站"}),
    (("停车场", "停车库"), {"category": "汽车服务", "subtype": "停车场"}),
    (("药店", "药房"), {"category": "医疗保健服务", "subtype": "药店"}),
    (("酒店", "宾馆", "旅馆", "住宿"), {"category": "住宿服务"}),
    (("餐厅", "餐馆", "饭店", "餐饮", "火锅", "小吃", "美食", "咖啡", "奶茶"),
     {"category": "餐饮服务"}),
    (("超市", "商场", "便利店", "购物", "菜市场"), {"category": "购物服务"}),
    (("医院", "诊所", "卫生院", "医疗"), {"category": "医疗保健服务"}),
    (("银行", "ATM", "信用社"), {"category": "金融保险服务"}),
    (("公园", "景区", "景点", "风景名胜"), {"category": "风景名胜"}),
    (("政府", "街道办", "派出所", "政务"), {"category": "政府机构及社会团体"}),
    (("公司", "企业", "工厂"), {"category": "公司企业"}),
]

_AREA_DIST_RE = re.compile(r"\d+\s*(公里|千米|km|公里内|内).*$", re.IGNORECASE)
_PLACE_RE = re.compile(r"[\u4e00-\u9fff]{2,12}(?:省|自治区|市|区|县|旗|盟|自治州)")
_CHINA_BBOX = (73.0, 3.0, 135.0, 54.0)

_OSM_ADMIN_LEVEL = {4: "province", 6: "city", 8: "district"}


def local_query_first_enabled() -> bool:
    return bool(getattr(settings, "LOCAL_QUERY_FIRST", True))


def infer_admin_levels(name: str) -> List[str]:
    """按中文后缀猜测查询级别，未命中则 city → district → province。"""
    text = (name or "").strip()
    if text.endswith(("省", "自治区")):
        return ["province"]
    if text.endswith(("市",)):
        return ["city", "district"]
    if text.endswith(("区", "县", "旗", "盟", "自治州")):
        return ["district", "city"]
    return ["city", "district", "province"]


def _clean_area(area: str) -> str:
    cleaned = _AREA_DIST_RE.sub("", area or "").strip()
    return cleaned or (area or "").strip()


def resolve_local_admin(
    name: str,
    *,
    to_wgs84: bool = True,
    simplified: bool = True,
    levels: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """名称命中本地行政区则返回 FeatureCollection，否则 None。"""
    if not name or not local_query_first_enabled():
        return None
    from app.tools.local_admin import query_admin_boundary

    for level in levels or infer_admin_levels(name):
        result = query_admin_boundary(
            level, name=name, to_wgs84=to_wgs84, simplified=simplified,
        )
        if result.get("error"):
            continue
        if result.get("count", 0) > 0 and result.get("total_bounds"):
            result = dict(result)
            result["source"] = "local_admin"
            result["level"] = level
            return result
    return None


def admin_bbox_wgs84(name: str) -> Optional[List[float]]:
    hit = resolve_local_admin(name, to_wgs84=True, simplified=True)
    if not hit:
        return None
    bbox = hit.get("total_bounds")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        return [float(v) for v in bbox]
    return None


def _stamp(payload: Dict[str, Any], source: str) -> Dict[str, Any]:
    out = dict(payload)
    out["source"] = source
    return out


def _gd_poi_generated_at() -> Optional[str]:
    """gd_poi 库的数据年份（meta.json generated_at），#702。

    语义是**数据生成时刻**（库的 vintage），不是查询时刻——agent 据此判断
    「本地库是几月抓的」。meta 缺失/损坏时返回 None（诚实缺位优于编造时间戳）。
    """
    try:
        from app.services.local_poi import _meta_path
        meta_path = _meta_path()
        if not meta_path.exists():
            return None
        import json as _json
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        val = meta.get("generated_at")
        return val if isinstance(val, str) and val else None
    except Exception:
        return None


def resolve_poi_filters(
    keyword: str = "",
    types: str = "",
) -> Tuple[List[str], Optional[str]]:
    """把中文关键词/高德 types 收成 OSM tag 列表；对不上才退回 name_like。"""
    text = f"{keyword or ''} {types or ''}".strip()
    if not text:
        return [], None
    exact = _CATEGORY_TO_TAG.get(keyword) or _CATEGORY_TO_TAG.get(types)
    if exact:
        return [exact], None
    tags: List[str] = []
    for keys, tagset in _SYNONYM_TAGS:
        if any(k and k in text for k in keys):
            tags.extend(tagset)
    if tags:
        return list(dict.fromkeys(tags)), None
    if "=" in text:
        return [text.split()[0]], None
    return [], keyword or types or None


def _is_china_bbox(bbox: Sequence[float]) -> bool:
    minx, miny, maxx, maxy = (float(v) for v in bbox)
    cminx, cminy, cmaxx, cmaxy = _CHINA_BBOX
    return not (maxx < cminx or minx > cmaxx or maxy < cminy or miny > cmaxy)


def _ring_bbox(ring: Sequence) -> Optional[List[float]]:
    xs, ys = [], []
    for pt in ring:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
    if len(xs) < 2:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def polygon_to_bbox(polygon: Any) -> Optional[List[float]]:
    """从 search_poi_polygon 的 polygon 实参抽出 WGS/GCJ bbox。"""
    if polygon is None:
        return None
    if isinstance(polygon, dict):
        if isinstance(polygon.get("total_bounds"), (list, tuple)) and len(polygon["total_bounds"]) == 4:
            return [float(v) for v in polygon["total_bounds"]]
        features = (
            polygon.get("features", [])
            if polygon.get("type") == "FeatureCollection"
            else [polygon]
        )
        xs, ys = [], []
        for feat in features:
            geom = (feat or {}).get("geometry") or {}
            coords = geom.get("coordinates")
            if not coords:
                continue
            gtype = geom.get("type")
            rings = []
            if gtype == "Polygon":
                rings = coords[:1]
            elif gtype == "MultiPolygon":
                rings = [poly[0] for poly in coords if poly]
            for ring in rings:
                box = _ring_bbox(ring)
                if box:
                    xs.extend([box[0], box[2]])
                    ys.extend([box[1], box[3]])
        if xs:
            return [min(xs), min(ys), max(xs), max(ys)]
        return None
    if isinstance(polygon, (list, tuple)):
        if len(polygon) == 4 and all(isinstance(v, (int, float)) for v in polygon):
            w, s, e, n = (float(v) for v in polygon)
            return [w, s, e, n]
        return _ring_bbox(polygon)
    return None


def _radius_bbox(lng: float, lat: float, radius_m: float) -> List[float]:
    dlat = float(radius_m) / 111_000.0
    clat = max(0.2, abs(math.cos(math.radians(lat))))
    dlng = float(radius_m) / (111_000.0 * clat)
    return [lng - dlng, lat - dlat, lng + dlng, lat + dlat]


def _split_place_and_keyword(query: str) -> Tuple[Optional[str], str]:
    text = (query or "").strip()
    if not text:
        return None, ""
    match = _PLACE_RE.search(text)
    if match:
        place = match.group(0)
        rest = (text[: match.start()] + text[match.end() :]).strip(" ，,;；")
        return place, rest or text
    # 「成都大学」无「市」后缀时，用前 2–4 字碰行政区
    # #703-8：每候选至多两次调用（裸名一次 + 加「市」一次）——旧写法在判定与
    # 赋值处重复调 admin_bbox_wgs84，至多 2× 冗余 GeoDataFrame 过滤/候选。
    for n in range(4, 1, -1):
        cand = text[:n]
        bbox = admin_bbox_wgs84(cand)
        if bbox or admin_bbox_wgs84(cand + "市"):
            place = cand if bbox else cand + "市"
            rest = text[n:].strip()
            return place, rest or text
    return None, text


def _local_osm(
    theme: str,
    bbox: Sequence[float],
    *,
    name_like: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 200,
) -> Optional[Dict[str, Any]]:
    """GPKG 不可用返回 None；查得到（含 0 条）返回 FeatureCollection。"""
    from app.services.local_osm import query_osm_features

    result = query_osm_features(
        theme, list(bbox), name_like=name_like, tag=tag, limit=limit,
    )
    if result.get("error"):
        return None
    return result


def _local_osm_pois(
    bbox: Sequence[float],
    *,
    tags: Sequence[str],
    name_like: Optional[str],
    limit: int,
) -> Optional[Dict[str, Any]]:
    if not tags:
        return _local_osm("pois", bbox, name_like=name_like, tag=None, limit=limit)
    seen: Dict[Any, bool] = {}
    features: List[Dict[str, Any]] = []
    available = False
    per = max(1, int(limit) // len(tags)) if len(tags) > 1 else int(limit)
    for tag in tags:
        part = _local_osm("pois", bbox, name_like=name_like, tag=tag, limit=per)
        if part is None:
            continue
        available = True
        for feat in part.get("features") or []:
            oid = (feat.get("properties") or {}).get("osm_id") or id(feat)
            if oid in seen:
                continue
            seen[oid] = True
            features.append(feat)
            if len(features) >= limit:
                break
        if len(features) >= limit:
            break
    if not available:
        return None
    return {
        "type": "FeatureCollection",
        "features": features[:limit],
        "count": len(features[:limit]),
        "bbox": list(bbox),
    }


def _poi_collection(osm: Dict[str, Any], bbox: Sequence[float]) -> Dict[str, Any]:
    features = osm.get("features") or []
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": osm.get("count", len(features)),
        "provider": "local_osm",
        "source": "local_osm",
        "bbox": list(bbox),
    }


def _gd_hints(text: str) -> Dict[str, str]:
    for keys, hint in _GD_KEYWORD_HINTS:
        if any(k and k in text for k in keys):
            return hint
    return {}


def _local_gd_poi(
    bbox: Sequence[float],
    *,
    keyword: str = "",
    types: str = "",
    limit: int = 50,
) -> Optional[Dict[str, Any]]:
    """gd_poi 库检索；库不可用返回 None（走 OSM），命中返回 FeatureCollection。

    关键词能对上 amap 分类走 category/subtype（召回最准）；对不上退
    name_like 文本匹配。分类查空时再兜底一次 name_like，防口语词错配。
    """
    from app.services.local_poi import query_gd_poi

    text = f"{keyword or ''} {types or ''}".strip()
    hint = _gd_hints(text)
    name_term = keyword or types or None
    # G-8（#872）：复合学段词（中小学/初高中）映射为多 subtype OR 查询，
    # 与 OSM 链的全学段语义对齐。
    subtype_arg: Any = hint.get("subtypes") or hint.get("subtype")
    result = query_gd_poi(
        list(bbox),
        category=hint.get("category"),
        subtype=subtype_arg,
        name_like=None if hint else name_term,
        limit=limit,
    )
    if result.get("error"):
        return None  # 库未生成/读失败：交给 OSM 层
    if result.get("count", 0) > 0 or not hint:
        return result if result.get("count", 0) > 0 else None
    retry = query_gd_poi(list(bbox), name_like=name_term, limit=limit)
    if retry.get("error") or retry.get("count", 0) == 0:
        return None
    return retry


def _local_poi_chain(
    bbox: Sequence[float],
    *,
    keyword: str = "",
    types: str = "",
    limit: int = 50,
) -> Optional[Dict[str, Any]]:
    """本地 POI 检索链：gd_poi → OSM pois。任一命中即返回；全空返回 None
    （调用方据此出网）。"""
    gd = _local_gd_poi(bbox, keyword=keyword, types=types, limit=limit)
    if gd is not None:
        # G-1（#865）：截断披露透传——gd 信封里的 total_matched/truncated/notes
        # 此前被丢掉，LLM 无从得知样本被截断（偏斜样本上照常输出分布结论）。
        gd_envelope = {
            k: gd[k]
            for k in ("total_matched", "truncated", "notes")
            if gd.get(k) is not None
        }
        return {
            "type": "FeatureCollection",
            "features": gd.get("features", []),
            "count": gd.get("count", 0),
            "provider": "local_gd_poi",
            "source": "local_gd_poi",
            # #702：数据年份（库的 vintage），非查询时刻；meta 缺失时诚实缺位
            "generated_at": _gd_poi_generated_at(),
            "bbox": list(bbox),
            **gd_envelope,
        }
    tags, name_like = resolve_poi_filters(keyword, types)
    osm = _local_osm_pois(bbox, tags=tags, name_like=name_like, limit=limit)
    if osm is None or osm.get("count", 0) == 0:
        return None
    return _poi_collection(osm, bbox)


def try_local_admin_division(
    keywords: str,
    child_level: int = 0,
) -> Optional[Dict[str, Any]]:
    """拦截 get_admin_division。街道级（child_level>=2 或区级下级）本地没有，返回 None。"""
    if not local_query_first_enabled() or not keywords:
        return None
    hit = resolve_local_admin(keywords, to_wgs84=False, simplified=True)
    if hit is None:
        return None
    level = hit.get("level")
    if child_level >= 2:
        return None
    if child_level >= 1:
        if level not in ("city", "province"):
            return None
        from app.tools.local_admin import query_child_districts

        parent_level = "province" if level == "province" else "city"
        children = query_child_districts(
            keywords, parent_level, to_wgs84=False, simplified=True,
        )
        if children.get("error") or not children.get("features"):
            return None
        return _stamp(children, "local_admin")
    return hit


def try_local_child_districts(keywords: str) -> Optional[Dict[str, Any]]:
    if not local_query_first_enabled() or not keywords:
        return None
    hit = resolve_local_admin(keywords, to_wgs84=False, simplified=True)
    if hit is None or hit.get("level") not in ("city", "province"):
        return None
    from app.tools.local_admin import query_child_districts

    parent_level = "province" if hit["level"] == "province" else "city"
    children = query_child_districts(
        keywords, parent_level, to_wgs84=False, simplified=True,
    )
    if children.get("error") or not children.get("features"):
        return None
    return _stamp(children, "local_admin")


def try_local_osm_poi(
    area: str,
    category: str = "restaurant",
    limit: int = 50,
) -> Optional[Dict[str, Any]]:
    """拦截 query_osm_poi：本地链（gd_poi → OSM）命中即返回；未命中放行出网。"""
    if not local_query_first_enabled() or not area:
        return None
    clean = _clean_area(area)
    bbox = admin_bbox_wgs84(clean)
    if bbox is None:
        return None
    osm = _local_poi_chain(bbox, keyword=category, limit=limit)
    if osm is None:
        return None
    south_west_north_east = f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}"
    # G-1（#865）：截断披露透传到工具信封（此前 _local_poi_chain 已带上、
    # 这里又丢一次），query_osm_poi 的调用方（LLM/统计下游）可感知样本完整性。
    disclosure = {
        k: osm[k]
        for k in ("total_matched", "truncated", "notes")
        if osm.get(k) is not None
    }
    return {
        "type": "poi_query",
        "area": area,
        "category": category,
        "count": osm.get("count", len(osm.get("features", []))),
        "geojson": {
            "type": "FeatureCollection",
            "features": osm.get("features", []),
        },
        "bbox": south_west_north_east,
        "source": osm["source"],
        **disclosure,
    }


def try_local_osm_roads(
    area: str,
    road_type: str = "primary",
    limit: int = 100,
) -> Optional[Dict[str, Any]]:
    if not local_query_first_enabled() or not area:
        return None
    bbox = admin_bbox_wgs84(_clean_area(area))
    if bbox is None:
        return None
    tag = f"highway={road_type}" if road_type else None
    osm = _local_osm("roads", bbox, tag=tag, limit=limit)
    if osm is None:
        return None
    south_west_north_east = f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]}"
    return {
        "type": "road_query",
        "area": area,
        "road_type": road_type,
        "count": osm.get("count", len(osm.get("features", []))),
        "geojson": {
            "type": "FeatureCollection",
            "features": osm.get("features", []),
        },
        "bbox": south_west_north_east,
        "source": "local_osm",
    }


def try_local_osm_boundary(
    name: str,
    admin_level: int = 8,
) -> Optional[Dict[str, Any]]:
    if not local_query_first_enabled() or not name:
        return None
    mapped = _OSM_ADMIN_LEVEL.get(int(admin_level), "district")
    hit = resolve_local_admin(name, to_wgs84=True, simplified=True, levels=[mapped])
    if hit is None:
        return None
    return {
        "type": "boundary_query",
        "name": name,
        "admin_level": admin_level,
        "count": hit.get("count", 0),
        "geojson": {
            "type": "FeatureCollection",
            "features": hit.get("features", []),
        },
        "source": "local_admin",
        "total_bounds": hit.get("total_bounds"),
    }


def try_local_search_poi(
    keyword: str,
    city: str = "",
    limit: int = 20,
) -> Optional[Dict[str, Any]]:
    """拦截 search_poi：本地链（gd_poi → OSM）命中即返回；未命中放行出网。"""
    if not local_query_first_enabled() or not keyword:
        return None
    place = city
    kw = keyword
    if not place:
        place, rest = _split_place_and_keyword(keyword)
        if place:
            kw = rest or keyword
    if not place:
        return None
    bbox = admin_bbox_wgs84(place)
    if bbox is None or not _is_china_bbox(bbox):
        return None
    hit = _local_poi_chain(bbox, keyword=kw, limit=limit)
    if hit is None:
        return None
    return hit


def try_local_search_poi_polygon(
    polygon: Any,
    keyword: str = "",
    types: str = "",
    limit: int = 50,
) -> Optional[Dict[str, Any]]:
    if not local_query_first_enabled() or (not keyword and not types):
        return None
    bbox = polygon_to_bbox(polygon)
    if bbox is None or not _is_china_bbox(bbox):
        return None
    hit = _local_poi_chain(bbox, keyword=keyword, types=types, limit=limit)
    if hit is None:
        return None
    return hit


def try_local_search_poi_around(
    center: Sequence[float],
    radius_m: int = 1000,
    keyword: str = "",
    types: str = "",
    limit: int = 20,
) -> Optional[Dict[str, Any]]:
    if not local_query_first_enabled() or not center or len(center) != 2:
        return None
    if not keyword and not types:
        return None
    bbox = _radius_bbox(float(center[0]), float(center[1]), float(radius_m))
    if not _is_china_bbox(bbox):
        return None
    hit = _local_poi_chain(bbox, keyword=keyword, types=types, limit=limit)
    if hit is None:
        return None
    return hit


def try_local_web_poi(query: str, limit: int = 20) -> Optional[Dict[str, Any]]:
    """拦截 search_and_extract_poi：查询串里能解析出中国行政区就走本地链。"""
    if not local_query_first_enabled() or not query:
        return None
    place, rest = _split_place_and_keyword(query)
    if not place:
        return None
    bbox = admin_bbox_wgs84(place)
    if bbox is None or not _is_china_bbox(bbox):
        return None
    hit = _local_poi_chain(bbox, keyword=rest or query, limit=limit)
    if hit is None:
        return None
    out = dict(hit)
    out["type"] = "poi_web_search"
    out["query"] = query
    out["data"] = []
    out["message"] = "已用本地 POI 库检索，无需公网爬取。"
    return out
