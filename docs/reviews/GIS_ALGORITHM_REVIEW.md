# GIS 算法审查报告

- **审查日期**: 2026-08-24
- **审查员**: Agent B（GIS 算法与空间分析正确性专项）
- **范围**: `app/tools/` 空间分析工具、`app/services/rs/` 遥感、`app/services/mapspec/` + `app/lib/cartography/` 分类与制图契约、数据获取链路（geocoding / OSM / chinese_maps / 本地 POI 库）、热力图/聚类/密度估计算法
- **方法**: 逐文件精读核心算法实现（约 9000 行）→ 对照 `tests/unit/` 边界用例 → 对可疑算法做本地数值验证（2-opt 增量式 fuzz 600 组非对称矩阵、pyogrio bbox+max_features 截断实证、聚类量纲混合定量测算）
- **背景**: 3 轮历史审计（CODE_REVIEW.md + 代码内 #381~#823 系列修复注释）后 open issues 为 0。本次只报告**仍然存在**的问题，每条均有 file:line 证据，宁缺毋滥。

---

## 1. 「成都小学分布」任务的真实执行链还原（每环 file:line）

以用户输入「成都小学分布情况」为例，从代码还原当前系统真实执行路径：

| # | 环节 | 真实实现 (file:line) | 现状与缺陷 |
|---|------|----------------------|-----------|
| 1 | **自然语言理解 / 工具决策** | 系统提示词 `app/services/chat/prompt.py:35-129`；LLM 工具循环 `app/services/chat/execution_engine.py`（dispatch 缝 `app/services/tool_dispatch_service.py:430-523`） | prompt.py:55 明确「分布情况」→ 优先 `heatmap_data(render_type='native')`；:57-61「精准分析协议」要求先 `get_local_admin_boundary` 再取 POI 再 clip。**缺陷**: :57 未提醒边界返回的是 GCJ-02（见 G-2） |
| 2 | **GIS 语义解析 / 计划** | `webgis_map_intent` `app/services/gis_harness/tools.py:121-135` → 确定性 recipe 选择 `app/services/gis_harness/planner.py:174-227`（无命中兜底 `poi_distribution_overview`，planner.py:188-191） | recipe `poi_distribution_overview` `app/services/gis_harness/recipes.py:227-263`：主表达 visual_heatmap，点数<10 确定性降级点图。此环节设计良好，无缺陷 |
| 3 | **空间范围确定 (geocoding)** | 首选 `get_local_admin_boundary` `app/tools/local_admin.py:241-253`（默认 `to_wgs84=False` → 返回 **GCJ-02** 且 FC 无 `crs` 成员，local_admin.py:138-147）；本地 POI 拦截路径则用 `admin_bbox_wgs84` `app/services/local_first.py:195-202`（正确转 WGS84）；出网兜底 Nominatim `_geocode_bbox` `app/tools/osm.py:103-149` | **缺陷 G-2**: agent 直接调用工具时默认拿到 GCJ-02 边界，与下游 WGS84 POI 混叠 ~100-600m；#599/#813 建立的 `crs` 成员契约在此出口未生效 |
| 4 | **数据获取 (POI)** | `query_osm_poi` `app/tools/osm.py:219-376`：① 本地拦截 `try_local_osm_poi` osm.py:220-224 → `_local_poi_chain` local_first.py:448-473 → 高德分类提示 `_GD_KEYWORD_HINTS` local_first.py:117-139（"小学"→subtype=小学）→ `query_gd_poi(bbox=行政bbox)` `app/services/local_poi.py:593-841`；② gd 未命中 → 本地 OSM GPKG `resolve_poi_filters` local_first.py:230-249；③ 全部未命中 → Overpass `amenity=primary_school` osm.py:311 → Nominatim 关键词兜底 osm.py:319-357 | **缺陷 G-1（P1，已实证）**: `query_gd_poi` 的 fid 均匀采样只挂在"无 bbox/polygon"分支（local_poi.py:718），主路径（bbox 查询）仍是 pyogrio 索引头部截断 → 「成都小学」返回的点全部落在入库顺序最前的区县，密度图系统性偏斜；**缺陷 G-4**: Overpass 用非标准 tag `amenity=primary_school`；**G-7/G-8**: limit 截断语义与跨链路类别词不一致 |
| 5 | **空间分析 (密度/聚类)** | prompt「由简入深」→ 视觉热力 `heatmap_data` native `app/tools/spatial.py:150-238`（radius 契约 :153-158，<10 点守卫 :165-199）；定量网格 `h3_binning` `app/tools/advanced_spatial.py:267-314` → `app/lib/geo_analysis/aggregation.py:214-305`；KDE `app/lib/geo_analysis/density.py:145-309`；聚类 `app/lib/geo_analysis/statistics.py:519-619`；最近邻/Moran/Gi* statistics.py:178-468 | **缺陷 G-3**: 聚类 value_field 量纲混合默认失效；**G-5**: KDE 自动带宽城市尺度过平滑；**G-6**: Gi*/LISA 无多重比较校正；**G-7**: 截断样本上照常输出"聚集/随机"显著性叙述 |
| 6 | **制图方法选择** | dispatch 授权 `_author_display_result` `app/services/tool_dispatch_service.py:559-613`（type_hint=heatmap 驱动图层类型）→ `convert_analysis_to_mapspec_layer` `app/services/analysis_cartography_converter.py:251-436`（热力半径契约 :340-358，heatmap 守卫 :288-322）；专题分级 `create_thematic_map` `app/tools/cartography.py:112-164` → `CartographyService.classify` `app/services/cartography_service.py:95-118`（Fisher-Jenks DP :17-92） | 此环节契约完善（heatmap_contract / thematic_spec 单一投影），未发现新缺陷 |
| 7 | **地图生成** | MapSpec store/runtime：converter 输出 layer+paint → 前端 `frontend/lib/mapspec-compiler/` 渲染 MapLibre；热力 paint 由 `palettes.heatmap_paint` + `radius_px` 生成 | 无缺陷（radius_px/bandwidth_m 契约闭环，analysis_cartography_converter.py:340-358 已验证遵守） |
| 8 | **结果反馈** | `GeoAnalysisResult.to_llm_response` `app/lib/geo_processor/core.py:60-75`（summary+stats）；dispatch 载荷裁剪 `slim_tool_result` tool_dispatch_service.py:500-528 | **缺陷 G-7**: 上游截断的 `note/truncated/total_matched` 在 `try_local_osm_poi` 信封中被丢弃（local_first.py:536-548），LLM 无从得知样本被截断 |

**结论**: 分析与制图算法层（第 5/6/7 环的数值实现）经过 3 轮审计后质量很高；当前主要风险集中在**第 3/4 环（数据获取与坐标系）**——进入分析引擎的数据本身有偏（G-1）或坐标系混叠（G-2），后端统计与渲染无法察觉，输出看似专业实则误导。

---

## 2. 已有算法资产（简述，防重复建设）

- **投影基座**: `to_utm_gdf`（`app/lib/geo_processor/core.py:366-553`）自动 UTM/极地立体投影、跨反子午线重绕（#709）、多区诚实告警、gcj02/bd09 归一（#813）、身份缓存。度/米换算问题在此层基本封死。
- **缓冲/叠加/裁剪/融合**: `geo_processor/geometry.py`、`overlay.py` —— make_valid 前置、CRS 对齐、非米制 CRS 单位换算（#524/GIS-P3-8）、输出 `crs` 成员声明（GIS-599）。
- **空间统计**: Moran's I（置换检验 +1 修正 E-8）、Getis-Ord Gi* 稀疏向量化（#385, O(n)）、KNN 权重自排除（E-4）、常量场守卫（E-2/E-3）、`h3_lisa`（esda Moran_Local, seed 固定）。
- **密度**: `density.py` 各向同性米带宽 KDE（#384 `cho_cov` 覆盖）、输入点/格网双 OOM 上限、`kde_contours` 偶奇 containment 挖洞（#707/#762）；`heatmap_contract.py` radius_px/bandwidth_m 双语义唯一归一化边界。
- **分类**: Fisher-Jenks O(n²k) DP + 前缀和向量化（#441，等价性有逐字测试 `tests/test_jenks_441.py`），>1000 均匀降采样并披露（#618-19）；std_dev / head_tail 分類；`thematic_spec.py` legend→paint 单一投影。
- **网络**: 等时圈 V2（`services/network/service_area.py`，米制缓冲 #GIS-08、部分可达边截断 #618-20、单位归一 #706）；VRP 有向 2-opt O(1) 增量（vrp.py:199-258，**本次 600 组非对称矩阵 fuzz 验证与朴素重算逐位一致**）；OD 矩阵自写 Dijkstra 带副指标累积（#449）；选址分配 exact/启发式自动切换（GIS-11）。
- **遥感**: `band_math.py` nodata→NaN 语义（B-F09）、EVI DN→反射率（#382）、Horn 坡度/坡向罗盘角（#379）、纬度校正 cell_size_x、±inf 排除（#712）；变化检测公共足迹像元级差值（#445/#381）。
- **插值**: IDW 全程米制投影 + H3 资源守卫 + 重复点确定性聚合 + 反子午线 ring（#763）。
- **质量闸门**: recipe eligibility + HEATMAP_MIN_POINTS 确定性守卫（#690）、converter 端 heatmap_guard 双保险、cartography semantic_checks。

---

## 3. 发现的问题

### G-1 [P1] 高德 POI 库 bbox/polygon 查询仍按索引顺序头部截断，「成都小学分布」类任务返回空间严重偏斜的样本
- **问题描述**: `query_gd_poi` 的"fid 哈希均匀采样"修复只覆盖 `where is not None and parsed is None`（即无 bbox/polygon、纯属性过滤）的分支；带 bbox 的查询（本地优先链路 `try_local_osm_poi` → `_local_gd_poi` 永远传 bbox）直接走 `pyogrio.read_dataframe(bbox=..., max_features=limit)`，返回** fid 顺序前 limit 条**。gd_pois.gpkg 按 省→行政区→xlsx 行序 追加写入，因此市级查询超限时返回点几乎全部来自入库顺序最前的区县。代码注释自己记录了该故障模式（"实测 2000 条全是锦江区培训机构"），但修复没有覆盖最常见的 bbox 路径。
- **影响范围**: 所有"区域内某类 POI 分布"任务（成都小学/医院/餐厅…）的主数据链路：`query_osm_poi`(本地命中) / `query_local_poi`(带 bbox) / `query_local_osm(theme=pois)`。下游热力图/KDE/h3_binning/spatial_aggregate 在偏斜样本上产出"某区密度最高"的确定性错误结论，且工具层无从察觉。polygon 路径（read_cap = min(20000, max(limit*5,5000))）同样先取 bbox 候选的头部再过滤，偏差一致。同时 `query_local_poi` 工具文档承诺"超出时服务端按 fid 均匀采样返回…total_matched 字段给出真实命中数"（local_stats.py:117-120），在 bbox/polygon 路径上不成立（total_matched 恒缺失）；`try_local_osm_poi` 信封（local_first.py:536-548）还会把 gd 结果里仅存的 `truncated/note/total_matched` 字段丢弃，LLM 完全失去截断感知。
- **代码位置**: `app/services/local_poi.py:707-712`（read_cap=limit 的 bbox 路径）、`app/services/local_poi.py:718`（采样门条件 `if where is not None and parsed is None`）、`app/services/local_poi.py:732-743`（fid 哈希采样仅在门内）、`app/services/local_poi.py:764-768`（bbox 读取带 max_features）；文档失配 `app/tools/local_stats.py:117-120`；披露丢失 `app/services/local_first.py:457-468, 536-548`
- **原因分析**: 采样修复以"属性过滤 + 无空间过滤"为前提设计（COUNT 需要把 where 下推 SQLite）；bbox 过滤发生在 OGR 层，无法直接复用同一条 SQL，于是被整体跳过。pyogrio `max_features` 语义是"从文件头读 N 条"（官方文档 "Number of features to read from the file"），叠加 bbox 过滤后即"fid 序前 N 条命中"。
- **优化方案**: ① bbox/polygon 路径先用 SQLite 对 `mbrminx/mbrmaxx…`（GPKG 已有 R-tree）做 COUNT + `ORDER BY (fid*2654435761 % 2147483647) LIMIT read_cap` 取 fid 集，再用 `fids=` 读取（现成模式已在同函数 732-743/758-763 行实现，只需把 bbox 条件写进 SQL：`mbrintersects(BuildMBR(?,?,?,?), geom)`）；② 或读取后检测 `total_matched > read_cap` 时空间分层采样（按 adcode 分层配额）；③ `try_local_osm_poi` 信封透传 `truncated/total_matched/note`；④ 修正 `query_local_poi` 参数文档，注明各路径行为。
- **验证方式**:
  ```bash
  # 偏差实证（已在审查中执行，输出 10/10 全部 adcode=510104）：
  python3 - <<'EOF'
  import geopandas as gpd, pyogrio
  from shapely import points
  xs = [104.0+i*0.001 for i in range(15)] + [104.5+i*0.001 for i in range(15)]
  ys = [30.6]*15 + [30.7]*15
  gdf = gpd.GeoDataFrame({'name':[f'n{i}' for i in range(30)],
      'adcode':['510104']*15+['510181']*15}, geometry=points(xs,ys), crs='EPSG:4326')
  pyogrio.write_dataframe(gdf, '/tmp/fake_pois.gpkg', layer='pois')
  r = pyogrio.read_dataframe('/tmp/fake_pois.gpkg', bbox=(103.9,30.5,104.7,30.8),
      columns=['name','adcode'], max_features=10)
  print(len(r), r.adcode.unique())   # 10 ['510104'] ← 空间偏斜
  EOF
  # 回归：pytest tests/unit/test_local_stats_data.py -k gd_poi -q
  # 新增用例：bbox 命中 30 条、limit=10 时断言 adcode 覆盖 ≥2 个区县 且 total_matched==30
  ```

### G-2 [P2] 本地行政边界默认返回 GCJ-02 且不声明 `crs` 成员，与 WGS84 POI 层叠加存在 ~100-600m 系统偏移
- **问题描述**: `get_local_admin_boundary` 默认 `to_wgs84=False`，返回 GCJ-02 坐标的 FeatureCollection，但输出**没有**按 #599/#813 契约附加 `crs: "gcj02"` 成员（只有给人读的 `crs_note` 字符串）。下游 `gdf_from_features`/`to_utm_gdf` 读不到声明成员，按 EPSG:4326 处理。系统提示词强制"涉及特定区域必须优先使用 get_local_admin_boundary"（prompt.py:57）却未提醒传 `to_wgs84=true`。
- **影响范围**: ① `clip_layer(WGS84 POI, GCJ-02 边界)` / `spatial_aggregate`：边界带 ~300-600m 内的点被错误包含/排除（成都市界附近的小学归属错判）；② 边界层直接上图时与 WGS84 底图（MapLibre，前端无 GCJ 处理）可见偏移；③ h3/渔网与边界的相交分析同理。高德系 POI（gd_pois、amap 工具）均已转 WGS84，混合叠放必错。
- **代码位置**: `app/tools/local_admin.py:241-253`（工具签名 `to_wgs84: bool = False`）、`app/tools/local_admin.py:138-147`（`_to_feature_collection` 不写 `crs` 成员）、`app/tools/local_admin.py:226-239`（描述仅口头声明坐标系）；消费端 `app/lib/geo_processor/core.py:246-267`（只认 `crs` 成员，具备 gcj02 归一能力却收不到信号）；`app/services/chat/prompt.py:57`
- **原因分析**: #813 为"声明 gcj02 的输入"建立了完整的归一化管道，但本地行政边界这个最常用的中国数据出口没有接入该契约，坐标系语义停留在 docstring 里，对机器不可见。
- **优化方案**: ① `_to_feature_collection` 在 `to_wgs84=False` 时写入 `"crs": "gcj02"`（字符串简式，`normalize_chinese_crs` 已支持），下游全部自动归一；② 或直接把工具默认值改为 `to_wgs84=True`（系统内其余数据均为 WGS84，仅 amap 原生链路需要 GCJ-02）；③ prompt.py「精准分析协议」补一句坐标系要求。
- **验证方式**:
  ```bash
  pytest tests/unit/test_local_stats_data.py -q   # 现有边界相关用例
  # 新增断言：query_admin_boundary(level="city", name="成都市", to_wgs84=False) 的返回
  # 含 "crs"=="gcj02"；且 clip(POI_wgs84, 该边界) 与 clip(POI_wgs84, to_wgs84=True 边界)
  # 的结果差 ≤ 迭代逆变换精度 (~1m)
  ```

### G-3 [P2] spatial_cluster 的 value_field 维度与米制坐标直接拼接，默认 value_weight=1.0 下"值感知聚类"退化为纯空间聚类
- **问题描述**: `cluster_narrated` 在设置 `value_field` 时把标准化后的值维（σ=1）与 UTM 米坐标（城市尺度 σ≈8-20 km）列拼接后送 DBSCAN/K-Means，`value_weight` 默认 1.0 意味着值维以"1 米"的量级参与距离。实测城市尺度下值维对两点距离的贡献约为 eps=1000m 的 0.09%（4 个数量级小于空间维），聚类结果与不传 `value_field` 几乎逐点相同——但 summary/cluster_stats 不会告知调用方"值维实际未参与"。
- **影响范围**: LLM 按 spatial_stats.py:23-24 的参数说明（"value_field 参与聚类的数值字段名，将作为额外聚类维度"）调用后，会向用户叙述"按 XX 值聚类"的结论，实际只是空间聚类；DBSCAN 的 `eps`（米）语义在 3 维混合空间中也已失真。
- **代码位置**: `app/lib/geo_analysis/statistics.py:550-570`（`features = np.column_stack([coords, vals_scaled])`，vals_scaled 仅乘 value_weight）；工具面 `app/tools/spatial_stats.py:15, 23-24`；测试只覆盖 0 与 10 两个极端档（`tests/unit/lib/test_cluster_value_weight.py:20-35`），默认档无有效性断言。
- **原因分析**: 量纲不可比——标准化值无量纲、坐标是米，"等权"在物理上无意义。正确做法是把值维缩放到与坐标（或与 eps）可比的尺度。
- **优化方案**: 默认将值维缩放为 `value_weight × coords_std`（或 `× eps × 比例因子`），让 value_weight 变成"值维相对空间维的标准差比"这样有单位语义的参数；或至少在结果信封输出 `value_dim_effective_scale_m`，当值维贡献 < 空间维 1% 时在 summary 中明确"值维影响可忽略（建议增大 value_weight）"。
- **验证方式**:
  ```bash
  pytest tests/unit/lib/test_cluster_value_weight.py -q
  # 新增默认档用例：两组点空间位置一致、值域不同，默认参数下断言聚类标签不随值变
  # （当前实现必然通过——恰好证明默认档值维失效），修复后该用例应 FAIL 并驱动新语义
  ```

### G-4 [P2] query_osm_poi 的 Overpass 类别映射使用非标准 OSM 标签（amenity=primary_school 等），在线兜底路径召回系统性偏低；两份类别映射表已失同步
- **问题描述**: `category_map` 把"小学"映射为 `amenity=primary_school`、"中学/高中"映射为 `amenity=secondary_school`。OSM 社区标准是 `amenity=school`（含中小学）+ `school=*`/`isced:level=*` 区分学段（OSM Wiki Tag:amenity=school）；`primary_school`/`secondary_school` 不是文档化标签，全球用量相比 `amenity=school` 可忽略。中国本地库未命中（如境外城市、gd 库无该省数据）而走 Overpass 时，"小学"查询几乎必然 0 命中，再降级到 Nominatim 关键词搜索（返回命名地点匹配而非普查，量级最多 limit=50 且其 `category_names` 兜底词表 osm.py:325-338 根本没有 primary_school/secondary_school/supermarket/mall/station 词条，兜底时直接拿原始英文值当关键词搜索）。另外 `local_first._CATEGORY_TO_TAG`（注释声称"与 osm.py 的中文类别表对齐"，local_first.py:19）缺少 #694 新增的 小学/中学/高中/超市/菜市场/商场/地铁站/火车站 词条——同一中文词两条链路解析到不同 OSM tag。
- **影响范围**: 中国境外或本地库缺失场景的 POI 枚举完整性；两表失同步使"本地优先→出网"两阶段对同一请求使用不同过滤语义，count 前后不可比。
- **代码位置**: `app/tools/osm.py:272-275`（小学→primary_school / 中学·高中→secondary_school）、`app/tools/osm.py:299-309`（tag_filter 一律 `amenity=`）、`app/tools/osm.py:325-338`（兜底词表缺项）、`app/services/local_first.py:20-90` vs `app/tools/osm.py:247-279`（两表失同步）
- **原因分析**: #694 修复"中文直通 0 命中"时选用了字面直译的 tag 值，未对齐 OSM 实际数据分布；两处映射表没有单一事实来源。
- **优化方案**: ① "小学" → Overpass 联合查询 `(amenity=school and school~primary|elementary) 或 amenity=kindergarten 边界外` 简化为 `["amenity"="school"]["school"~"primary|elementary"]` + 无 school 子标签的 amenity=school 兜底（或直接 amenity=school 并在 fallback_note 说明含中学）；② 把 category_map 提取为共享模块（单一事实来源），local_first 与 osm.py 共同 import；③ 兜底词表补齐新增类别。
- **验证方式**:
  ```bash
  # 对比召回（需出网）：
  curl -s https://overpass-api.de/api/interpreter --data-urlencode 'data=[out:json][timeout:30];(node["amenity"="primary_school"](30.65,104.04,30.73,104.15););out count;'   # ≈ 个位数/0
  curl -s https://overpass-api.de/api/interpreter --data-urlencode 'data=[out:json][timeout:30];(node["amenity"="school"](30.65,104.04,30.73,104.15););out count;'      # 数十倍
  pytest tests/unit/test_694_sweep.py -q   # 回归现有映射
  ```

### G-5 [P2] KDE 自动带宽实为 Scott 规则且无城市尺度钳制，"小学分布"类强聚集数据的默认密度面被数公里级带宽抹平；文档误标为 Silverman 法则
- **问题描述**: `_fit_kde` 用 `bw_method="scott"` 拟合后取 `bw = kde.factor × mean(数据各轴 std)`。Scott 因子 = n^(-1/(d+4))，对城市 POI 数据（n=50~500、坐标 σ≈8km）自动带宽落在 **2.8~4.2 km**——比小学聚簇特征尺度（数百米）大一个数量级，kde_surface/kde_contours 的等值面会合并成城市级的 1-2 个团块，"哪片学校密集"的信号被系统性平滑掉。工具文档（spatial_stats.py:84"0表示自动计算（Silverman法则）"）与实现（Scott）不符；自动模式没有带宽上限（如 ≤ bbox 对角线 5%）或基于最近邻距离的自适应。
- **影响范围**: 所有用 `bandwidth=0` 默认值的 `kde_surface`/`kde_contours` 调用；LLM 若按文档信任"自动法则"，会把抹平后的单核分布叙述为"全市均匀集聚"。对照组：`hotspot_narrated` 的自动距离带已改用 k 近邻尺度（statistics.py:299-313, E-7），KDE 未同步。
- **代码位置**: `app/lib/geo_analysis/density.py:106-126`（`_fit_kde`：scott + factor×mean std，无上限）；文档失配 `app/tools/spatial_stats.py:84, 107`
- **原因分析**: Scott/Silverman 是对**单峰近似高斯**数据的最优规则，对多峰强聚集点集会严重过平滑；文档与实现漂移属契约失真。
- **优化方案**: ① 文档改为 Scott；② 自动带宽取 `min(scott_bw, c × mean_kNN_dist)`（如 c=6，与 Gi* 的 E-7 尺度哲学对齐）或 `min(scott_bw, 0.1×bbox对角线)`，并在结果信封 `bandwidth_m` 旁输出 `bandwidth_mode: auto-clamped`；③ summary 提示"自动带宽按全局离散度估计，强聚集数据建议显式给 bandwidth"。
- **验证方式**:
  ```bash
  pytest tests/unit/lib/test_density.py tests/unit/lib/test_density_isotropy_cap.py -q
  python3 -c "
  # 当前自动带宽量级演示：
  n=500; import math; print('scott factor=%.3f' % n**(-1/6.), '-> bw≈%.1f km @ std=8km' % (n**(-1/6.)*8))"
  # 新增用例：双簇间距 3km、簇内 σ=200m 的点集，自动带宽下两个峰必须仍可分辨（峰值间距 > 0.5×峰宽）
  ```

### G-6 [P3] Gi* 热点与 LISA 的显著性判定无多重比较校正，n 个单元各按 α=0.05 检验时假热点期望数随格网数线性放大
- **问题描述**: `hotspot_narrated` 对每个要素独立算 p 值（正态近似）并按 0.05/0.01/0.1 打置信度标签；`h3_lisa` 对每个 H3 格做 Moran_Local p_sim<0.05 判定。两者都没有 FDR/FWER 控制：1000 个格网的 LISA 在完全随机数据下期望产出 ~50 个"显著 HH/LL"格子并直接上图。Gi* 还使用正态近似 p 值（小样本或偏态分布下偏乐观），而非条件随机化。Esri 桌面同样默认不校正，但本系统把"99% 置信热点"直接渲染成地图并写进 narrative，误导风险更高。
- **影响范围**: `hotspot_analysis`、`h3_lisa` 工具输出及其 choropleth 图层；prompt.py:65 还要求 LLM 把"99% 置信度聚集"作为核心洞察转述。
- **代码位置**: `app/lib/geo_analysis/statistics.py:347`（`p_vals = 2*(1-norm.cdf(...))`）、:358-371（置信度分级，无校正）；`app/lib/geo_analysis/statistics.py:665-676`（LISA p_sim 门限）
- **原因分析**: 逐点检验是局部统计的标准输出，但多重比较校正（BH-FDR 或至少报告期望假阳性数）是从"统计量"到"可断言热点"之间缺失的一步。
- **优化方案**: 在 cluster_stats/summary 中附加 BH-FDR 校正后的 q 值（或在随机零期望下报告 `expected_false_positives = 0.05 × n`，当 hot_count 接近该值时降级叙述）；Gi* 可选条件随机化（999 次）计算 pseudo-p。
- **验证方式**:
  ```bash
  pytest tests/unit/lib/test_hotspot_gistar.py tests/unit/lib/test_statistics_hardening.py -q
  # 新增用例：随机独立值 + 规则格网输入，断言 summary 披露 expected_false_positives，
  # 且 hot_count 显著低于 0.05×n 时才输出 "hot spots detected"
  ```

### G-7 [P3] POI 枚举语义与 limit 截断：分布统计结论建立在截断样本上而系统不感知
- **问题描述**: 「成都小学分布」是**全量普查**语义，但 `query_osm_poi` 默认 `limit=50`、schema 上限 500（Overpass `out body geom 500` 返回的是任意前 500 条而非空间均匀样本），`query_local_poi` 上限 2000。当命中数超过 limit 时（成都小学实际 600+），下游 `nearest_neighbor`（R 比率）、`moran_i`、`hotspot_analysis`、DBSCAN 在截断样本上照常输出"聚集/随机/显著"叙述，工具结果里没有任何"样本占全量比例"字段（G-1 修复后 gd 链路有 total_matched，Overpass 链路 `count==limit` 也不置 truncated 标志）。
- **影响范围**: 所有大区域+高频类别的分布/密度任务；R 比率对截断方式极其敏感（头部截断把 R 压向聚集，见 G-1 的区县偏斜）。
- **代码位置**: `app/tools/osm.py:200-201`（limit 1-500）、:359-360（截断后无 truncated 标记）、:364-373（结果信封无 total/截断字段）；`app/lib/geo_analysis/statistics.py:446-452`（R 比率模式叙述无样本完整性前提）
- **原因分析**: 检索工具（返回前 N）与统计工具（假设代表性样本）之间的契约缺一层"样本完整性"元数据。
- **优化方案**: ① `query_osm_poi` 在 `count==limit` 时置 `truncated: true` 并建议提高 limit 或分 bbox 拉取；② 检索结果 ref 的 descriptor 里记录 `total_matched/limit`，`nearest_neighbor`/`moran_i`/`hotspot` 在检测到 ref 元数据截断时在 summary 首句声明"基于截断样本（x/y）"；③ prompt 的"分析点到为止"段落补充"普查类任务先确认未截断"。
- **验证方式**:
  ```bash
  pytest tests/unit/test_local_first_routing.py -q
  # 新增：mock Overpass 返回 500 条（limit=500），断言结果含 truncated=true
  ```

### G-8 [P3] 同一中文类别词在高德链与 OSM 链解析为不同语义（"中小学"→仅小学 vs amenity=school 全学段），跨源计数不可比
- **问题描述**: `query_osm_poi(category="中小学")` 本地命中时，`_gd_hints` 按子串顺序先命中 `("小学",)` → 只查高德 subtype=小学（漏掉中学）；本地未命中出网时 `category_map["中小学"]="school"` → `amenity=school`（含初高中）。同一请求在"本地有数/无数"两种情况下返回的总体集合不同，agent 汇报的总数不可比，也无法察觉。类似地 `_GD_KEYWORD_HINTS` 中 `("中学","初中","高中","完中")` 与 OSM 侧 `中学/高中→secondary_school`（本就是非标准 tag，见 G-4）语义再度分叉。
- **影响范围**: 含复合学段词（中小学、初高中）或多义词的 POI 统计；本地→出网 fallback 前后对比。
- **代码位置**: `app/services/local_first.py:119-121`（_GD_KEYWORD_HINTS 顺序，"小学"先于"中学"命中"中小学"）、`app/services/local_first.py:99`（_SYNONYM_TAGS：中小学→amenity=school）、`app/tools/osm.py:249`（中小学→school）
- **原因分析**: 两套关键词→分类的映射各自维护、匹配策略不同（子串首中 vs 精确表 vs 同义词组），没有共享的语义定义。
- **优化方案**: 建立 `{类别词: {gd: (category, subtype…), osm: [tags…]}}` 单一映射表供三处消费；"中小学"在 gd 侧映射为 subtype 匹配 `小学 OR 中学`（query_gd_poi 需支持 subtype 列表/ OR 查询）。
- **验证方式**:
  ```bash
  pytest tests/unit/test_local_first_routing.py tests/unit/test_694_sweep.py -q
  # 新增：同一 bbox 下 try_local_osm_poi("成都","中小学") 与出网路径的类别语义断言一致（都含初高中或都不含）
  ```

### G-9 [P3] h3_binning 传 stat_method='sum'/'mean' 而未给 stat_field 时静默降级为 count，结果字段名与方法名不符
- **问题描述**: `h3_binning`（lib 层）在 `stat_method in ('sum','mean')` 但 `stat_field` 缺失或不在列中时，直接落回 count 分支并把 `stat_method` 改写为 'count'，无 warning、summary 也不提及；输出属性列名仍为 `count`。LLM 若按工具说明（advanced_spatial.py:280 "统计方法，如 'count'（默认）, 'sum', 'mean'"）只传 stat_method，会以为拿到的是均值专题图，实际是计数——随后 `build_graduated_spec`（advanced_spatial.py:292-305）按 `stat_method in ("sum","mean")` 判断字段名又会取到错误的列（取 stat_field_name=stat_method 列，但 FC 里只有 count 列 → legend_spec 为 None 静默无图例）。
- **影响范围**: `h3_binning` 工具的 sum/mean 路径；下游 graduated 图例缺失。
- **代码位置**: `app/lib/geo_analysis/aggregation.py:271-279`（静默降级 + stat_method 改写）、`app/tools/advanced_spatial.py:292-295`（字段名推断与 lib 层降级行为组合后失配）
- **原因分析**: lib 层容错降级与 tool 层字段名推断各自假设对方语义。
- **优化方案**: lib 层降级时在返回 dict 里加 `"stat_method_effective": "count"` 与 warning 文案；tool 层优先读该字段推断列名，检测到降级时在 payload 附加 correction_hint 提示补传 stat_field。
- **验证方式**:
  ```bash
  pytest tests/unit/lib/test_geo_analysis.py -q -k h3
  # 新增：h3_binning(stat_method="mean")（无 stat_field）断言返回含 stat_method_effective=="count"
  # 且 tool payload 带 correction_hint；修复后 legend_spec 不再静默缺失
  ```

---

### 附：审查中数值验证记录
1. **VRP 2-opt 增量式**（vrp.py:199-258）：提取实现与朴素逐候选重算对比，300 组随机非对称成本矩阵 × 开环/闭环 = 600 案例逐位一致（含接受序列），未发现正确性缺陷——对应检查清单第 7 项。
2. **pyogrio bbox+max_features 头部截断**：构造 30 点双区 GPKG 实证返回 10/10 均来自 fid 最小前缀区县（G-1 依据）。
3. **聚类值维量纲**：城市尺度（坐标 σ=8696m）下标准化值维贡献 ≈0.86 米 ≈ eps=1000m 的 0.086%（G-3 依据）。
4. **Jenks DP 回溯、Horn 坡度/坡向推导、Gi* 公式、等时圈部分边截断、fishnet 六边形镶嵌（30°顶点 + dx=cell_size）**均手工推导复核，未发现错误。
