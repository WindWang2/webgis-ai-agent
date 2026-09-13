"""标注 ground truth 数据集库（ac-05，ADR-0154）—— 10 个拟真 schema 的确定性合成数据集.

用途（P0/P7）：``choose_label_field`` 准确率回归的基准。每个数据集的
**schema 模仿一类真实 GIS 数据**（行政区划 / POI / 水系 / 轨交 / 传感网 /
地块 / 世界政区 / 公交 / 监测站 / 密集商贸点），字段命名覆盖中英混排、
ID / 编码 / 时间戳 / 长文本等诱饵字段；其中 ``sensors`` 刻意**不含任何
name-like 字段**（对应"无候选 → 不标注 + advisory"的退化契约）。

设计契约（与 ``tests/fixtures/gis_samples.py`` 同规）：
- **确定**：纯函数生成（显式 seed → np.random.default_rng），同参数两次
  构造逐位一致；不提交二进制数据、不拷贝真实数据（license clean —— 全合成，
  字段取值均为常见地名学样式的合成串）；
- **可生成**：调用即得（GeoJSON FeatureCollection dict），不落盘；
- **有人工判定列**：``GROUND_TRUTH`` 是人工判定的最佳标注字段（含
  ``None``=不标注），``docs/dev/ac-05-label-groundtruth.csv`` 记录逐字段
  理由，P7 以 90% 准确率为门禁。

字段诱饵谱系（每个数据集至少覆盖两类）：
- 主键/对象 ID：``OBJECTID`` / ``osm_id`` / ``station_id``（UUID）/ ``fid``
- 行政/业务编码：``code``（6 位区划码）/ ``parcel_no`` / ``ISO_A3``
- 时间戳：``更新时间`` / ``last_seen``
- 长文本：``address`` / ``first_last``（首末班时刻表）
- 低基数类别：``类别`` / ``zone`` / ``continent``（当标注会大量重复，非好字段）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

_WGS84 = "EPSG:4326"


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _fc(features: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"type": "FeatureCollection", "features": features, "crs": _WGS84}


def _pt(lon: float, lat: float) -> Dict[str, Any]:
    return {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}


def _poly(x0: float, y0: float, dx: float, dy: float) -> Dict[str, Any]:
    return {"type": "Polygon", "coordinates": [[
        [x0, y0], [x0 + dx, y0], [x0 + dx, y0 + dy], [x0, y0 + dy], [x0, y0],
    ]]}


# ── 1. 中国省级行政区（polygon，CJK 名称最优）────────────────────────────
_CN_PROVINCES = [
    ("北京市", "Beijing", "110000", 116.40, 39.90, 16410, 43760),
    ("上海市", "Shanghai", "310000", 121.47, 31.23, 6341, 47219),
    ("广东省", "Guangdong", "440000", 113.26, 23.13, 179800, 135673),
    ("四川省", "Sichuan", "510000", 104.07, 30.67, 486000, 60132),
    ("黑龙江省", "Heilongjiang", "230000", 126.53, 45.80, 473000, 15838),
    ("云南省", "Yunnan", "530000", 102.71, 25.04, 394100, 30021),
    ("陕西省", "Shaanxi", "610000", 108.95, 34.27, 205800, 33786),
    ("浙江省", "Zhejiang", "330000", 120.15, 30.28, 105500, 82553),
    ("湖北省", "Hubei", "420000", 114.30, 30.59, 185900, 55804),
    ("甘肃省", "Gansu", "620000", 103.83, 36.06, 425800, 11865),
    ("新疆维吾尔自治区", "Xinjiang", "650000", 87.62, 43.79, 1660000, 17741),
    ("内蒙古自治区", "InnerMongolia", "150000", 111.67, 40.82, 1183000, 23600),
]


def cn_provinces() -> Dict[str, Any]:
    """省级行政区面：``名称`` 最优；诱饵 = OBJECTID / code / 更新时间。"""
    features = []
    for i, (cn, en, code, lon, lat, area, gdp) in enumerate(_CN_PROVINCES, 1):
        features.append({
            "type": "Feature",
            "geometry": _poly(lon - 1.5, lat - 1.0, 3.0, 2.0),
            "properties": {
                "OBJECTID": i, "code": code, "名称": cn, "name_en": en,
                "面积_km2": area, "gdp_2023": gdp, "更新时间": "2026-01-0%d" % (i % 9 + 1),
            },
        })
    return _fc(features)


# ── 2. 地级市点位（point，name 最优；address 长文本诱饵）─────────────────
_CN_CITIES = [
    ("成都市", "Chengdu", 20940, "四川省成都市锦江区人民南路一段86号"),
    ("绵阳市", "Mianyang", 4868, "四川省绵阳市涪城区临园路东段72号"),
    ("宜宾市", "Yibin", 4589, "四川省宜宾市翠屏区蜀南大道南段15号"),
    ("南充市", "Nanchong", 5604, "四川省南充市顺庆区万年西路1号"),
    ("泸州市", "Luzhou", 4254, "四川省泸州市江阳区江阳西路39号"),
    ("德阳市", "Deyang", 3462, "四川省德阳市旌阳区长江东路101号"),
    ("自贡市", "Zigong", 2489, "四川省自贡市自流井区汇东路1号"),
    ("攀枝花市", "Panzhihua", 1214, "四川省攀枝花市东区炳草岗大街10号"),
    ("乐山市", "Leshan", 3260, "四川省乐山市市中区滨河路98号"),
    ("内江市", "Neijiang", 3141, "四川省内江市市中区翔龙山路段2号"),
    ("遂宁市", "Suining", 2817, "四川省遂宁市船山区嘉禾东路55号"),
    ("广元市", "Guangyuan", 2308, "四川省广元市利州区利州东路一段606号"),
]


def cn_cities_points() -> Dict[str, Any]:
    """地级市点：``name`` 最优；``类别`` 低基数、``address`` 长文本。"""
    rng = _rng(7)
    features = []
    for i, (cn, en, pop, addr) in enumerate(_CN_CITIES):
        lon = 102.0 + rng.uniform(0, 3.5)
        lat = 28.5 + rng.uniform(0, 3.0)
        features.append({
            "type": "Feature",
            "geometry": _pt(lon, lat),
            "properties": {
                "osm_id": 100000 + i * 137, "name": cn, "name_en": en,
                "类别": "地级市", "population": pop, "address": addr,
            },
        })
    return _fc(features)


# ── 3. 河流线（line，NAME 最优）──────────────────────────────────────────
_RIVERS = [
    ("长江", 6300, 1), ("黄河", 5464, 2), ("黑龙江", 4444, 2), ("珠江", 2320, 2),
    ("澜沧江", 4880, 2), ("雅鲁藏布江", 2057, 2), ("怒江", 3240, 2), ("汉江", 1577, 3),
    ("湘江", 856, 3), ("赣江", 766, 3), ("岷江", 735, 3), ("嘉陵江", 1119, 3),
]


def rivers() -> Dict[str, Any]:
    """河流线：``NAME`` 最优；``order`` / ``scalerank`` 是数值分级诱饵。"""
    rng = _rng(11)
    features = []
    for i, (nm, length, order) in enumerate(_RIVERS):
        x = 100.0 + i * 0.6
        y = 28.0 + rng.uniform(0, 4.0)
        coords = [[round(x, 4), round(y, 4)], [round(x + 0.3, 4), round(y + 0.4, 4)],
                  [round(x + 0.6, 4), round(y + 0.2, 4)]]
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "fid": 9000 + i, "NAME": nm, "length_km": length,
                "order": order, "scalerank": order,
            },
        })
    return _fc(features)


# ── 4. 英文轨交站点（point，title 最优；UUID 诱饵）───────────────────────
_METRO = [
    ("King's Cross St Pancras", "Circle/Piccadilly/Victoria", 1),
    ("Oxford Circus", "Central/Bakerloo/Victoria", 1),
    ("Waterloo", "Jubilee/Northern/Bakerloo", 1),
    ("Bank", "Central/Northern/DLR", 1),
    ("Westminster", "Jubilee/District/Circle", 1),
    ("Paddington", "Bakerloo/Circle/District", 1),
    ("Liverpool Street", "Central/Circle/Hammersmith", 1),
    ("Stratford", "Central/Jubilee/DLR", 2),
    ("Canary Wharf", "Jubilee", 2),
    ("Green Park", "Jubilee/Victoria/Piccadilly", 1),
    ("Holborn", "Central/Piccadilly", 2),
    ("Euston", "Northern/Victoria", 2),
]


def metro_stations_en() -> Dict[str, Any]:
    """英文轨交站：``title`` 最优（无 name 字段 —— 词汇次序的 title 档）。"""
    import uuid
    rand = np.random.default_rng(23)
    features = []
    for i, (nm, line, zone) in enumerate(_METRO):
        station_id = str(uuid.UUID(bytes=rand.bytes(16)))
        features.append({
            "type": "Feature",
            "geometry": _pt(-0.12 + float(np.random.default_rng(100 + i).uniform(0, 0.12)),
                            51.49 + float(np.random.default_rng(200 + i).uniform(0, 0.08))),
            "properties": {
                "station_id": station_id, "title": nm, "line": line,
                "zone": zone, "daily_ridership": int(rand.integers(20, 400)) * 1000,
            },
        })
    return _fc(features)


# ── 5. 传感网（point，**无 name-like 字段** —— 必须退化为不标注）─────────
def sensors(n: int = 60) -> Dict[str, Any]:
    """IoT 传感点：device_id 为 hex、无任何自然语言名称字段。"""
    rng = _rng(31)
    features = []
    for i in range(n):
        features.append({
            "type": "Feature",
            "geometry": _pt(120.0 + rng.uniform(0, 0.5), 30.0 + rng.uniform(0, 0.4)),
            "properties": {
                "device_id": "%08x" % int(rng.integers(0, 2 ** 32)),
                "model": rng.choice(["S-100", "S-200", "S-300"]),
                "battery_pct": int(rng.integers(5, 100)),
                "last_seen": "2026-09-%02dT%02d:00:00Z" % (int(rng.integers(1, 29)), int(rng.integers(0, 24))),
                "value": round(float(rng.uniform(0, 100)), 1),
            },
        })
    return _fc(features)


# ── 6. 地块（polygon，CJK 名称最优但有 12% 空值；parcel_no 编码诱饵）─────
_LAND_USES = ["住宅用地", "商业用地", "工业用地", "绿地", "公共服务"]


def land_parcels(n: int = 40) -> Dict[str, Any]:
    """地块面：``地块名称`` 最优（含少量空值）；``用途`` 低基数诱饵。"""
    rng = _rng(43)
    features = []
    for i in range(n):
        x0 = 104.0 + (i % 8) * 0.02
        y0 = 30.5 + (i // 8) * 0.02
        nm = f"{rng.choice(['锦江', '银杏', '望江', '青龙', '金沙'])}{i + 1}号地块" if rng.random() > 0.12 else None
        features.append({
            "type": "Feature",
            "geometry": _poly(x0, y0, 0.018, 0.015),
            "properties": {
                "parcel_no": "CD-%04d-%02d" % (2024, i + 1),
                "地块名称": nm,
                "用途": str(rng.choice(_LAND_USES)),
                "area_ha": round(float(rng.uniform(0.5, 12.0)), 2),
            },
        })
    return _fc(features)


# ── 7. 世界政区（polygon，混排双名称 —— exact 命中优先于子串命中）─────────
_COUNTRIES = [
    ("France", "法兰西共和国", "FRA", 68042, "Europe"),
    ("Germany", "德意志联邦共和国", "DEU", 44561, "Europe"),
    ("Japan", "日本国", "JPN", 42129, "Asia"),
    ("Brazil", "巴西联邦共和国", "BRA", 217367, "South America"),
    ("Australia", "澳大利亚联邦", "AUS", 172358, "Oceania"),
    ("India", "印度共和国", "IND", 355016, "Asia"),
    ("South Africa", "南非共和国", "ZAF", 37753, "Africa"),
    ("Mexico", "墨西哥合众国", "MEX", 178889, "North America"),
    ("Indonesia", "印度尼西亚共和国", "IDN", 137117, "Asia"),
    ("Egypt", "阿拉伯埃及共和国", "EGY", 395913, "Africa"),
]


def countries_mixed() -> Dict[str, Any]:
    """世界政区：``name``（exact 词汇命中）优于 ``中文名称``（子串命中）。"""
    rng = _rng(5)
    features = []
    for i, (latn, formal, iso3, gdp, cont) in enumerate(_COUNTRIES):
        features.append({
            "type": "Feature",
            "geometry": _poly(-20 + i * 6.0, -10 + (i % 3) * 8.0, 5.0, 4.0),
            "properties": {
                "FID": i + 1, "ISO_A3": iso3, "name": latn,
                "中文名称": formal, "pop_est": int(rng.integers(5, 1450)) * 10 ** 4,
                "gdp_md": gdp, "continent": cont,
            },
        })
    return _fc(features)


# ── 8. 公交线路（line，CJK 线路名最优；首末班长文本诱饵）─────────────────
_ROUTES = [
    ("1路", "06:00-22:30", 22), ("16路", "06:00-23:00", 15), ("52路", "05:30-22:00", 18),
    ("99路", "06:15-21:30", 12), ("夜班7路", "22:30-02:00", 6), ("快速公交K1", "06:00-22:00", 25),
    ("机场专线", "05:00-22:00", 10), ("观光3路", "08:00-18:00", 4),
]


def bus_routes() -> Dict[str, Any]:
    """公交线路：``线路名`` 最优；``first_last`` 长文本、``fleet_count`` 数值。"""
    features = []
    for i, (nm, hours, fleet) in enumerate(_ROUTES):
        y = 30.55 + i * 0.012
        coords = [[104.02, round(y, 4)], [104.10, round(y + 0.006, 4)],
                  [104.18, round(y - 0.004, 4)], [104.26, round(y + 0.002, 4)]]
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "route_id": 700 + i * 3, "线路名": nm,
                "first_last": f"{hours}（首末班）", "fleet_count": fleet,
            },
        })
    return _fc(features)


# ── 9. 空气质量监测站（point，CJK 站点名最优；city 近似诱饵）─────────────
_AQ_CITIES = ["成都市", "重庆市", "西安市", "武汉市", "杭州市"]


def air_quality_stations(n: int = 36) -> Dict[str, Any]:
    """监测站：``站点名称`` 最优；``city`` 跨站重复（基数惩罚必须压过它）。"""
    rng = _rng(29)
    site_names = ["人民公园", "高新西区", "沙坪坝", "高新", "城东", "滨江",
                  "文殊坊", "火车北站", "科学城", "两江", "曲江", "光谷"]
    features = []
    for i in range(n):
        city = str(_AQ_CITIES[i % len(_AQ_CITIES)])
        features.append({
            "type": "Feature",
            "geometry": _pt(100.0 + rng.uniform(0, 12.0), 28.0 + rng.uniform(0, 6.0)),
            "properties": {
                "station_code": "AQ-%s-%03d" % (chr(65 + i % 26), i + 1),
                "站点名称": f"{city}{site_names[i % len(site_names)]}-{i + 1:03d}站",
                "city": city,
                "aqi": int(rng.integers(20, 220)),
                "pm25": int(rng.integers(5, 160)),
            },
        })
    return _fc(features)


# ── 10. 密集商贸点（point，3000+ 要素 —— top_n 策略 + collision 基线）─────
_BRANDS = ["星选便利", "惠民超市", "优选生鲜", "邻里药房", "快客便利", "万家粮油"]


def hotels_dense(n: int = 3200) -> Dict[str, Any]:
    """密集商贸 POI（默认 3200 点 > 2000 阈值）：``name`` 最优 + brand 低基数。"""
    rng = _rng(97)
    rand = np.random.default_rng(97)
    features = []
    for i in range(n):
        stars = int(rand.integers(0, 5)) + 1
        features.append({
            "type": "Feature",
            "geometry": _pt(104.0 + rng.uniform(0, 0.30), 30.55 + rng.uniform(0, 0.22)),
            "properties": {
                "id": 500000 + i,
                "name": f"{str(rng.choice(_BRANDS))}·{chr(65 + i % 26)}{i % 97:02d}店",
                "brand": str(rng.choice(_BRANDS)),
                "stars": stars,
                "price": int(rand.integers(80, 1200)),
            },
        })
    return _fc(features)


# ── ground truth 注册表（人工判定；P7 准确率基准的单一事实源）────────────
#: dataset_id → (builder, 几何, 最佳标注字段 | None, 判定理由摘要)
GROUND_TRUTH: Dict[str, Dict[str, Any]] = {
    "cn_provinces": {
        "builder": cn_provinces, "geometry": "polygon", "best_field": "名称",
        "rationale": "_exact_vocab_hit_cjk_标签短且基数全唯一；OBJECTID/code/更新时间 均为诱饵",
    },
    "cn_cities_points": {
        "builder": cn_cities_points, "geometry": "point", "best_field": "name",
        "rationale": "exact_vocab_hit；address 长文本、类别 低基数、osm_id 主键诱饵",
    },
    "rivers": {
        "builder": rivers, "geometry": "line", "best_field": "NAME",
        "rationale": "exact_vocab_hit（大小写归一）；order/scalerank 数值分级、fid 主键",
    },
    "metro_stations_en": {
        "builder": metro_stations_en, "geometry": "point", "best_field": "title",
        "rationale": "无 name 字段 → 词汇次序落到 title；station_id 为 UUID 诱饵",
    },
    "sensors": {
        "builder": sensors, "geometry": "point", "best_field": None,
        "rationale": "无任何 name-like 字段（device_id 为 hex）→ 退化不标注 + advisory",
    },
    "land_parcels": {
        "builder": land_parcels, "geometry": "polygon", "best_field": "地块名称",
        "rationale": "exact_vocab_hit（含 '名称' 子词）；parcel_no 业务编码诱饵、用途 低基数",
    },
    "countries_mixed": {
        "builder": countries_mixed, "geometry": "polygon", "best_field": "name",
        "rationale": "name exact 命中 > 中文名称 子串命中（确定性 tie-break 的最短路径）",
    },
    "bus_routes": {
        "builder": bus_routes, "geometry": "line", "best_field": "线路名",
        "rationale": "exact_vocab_hit（含 '名称' 子词）；first_last 长文本、fleet_count 数值",
    },
    "air_quality_stations": {
        "builder": air_quality_stations, "geometry": "point", "best_field": "站点名称",
        "rationale": "exact_vocab_hit；city 跨站重复（低基数惩罚必须压过类名语义）",
    },
    "hotels_dense": {
        "builder": hotels_dense, "geometry": "point", "best_field": "name",
        "rationale": "exact_vocab_hit；id 主键、brand 低基数、stars/price 数值",
    },
}


def build_dataset(dataset_id: str) -> Dict[str, Any]:
    """按 id 构造数据集（未注册 id 抛 KeyError —— 测试白名单语义）。"""
    entry = GROUND_TRUTH[dataset_id]
    return entry["builder"]()


def ground_truth_field(dataset_id: str) -> Optional[str]:
    """人工判定的最佳标注字段（None = 不标注）。"""
    return GROUND_TRUTH[dataset_id]["best_field"]


__all__ = [
    "GROUND_TRUTH",
    "build_dataset",
    "ground_truth_field",
    "cn_provinces", "cn_cities_points", "rivers", "metro_stations_en",
    "sensors", "land_parcels", "countries_mixed", "bus_routes",
    "air_quality_stations", "hotels_dense",
]
