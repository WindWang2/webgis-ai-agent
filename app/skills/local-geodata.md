---
name: local-geodata
description: 本地地理数据资源优先——中国行政区边界与 OSM 主题查询（离线、秒级、稳定）
---

# 本地地理数据（Local Geodata）

中国境内的地理查询**优先走本地数据资源**（离线可用、秒级响应、无配额限制），
在线 API（amap/geocode_cn/Overpass）作为回退。本地资源分两类：

1. **行政区边界**（四级 SHP，来源 ChinaAdminDivisonSHP，坐标系 GCJ-02 与 amap 同系）
2. **OSM 主题数据**（全国 PBF 预处理为 GPKG：pois / roads / railways / waterways，坐标系 WGS84）

## 执行步骤

1. 判断需求类型：
   - 要**行政区边界/轮廓/下级区划**（省、市、区县）→ 步骤 2；
   - 要**某范围内的设施/道路/水系**（POI、路网）→ 步骤 3。
2. 行政区查询：
   - `get_local_admin_boundary(name='成都市', level='city')`——名称模糊或 `adcode='510100'` 精确；
   - 下级区划用 `get_local_child_districts(parent_name='成都市', parent_level='city')`；
   - 省级以上大边界加 `simplified=true` 防超大 payload；与 MapSpec/在线 WGS 数据叠加分析时加 `to_wgs84=true`。
3. OSM 主题查询（典型组合打法）：
   - 先用步骤 2 拿到目标行政区边界，取其 `total_bounds` 作为 bbox；
   - `query_local_osm(theme='pois', bbox=[...], tag='amenity=restaurant', limit=200)`；
   - 不确定主题是否已导入时先 `get_local_osm_catalog()`。
4. 结果制图：把查询返回的 FeatureCollection 通过 `webgis_layer_upsert` 挂载为图层
   （工具结果携带 geojson ref，可直接作为 source_data）。
5. 本地未命中（数据缺失/范围在中国境外）→ 回退在线工具：行政区用 `get_admin_division`，
   POI 用 `search_poi`，路网用 Overpass 系工具。

## 注意

- 坐标系差异：行政区是 **GCJ-02**（与 amap 系数据天然一致），OSM 主题是 **WGS84**——
  两者叠加前必须统一（行政区加 `to_wgs84=true`，或用 `coord_transform` 转换另一方）。
- bbox 过大 + limit 过高会返回巨量要素：先用行政区收窄范围，必要时缩小 bbox 分片查询。
- 本地数据是快照（行政区 2024.02 版、OSM 按下载日期）；要最新状态时改用在线工具。
- 数据预处理/更新：`python manage.py osm-ingest`（OSM 主题），行政区替换
  `LOCAL_GEODATA_DIR/ChinaAdminDivisonSHP` 目录即可。
