"""中文 POI 类别 → OSM 标签的单一事实来源（G-4 / #868）。

此前 `app/tools/osm.py` 的 category_map 与 `app/services/local_first.py`
的 _CATEGORY_TO_TAG 各自维护且已失同步，且小学/中学映射到**非文档化**
标签（amenity=primary_school / secondary_school，全球用量可忽略，
Overpass 召回≈0）、超市/商场误挂 amenity 键（标准是 shop）、地铁/火车
站误挂 amenity=station（标准是 railway=station）。

本模块是唯一权威表：
- `CHINESE_CATEGORY_TAGS`：中文词 → (OSM key, value)，标准标签；
- `OVERPASS_STAGE_NARROW`：学段词的 Overpass 窄化（amenity=school +
  school~primary/elementary 等），0 命中时调用方应放宽回全量 school；
- `NOMINATIM_TERMS`：Nominatim 兜底搜索的中英词对。

消费方：app/tools/osm.py（Overpass 出网）与 app/services/local_first.py
（本地 GPKG）。新增类别只改这里。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# 中文类别 → 标准 OSM 标签（key, value）
CHINESE_CATEGORY_TAGS: Dict[str, Tuple[str, str]] = {
    "大学": ("amenity", "university"),
    "高校": ("amenity", "university"),
    "高等学校": ("amenity", "university"),
    "学院": ("amenity", "college"),
    "学校": ("amenity", "school"),
    "中小学": ("amenity", "school"),
    # G-4（#868）：学段区分在 OSM 是 amenity=school + school=* 子标签，
    # 不是独立的 amenity 值（primary_school/secondary_school 非文档化）。
    "小学": ("amenity", "school"),
    "中学": ("amenity", "school"),
    "初中": ("amenity", "school"),
    "高中": ("amenity", "school"),
    "幼儿园": ("amenity", "kindergarten"),
    "托儿所": ("amenity", "kindergarten"),
    "医院": ("amenity", "hospital"),
    "诊所": ("amenity", "clinic"),
    "餐厅": ("amenity", "restaurant"),
    "餐馆": ("amenity", "restaurant"),
    "饭店": ("amenity", "restaurant"),
    "银行": ("amenity", "bank"),
    "咖啡": ("amenity", "cafe"),
    "咖啡厅": ("amenity", "cafe"),
    "咖啡店": ("amenity", "cafe"),
    "酒吧": ("amenity", "bar"),
    "公园": ("leisure", "park"),
    "花园": ("leisure", "garden"),
    "酒店": ("tourism", "hotel"),
    "宾馆": ("tourism", "hotel"),
    "旅馆": ("tourism", "hotel"),
    "博物馆": ("tourism", "museum"),
    "图书馆": ("amenity", "library"),
    "药店": ("amenity", "pharmacy"),
    "药房": ("amenity", "pharmacy"),
    "加油站": ("amenity", "fuel"),
    "停车场": ("amenity", "parking"),
    "公交站": ("amenity", "bus_station"),
    "汽车站": ("amenity", "bus_station"),
    "派出所": ("amenity", "police"),
    "警察局": ("amenity", "police"),
    "消防站": ("amenity", "fire_station"),
    "消防局": ("amenity", "fire_station"),
    "邮局": ("amenity", "post_office"),
    "剧院": ("amenity", "theatre"),
    "剧场": ("amenity", "theatre"),
    "电影院": ("amenity", "cinema"),
    "体育馆": ("leisure", "sports_centre"),
    "体育场": ("leisure", "stadium"),
    "游泳池": ("leisure", "swimming_pool"),
    # shop 键族（G-4：此前误挂 amenity=supermarket/mall，非标准标签）
    "超市": ("shop", "supermarket"),
    "商场": ("shop", "mall"),
    "菜市场": ("amenity", "marketplace"),
    # railway 键族（G-4：此前误挂 amenity=station）
    "地铁站": ("railway", "station"),
    "火车站": ("railway", "station"),
    "寺庙": ("amenity", "place_of_worship"),
    "教堂": ("amenity", "place_of_worship"),
}

# 学段窄化（Overpass）：amenity=school + school~正则。窄化 0 命中时调用方
# 应放宽回全量 amenity=school（大量学校未标注 school 子标签）。
OVERPASS_STAGE_NARROW: Dict[str, str] = {
    "小学": "primary|elementary",
    "中学": "secondary|middle",
    "初中": "secondary|middle",
    "高中": "secondary|middle",
}

# Nominatim 兜底搜索词（G-4：补齐此前缺失的新增类别词条）
NOMINATIM_TERMS: Dict[str, List[str]] = {
    "park": ["park", "公园"],
    "garden": ["garden", "花园"],
    "school": ["school", "学校"],
    "hospital": ["hospital", "医院"],
    "clinic": ["clinic", "诊所"],
    "restaurant": ["restaurant", "餐厅"],
    "bank": ["bank", "银行"],
    "hotel": ["hotel", "酒店"],
    "museum": ["museum", "博物馆"],
    "cafe": ["cafe", "咖啡店"],
    "pharmacy": ["pharmacy", "药店"],
    "library": ["library", "图书馆"],
    "university": ["university", "大学"],
    "college": ["college", "学院"],
    "kindergarten": ["kindergarten", "幼儿园"],
    "police": ["police", "警察局"],
    "fire_station": ["fire station", "消防站"],
    "post_office": ["post office", "邮局"],
    "bus_station": ["bus station", "公交站"],
    "parking": ["parking", "停车场"],
    "fuel": ["fuel", "加油站"],
    "supermarket": ["supermarket", "超市"],
    "mall": ["mall", "商场"],
    "marketplace": ["marketplace", "菜市场"],
    "station": ["station", "车站"],
    "place_of_worship": ["temple", "寺庙"],
}


def tag_string(key: str, value: str) -> str:
    """本地 GPKG 查询用的 `key=value` 形态。"""
    return f"{key}={value}"


def chinese_category_tag_string(category: str) -> str:
    """中文类别 → `key=value`（未映射返回原文）。"""
    spec = CHINESE_CATEGORY_TAGS.get(category)
    return tag_string(*spec) if spec else category
