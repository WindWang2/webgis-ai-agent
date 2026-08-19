---
name: local-stats
description: 本地统计与 POI 数据库——县域/乡镇年鉴指标 + 高德 POI（WGS84）离线秒查
---

# 本地统计与 POI（Local Stats & POI）

中国境内的**社会经济统计**与**兴趣点检索**优先走本地数据库（离线、秒级），
在线检索仅作本地未命中的兜底。两类资源：

> **POI 主次策略（用户约定）**：POI 检索**以高德库 `query_local_poi` 为主力**
> （5174 万点、中文商户名全）；**OSM POI 为补充**——gd 库查不到、或需要
> OSM 特有标签语义（amenity= 精确匹配）时才用 `query_local_osm(theme='pois')`。
> `search_poi` / `search_poi_around` / `search_poi_polygon` / `query_osm_poi`
> 等在线工具已内置「gd_poi → OSM → 在线」三级链，本地命中即拦截出网；
> `query_local_osm` 的 pois 主题在不带标签时也会自动先查高德库。

1. **统计年鉴**（`yearbook.sqlite`）：乡镇卷 2014-2025（乡镇粒度，出版年 N 的
   数据年≈N-1）+ 县域面板 2000-2024（县级粒度，75+ 指标时间序列）；
   行已与行政区 **adcode 连接**，可直接挂边界/下钻。
2. **高德 POI**（`gd_pois.gpkg`）：全国 POI 点库，**已从 GCJ-02 转为 WGS84**，
   与 OSM/行政区(WGS) 同系可直接叠加；中文商户名比 OSM 全。

## 执行步骤

1. 先 `get_local_stats_catalog()` 确认年份覆盖/连接率/POI 省份。
2. 统计问题选 dataset：
   - 乡镇粒度（人口/面积/企业/村委会个数）→
     `query_local_yearbook(dataset='township', name='唐昌镇', year=2024)`；
     区县整体下钻用 `adcode='510124'`（返回该区县全部乡镇）；
   - 县级时间序列（GDP/财政/教育/医疗 2000-2024）→
     `query_local_yearbook(dataset='county_panel', name='金堂县', year=2010, year_to=2023,
     indicators='地区生产总值(万元),常住人口?')`（indicators 省略则返回全部）。
3. POI 检索：**行政区查询用 district（adcode 精确归属，不要 bbox）**——
   `query_local_poi(district='成都市', category='医疗保健服务', subtype='三级甲等')`。
   subtype 取「大类;小类」的小类段（高校=`高等院校`，另如 小学/中学/幼儿园/
   职业技术学校/科研机构/培训机构）；口语词（大学/高校/初中/高中）自动别名映射，
   零命中时结果附该范围真实子类分布提示。命中数超 limit 时服务端按 fid 均匀采样
   （total_matched 字段为真实命中数，空间覆盖整个查询范围）。
   名称类检索 `name_like='海底捞'`；跨省同名乡镇中心点用
   `get_township_center(name, adcode)`。
4. 制图：POI 结果是 WGS84 FeatureCollection，直接 `webgis_layer_upsert`；
   年鉴行用 district_adcode 调 `get_local_admin_boundary(adcode=..., to_wgs84=true)`
   挂边界，或 `get_township_center` 挂点。

## 注意

- **坐标系**：本 skill 全部产物为 WGS84；高德 POI 已在入库时用迭代逆变换
  （精度 ~1m）从 GCJ-02 转换，勿重复转换。行政区工具默认 GCJ-02，叠加时传
  `to_wgs84=true`。
- **年份语义**：township 的 `year` 是出版年份，数据年≈前一年；county_panel 的
  `year` 是数据年份本身。跨年对比时用 year_to 取区间。
- **连接率**：乡镇行按区县名前缀连接 district.shp；个别撤县设区/更名区县可能
  adcode 为空（连接率见 catalog），此时退回 full_name 文本匹配。
- **POI 覆盖**：gd 库不含广东省（数据源缺失），广东 POI 用 query_local_osm 或
  search_poi 在线回退；港澳台在 gd_81_820000 包内。
- 数据更新：`python manage.py yearbook-ingest` / `python manage.py gd-poi-ingest`
  （按年/按省幂等，--force 重建）。
