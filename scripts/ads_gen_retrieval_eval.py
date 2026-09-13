#!/usr/bin/env python
"""Generate the ads-v1 retrieval eval set (DS2, ADR-0172).

Produces ``tests/data/ads2_retrieval_eval.json``: ≥200 labelled samples of
「自然语言查询 → 期望数据集卡片」built from the registry cards. Queries are
bilingual (Chinese/English) with paraphrases, synonym substitutions and typo
variants — the labelled answer is the card the query describes, derived from
the card's own declared text (no invented ground truth).

Deterministic (seeded); re-run to regenerate after registry card changes:

    ./.venv/Scripts/python scripts/ads_gen_retrieval_eval.py
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

OUT = REPO / "tests" / "data" / "ads2_retrieval_eval.json"

from app.services.data_fabric.retrieval.cards import build_cards  # noqa: E402

# Per-card hand-curated query seeds (realistic phrasings, incl. city scopes
# that disambiguate portal themes). Cards not listed here get template
# queries derived from their title/keywords.
SEEDS = {
    "local_osm/pois": ["附近的学校医院餐馆分布", "本地POI兴趣点数据", "查一下设施点数据", "offline POI data"],
    "local_osm/roads": ["道路路网数据", "本市街道网络", "交通路网矢量", "road network data"],
    "local_osm/railways": ["铁路线路数据", "地铁轨道分布", "高铁线路图", "railway lines"],
    "local_osm/waterways": ["河流水系数据", "河网渠道分布", "水体线要素", "river network"],
    "local_yearbook/township_stats": ["县域GDP人口统计", "乡镇经济指标", "各县财政收入对比", "county statistics GDP"],
    "planetary_computer/sentinel-2-l2a": ["长三角近五年哨兵2号影像", "植被遥感监测用什么卫星数据", "Sentinel-2 surface reflectance", "10米分辨率多光谱影像"],
    "planetary_computer/landsat-c2-l2": ["长时序土地变化卫星影像", "landsat 地表反射率", "Landsat 30米影像", "历史卫星影像对比"],
    "planetary_computer/cop-dem-glo-30": ["全球30米高程数据", "地形DEM哪里有", "数字高程模型做坡度分析", "Copernicus DEM 30m"],
    "planetary_computer/alos-dem": ["ALOS高程数据", "日本卫星DEM", "AW3D30 elevation"],
    "planetary_computer/io-lulc": ["土地利用分类数据", "城市扩张用地变化", "land use cover 10m", "年度地表覆盖"],
    "planetary_computer/naip": ["美国高分辨率航空影像", "NAIP imagery"],
    "planetary_computer/daymet-daily-na": ["北美逐日气温降水", "气象格网数据", "daily weather gridded data"],
    "planetary_computer/modis-13q1-061": ["植被指数NDVI数据", "植被长势监测", "MODIS vegetation index"],
    "copernicus_dataspace/sentinel-1-grd": ["雷达SAR影像洪水监测", "全天候卫星雷达数据", "Sentinel-1 SAR"],
    "copernicus_dataspace/sentinel-2-l1c": ["哨兵2号原始影像", "光学遥感数据", "sentinel-2 TOA"],
    "copernicus_dataspace/sentinel-3-olci": ["湖泊叶绿素水质遥感", "海洋水色数据", "olci water quality"],
    "copernicus_dataspace/sentinel-5p-l2": ["大气污染NO2浓度遥感", "二氧化硫柱浓度卫星", "空气污染卫星数据", "sentinel-5p no2"],
    "nasa_cmr_lpcloud/HLSL30_2.0": ["Landsat哨兵统一反射率产品", "HLS daily reflectance"],
    "nasa_cmr_lpcloud/MOD11A2_6.1": ["地表温度LST数据", "城市热岛遥感", "land surface temperature"],
    "nasa_cmr_lpcloud/SRTMGL1_3.0": ["SRTM高程", "航天飞机地形数据", "SRTM 30m dem"],
    "nasa_cmr_lpcloud/GPM_3IMERGHH_7": ["全球降水数据", "降雨格网产品", "precipitation IMERG"],
    "worldbank_api/WDI": ["世界各国发展指标", "宏观国际数据", "world development indicators"],
    "worldbank_api/SP.POP.TOTL": ["各国总人口数据", "世界人口统计", "country population statistics"],
    "worldbank_api/NY.GDP.MKTP.CD": ["各国GDP数据", "世界经济总量对比", "country GDP data"],
    "worldbank_api/EN.ATM.PM25.MC.M3": ["各国PM2.5浓度", "跨国空气污染对比", "pm2.5 exposure by country"],
    "worldbank_api/AG.LND.FRST.ZS": ["各国森林覆盖率", "森林面积占比数据", "forest coverage by country"],
    "worldbank_api/SP.URB.TOTL.IN.ZS": ["各国城市化率", "城镇人口比例", "urbanization rate"],
    "gbif_api/occurrence_search": ["鸟类观测记录分布", "生物多样性出现数据", "动植物标本点位", "species occurrence records"],
    "gbif_api/species_matchbackbone": ["物种学名解析", "分类学名称匹配", "taxonomic name matching"],
    "gbif_api/dataset_search": ["生物多样性数据集目录", "gbif dataset catalog"],
    "overpass_api/osm_features_query": ["在线查OpenStreetMap要素", "OSM在线查询", "openstreetmap features online"],
    "overpass_api/osm_amenities": ["在线查某城市餐饮设施", "OSM公共设施点", "amenity points from osm"],
    "beijing_gov/theme_air_quality": ["北京市空气质量监测站点", "北京AQI数据", "北京PM2.5监测"],
    "beijing_gov/theme_traffic": ["北京交通拥堵指数", "北京公交线路数据", "北京地铁网络"],
    "beijing_gov/theme_facilities": ["北京医院学校名录", "北京市公共服务设施"],
    "shanghai_gov/theme_air_quality": ["上海市空气质量数据", "上海空气监测站点"],
    "shanghai_gov/theme_population": ["上海人口普查数据", "上海就业统计"],
    "shanghai_gov/theme_medical": ["上海医院名录", "上海社区卫生中心"],
    "guangdong_gov/theme_air_quality": ["广东省空气质量数据", "广东空气监测"],
    "guangdong_gov/theme_water": ["广东水质监测数据", "饮用水水源地水质"],
    "guangdong_gov/theme_economy": ["广东各市GDP统计", "广东进出口经济数据"],
}

FRAMES = [
    "{q}",
    "帮我找{q}方面的数据",
    "有没有{q}的数据集？",
    "我想做{q}分析",
    "什么数据源有{q}？",
    "{q} dataset",
    "data about {q}",
    "where can I get {q} data",
    "推荐{q}数据",
]

TYPO_RULES = [
    ("数据", "数剧"), ("监测", "监侧"), ("卫星", "位星"), ("道路", "到路"),
    ("population", "populaton"), ("quality", "qualty"), ("traffic", "trafic"),
]


def make_queries(card) -> list[dict]:
    queries: list[str] = []
    seeds = SEEDS.get(card.card_id)
    if seeds:
        queries.extend(seeds)
    else:
        queries.append(card.title)
        queries.append(" ".join(card.keywords[:3]) if card.keywords else card.title)
    # templated paraphrases
    base = seeds[0] if seeds else card.title
    for frame in (FRAMES[1], FRAMES[2], FRAMES[6]):
        queries.append(frame.format(q=base))
    # one typo variant on the longest query (robustness lever)
    longest = max(queries, key=len)
    for wrong, right in TYPO_RULES:
        if wrong in longest:
            queries.append(longest.replace(wrong, right, 1))
            break
    else:
        queries.append(longest + " ")
    # dedupe, keep order
    seen = set()
    out = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def main() -> int:
    cards = build_cards()
    rng = random.Random(42)
    samples = []
    for card in cards:
        for q in make_queries(card):
            samples.append({"query": q, "expected": card.card_id, "source_card": card.card_id})
    rng.shuffle(samples)  # mixes card groups; deterministic via seed
    OUT.write_text(
        json.dumps({"version": "1.0", "n": len(samples), "samples": samples}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {OUT.relative_to(REPO)}: {len(samples)} samples over {len(cards)} cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
