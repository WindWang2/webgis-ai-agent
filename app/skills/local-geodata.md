---
name: local-geodata
description: 本地地理数据资源优先——中国行政区边界与 OSM 主题查询（离线、秒级、稳定）
---

# 本地地理数据（Local Geodata）

中国境内的地理查询**优先走本地数据资源**（离线可用、秒级响应、无配额限制），
在线 API（amap/geocode_cn/Overpass）作为回退。本地资源分两类：

1. **行政区边界**（四级 SHP，来源 ChinaAdminDivisonSHP，坐标系 GCJ-02 与 amap 同系）
2. **OSM 主题数据**（全国 PBF 预处理为 GPKG：pois / roads / railways / waterways，坐标系 WGS84）

统计年鉴与高德 POI 见另一技能 **local-stats**（`get_local_stats_catalog` 查覆盖）。

## 执行步骤

1. 判断需求类型：
   - 要**行政区边界/轮廓/下级区划**（省、市、区县）→ 步骤 2；
   - 要**某范围内的设施/POI** → 步骤 3；要**路网/铁路/水系** → 步骤 4；
2. 行政区查询：
   - `get_local_admin_boundary(name='成都市', level='city')`——名称模糊或 `adcode='510100'` 精确；
   - 下级区划用 `get_local_child_districts`（parent_name=成都市, parent_level=city）；
   - 省级以上大边界加 simplified=true 防超大 payload；与 MapSpec/在线 WGS 数据叠加分析时加 to_wgs84=true。
3. **POI 检索 → 高德库为主**（详见 local-stats 技能）：直接用
   `query_local_poi(district='成都市', category='科教文化服务', subtype='高等院校')`。
   subtype 是「大类;小类」的小类段（高校=高等院校、小学/中学/幼儿园、职业技术学校、
   科研机构、培训机构…），口语词（大学/高校/初中/高中）自动别名映射；零命中时结果
   会提示该范围真实子类分布。**行政区查询用 district（adcode 归属），不要用 bbox**——
   bbox 矩形会把邻区边角点带进来。结果超出 limit 时服务端按 fid 均匀采样
   （total_matched 给真实命中数），不会只取某个区县；
   OSM 仅作补充——`query_local_osm(theme='pois', tag='amenity=school')` 会自动
   翻译标签先查高德库，gd 查不到才回落 OSM。
4. **路网/铁路/水系 → OSM 专属**：
   - `query_local_osm(theme='roads', bbox=[...], tag='highway=primary')`；
   - 不确定主题是否已导入时先 `get_local_osm_catalog()`。
5. 结果制图：把查询返回的 FeatureCollection 通过 `webgis_layer_upsert` 挂载为图层
   （工具结果携带 geojson ref，可直接作为 source_data）。
6. 本地未命中（数据缺失/范围在中国境外）→ 回退在线工具：行政区用 `get_admin_division`，
   POI 用 `search_poi`，路网用 Overpass 系工具。

## 注意

- 坐标系差异：行政区是 **GCJ-02**（与 amap 系数据天然一致），OSM 主题是 **WGS84**——
  两者叠加前必须统一（行政区加 to_wgs84=true，或用坐标转换工具转换另一方）。
- bbox 过大 + limit 过高会返回巨量要素：先用行政区收窄范围，必要时缩小 bbox 分片查询。
- 本地数据是快照（行政区 2024.02 版、OSM 按下载日期）；要最新状态时改用在线工具。
- 数据预处理/更新：`python manage.py osm-ingest`（OSM 主题），行政区替换
  `LOCAL_GEODATA_DIR/ChinaAdminDivisonSHP` 目录即可。
