# G3D03 DECISIONS

- **D1 复用而非重写 viewshed**：既有 `terrain.viewshed`（扇区扫描 + observer/target
  height + chunk 取消）不动；LOS 是新的两点算法（遮挡点/曲率/折射/nodata 政策是
  viewshed 语义外的正交能力）。
- **D2 垂直基准治理走「拒绝混算」**：不做 geoid 换算（无模型依赖、不虚构精度）；
  词表 `ellipsoid_height|geoid_height_egm96|geoid_height_egm2008|orthometric_local|unknown`；
  覆盖值 datum ≠ DEM datum → `VERTICAL_REFERENCE_MISMATCH`；`unknown` 允许单源 +
  旗标披露。offset（离地高度）恒米、与 datum 无关。
- **D3 surface_area 用对偶网格 facet（相邻中心）而非角点均值**：角点均值网络在边界
  角点只剩 1–2 邻居、平面不再解析精确（实测 172.54 vs 176.36）；对偶网格只消费
  真实观测、平面全域精确，代价是最外圈中心不成 facet（meta 披露）。
- **D4 LOS 曲率 = bulge 加到地形侧（球面弧在弦之上）**，R=6371008.8 m，
  折射 R_eff=R/(1−k)、k=0.1307；以独立密集解析网格为 oracle（曾抓出实现符号
  反转 bug：首版把 bulge 从地形减去导致 7 km 平地误判可见）。
- **D5 水平/地表距离同网格米制空间（cos(lat) 政策）**：不变量分两级 ——
  surface ≥ covered_horizontal（有效采样对水平长）无条件成立；surface ≥
  horizontal 仅在无跨洞剔除时成立（评审 P2-1 后修正，skip 政策输出
  covered_horizontal_m 供同口径比较）；椭球测地距离仅作为地理 CRS 的独立
  参考量输出（pyproj Geod，`get_geod()` 构造 —— `Geod(crs=...)` 构造签名
  不成立）。
- **D6 误差语义 = 半步加密收敛指示**（|D(step)−D(step/2)|/D，预算内才跑），
  不宣称解析误差界；bilinear 在凸函数上略高估（抛物线测试注释披露）。
- **D7 契约对齐**：vertices/polygon 是数据输入（同 viewshed 的 raster_path 先例）
  不进参数契约，工具层类型化校验（点数上限 256/1024、JSON 文本或内联数组）；
  契约管 sampling_step/nodata_policy/vertical_datum/z_units/curvature 等。
- **D8 输出载体**：`result["geojson"]` FC（3D LineString/Point/Polygon）走 dispatch
  既有 ref 化 → `analysis_cartography_converter` → MapSpecLayer（converter 测试以
  `_author_display_result` 真实形状驱动）；不建第二渲染状态，不改 frontend。
- **D9 nodata/ErrorPolicy**：`error`（默认，类型化失败）/`skip`（剔除 +
  nodata_fraction 披露 + 超阈失败）；LOS 端点必须在有效覆盖内（锚点无定义）。
- **D10 注册表面**：4 AlgorithmDescriptor（resource_envelope + chunk_boundary +
  tolerance，ratchet 全过）、terrain_measurement capability（output
  `line_feature_set|stats_table` —— `stats` 非法词，parity 门抓出后修正）、
  4 参数契约、method refs fisher1993/jenness2004；g02 O12 计数钉扎与
  execution catalog/benchmark/quality 生成物同 PR regen。

- **D11（评审轮修正）**：抛物线 oracle 闭式 asinh 系数纠错（真值 64.9916）；
  采样近似三算法补 approximate=True；对称性措辞改「角色互换连同高度判定不变」；
  有效采样 <2 类型化失败（防单坐标 LineString）；surface_distance 单次采样
  （include_samples，meta.sample_points 不入 payload）；四工具独立
  _register_measurement_tools 注册；词表 parity 钉扎测试；账本 --update
  固定为 format/regen 之后的最后一步。
