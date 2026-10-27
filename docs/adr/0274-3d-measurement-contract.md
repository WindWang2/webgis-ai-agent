# ADR-0274: 三维量测契约 —— 垂直基准治理与确定性地表测量

- 状态：Accepted（G3D03）
- 日期：2026-10-26
- 关联：ADR-0224（Pi Tool ABI V2）、ADR-0099（domain packs）、science-v4 契约 ratchet、#1760（科学守门）

## 背景

仓库已有完整的 terrain 科学族（slope/aspect/viewshed/D8/contours…），但**没有任何三维量测能力**：
沿线的地形剖面、两点通视（LOS）与遮挡点、坡面（地表）距离 vs 水平距离 vs 三维斜距、真实地表面积。
同时全库不存在**垂直基准（vertical datum）概念**：`GISDatasetDescriptor` 无字段，GeoTIFF 本身不携带
z 单位 —— 任何把「DEM 高程」与「外部高度来源」（建筑/仪器/目视）混算的路径都是静默科学错误。

## 决策

1. **新算法族 `terrain.measurement`**（`app/lib/geo_analysis/measurements.py`，纯 NumPy、确定性、
   可协作取消），四个算法 + 工具，与 viewshed 同一登记/护栏/证据/契约路径：
   - `terrain.profile` → `terrain_profile`：沿折线 bilinear 采样剖面（水平距离、z、逐段坡度、
     累计爬升/下降、极值），输出 3D LineString FeatureCollection。
   - `terrain.line_of_sight` → `line_of_sight_analysis`：两点通视判定 + 首遮挡点 + 视线采样剖面；
     地球曲率+大气折射为**可选**（`curvature="none"|"earth"|"earth_refracted"`，k=0.1307 披露）；
     几何视线对称：角色互换（连同各自高度）判定不变；高度本身改变视线与可见性。
   - `terrain.surface_distance` → `surface_distance_analysis`：同一折线上的水平（网格米制）/
     三维直线斜距 / 地表（沿面采样）距离、高差、累计爬升，附半步加密收敛指示（Δ_rel）。
   - `terrain.surface_area` → `surface_area_analysis`：多边形窗口内逐像元两三角面 3D 面积和
     vs 投影面积（粗糙比），nodata 像元剔除 + 超阈值类型化失败。
2. **垂直基准契约（本 ADR 核心）**：`VerticalReference` 闭合词表
   （`ellipsoid_height|geoid_height_egm96|geoid_height_egm2008|orthometric_local|unknown`）
   + `z_units`（`m|ft`）。规则：
   - DEM 侧：调用方声明（`vertical_datum`/`z_units` 参数），缺省 `unknown`/`m`（GeoTIFF 无 z 元数据，
     缺省即披露，不做探测虚构）；`ft` 以精确 0.3048 折算米并记 transformation。
   - 覆盖值侧（observer/target height、z override）：同样必须落在**同一 datum**；datum 不同 →
     类型化科学错误 `VERTICAL_REFERENCE_MISMATCH`（scientific_errors 通道），绝不静默换算。
   - `z_units` 词表外声明 → `VERTICAL_UNIT_MISMATCH`（闭合词表 m|ft 在此校验；z 覆盖值
     按定义取 DEM z_units —— 不存在运行期「单位不一致」比对路径）。
   - `datum="unknown"` 允许单源运算（一个 DEM + 米制覆盖值），但在 quality_flags 披露
     `vertical_datum_unknown` —— 未知不等于可以混算两个 unknown 之外的来源。
3. **采样与误差语义**：采样步长 `sampling_step_m`（缺省 ~1 像元，与 viewshed 同政策）进
   parameters_applied 与输出 meta；地表距离/面积为**离散近似**，提供半步加密收敛指示
   `convergence.delta_rel`（|D(step) − D(step/2)| / D）作为经验误差量级，不做无依据的
   解析误差界宣称。水平距离恒用网格米制空间（cos(lat) 政策，披露 transformation），
   地理 CRS 额外报告独立椭球测地距离 `geodesic_horizontal_m`（参考量，不参与内部一致性）。
4. **nodata/ErrorPolicy**：`nodata_policy ∈ {"error","skip"}` —— `error` 遇任何无效采样点类型化
   `NoValidObservations`；`skip` 剔除并按 `nodata_fraction` 披露，超 `max_invalid_fraction`
   （缺省 0.5）仍失败。surface_area 同语义按 quad（4 像元全有效才计入；
   mask 边界按像元中心判定，planar_area 是 quad 对多边形的近似）。
5. **不做**（诚实边界）：垂直基准转换（EGM96↔椭球）—— 无 geoid 模型依赖，拒绝混算而非假装换算；
   ft 为国际英尺（0.3048 m 精确；US survey foot 须先换算）；
   建筑高度/体积、机场净空 —— 无实证数据源与专业规则参数，不虚构；曲率/折射只进 LOS
   （viewshed 保持无曲率披露不动，避免既有输出漂移）。

## 后果

- 输入/输出契约：`SpatialReference（raster CRS）+ VerticalReference + SurfaceEvidence（DEM 窗口）
  + Geometry（折线/点）+ SamplingResolution + ErrorPolicy` →
  `统计标量 + GeoJSON(3D) + MethodUseContract（科学证据块）+ QuantityUnit + ErrorBounds（收敛指示）
  + Provenance（algorithm_version/参数）+ QualityFlags`。
- 复用面：`_read_terrain_window`（护栏）、`apply_contract`、`build_evidence`、`ResourceEnvelope`
  /`cancellation_profile`（chunk_boundary）、dispatch FeatureCollection → ref → MapSpec 通道。
- 既有 viewshed/slope/… 输出零漂移（不触碰其函数与 descriptor 数值语义）。
