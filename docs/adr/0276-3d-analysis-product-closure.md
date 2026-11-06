# ADR 0276 — 三维分析产品闭环（3D Analysis Product Closure）

- 状态：Accepted
- 日期：2026-10-28
- 方向：G3D-V2-C（三维科学分析 → AI GIS Harness → 智能制图产品闭环）
- 关联：ADR-0274（三维量测契约）、ADR-0275（三维制图产品）、ADR-0199（场景协议）、
  ADR-0082（artifact 登记即铸）、ADR-0225（执行信封）、G02 MethodUseContract、G05 任务验收

## 背景

#1773/#1829/#1830 之后，三维能力以"零件"形态存在，但**产品链有六处断链**
（C0 审计，file:line 见 planning/g3dv2-c-analysis-harness-map/BASELINE.md）：

1. `viewshed_analysis` 结果 stats-only：内核 bool 栅格在工具层被丢弃，无
   可视几何——"视域面"在制图侧无生产供给；
2. `run_3d_analysis` 仅编排 viewshed：#1773 四量测与 #1829 编排互相没接
   （decline 码自认 `SCENE_CAP_ALGORITHM_PENDING_G3D03` 欠账）；
3. `evaluate_staleness` 零生产调用：失效裁决纯函数写好但没有执行方——
   拖观察点判旧/camera-only 不判旧未闭环；
4. `elevation_profile ref:profile-*` 双重断链：无生产写入方 + 端点白名单
   硬编码 `ref:chart-` 直接拒绝该族；组件工厂注册表缺该类型（upsert 恒
   not found）；
5. 无土方体积算法（#1773 明确不做，本轮以充分 DEM 数据开放）；无建筑
   高度数据 → 净空类分析 fail-closed 不开放；
6. 编排层嵌套 dispatch 的产物不进外层缝——即使工具产出 geojson 也
   不铸 ref、不挂层。

## 决策

### D1 视域几何：算法计算与几何可视化产物分离（`viewshed_geometry.py`）

- `terrain.viewshed` 数值内核**零改动**（`include_geometry=True/False` 两
  模式 stats 逐位一致，测试钉住）；几何提取是独立纯算子
  `raster_mask_polygons` / `visibility_geometry`：cell-edge 整像元归并
  （4-连通、含洞、GeoJSON RFC 7946 环取向归一）；
- **像元原子记账**（CRS 无关）：多边形 shoelace 面积（CRS 单位²）与
  |det(affine)| 的整数倍关系是解析恒等式，不符即 `DegenerateData` 立即
  失败——带病上图不如失败；**米制面积 = 像元数 × 米制像元面积**（地理
  CRS 的度² shoelace 永不冒充 m²）；
- 输出有界：多边形数/顶点数双预算，超限按面积阈值二进位阶梯淘汰最小者，
  淘汰量 `unattributed_*` 披露（stats 与几何可对账）。

### D2 土方体积：中点求积 + 解析 oracle（`volumes.py`）

- `cut_fill_volume`：像元中心 prism `(z_center − base) × cell_area`，正部
  cut/负部 fill；**像元中心是唯一观测**（不发明角点高程，Jenness 口径
  同源）；基准面 constant（绝对高程）| mean（区域有效像元均值，
  net=0 解析恒等）；
- 数值诚实：中点求积对 ≤1 次表面**精确**（平面/斜面/台阶闭式 oracle
  钉住，<1e-6 m³）；曲面随分辨率收敛、不宣称解析误差界；边界半像元
  误差以 `boundary_cells` 披露；nodata 超阈类型化失败；
- 垂直治理复用 ADR-0274：`resolve_vertical_reference` 单一真相，跨 datum
  混算类型化拒绝。

### D3 编排产物提升缝（run_3d_analysis 薄包装纪律）

- 六类分析表驱动接入（viewshed/line_of_sight/terrain_profile/
  surface_distance/surface_area/cut_fill_volume），#1829 顶层参数保持兼容，
  `options` JSON 透传（目标工具参数契约为权威——编排层不做第二套校验）；
- **产物提升**：嵌套 dispatch 的 `geojson`/`profile_chart` 提升到编排层
  结果——外层 ToolDispatchService 缝按既有认可键纪律铸 `ref:geojson-*`/
  `ref:profile-*` 并挂层/挂组件。编排不是第二执行真相，产物走与直调
  完全相同的上链通道。

### D4 剖面 artifact 生产链（`profile_chart` 认可键）

沿 #517/uncertainty 先例：工具产 `{chart: ChartData}` 种子（≤512 点抽稀
披露）→ dispatch 缝 `prefix="profile"` 铸 `ref:profile-*` 并剥内联体 →
`_author_display_result` 层挂载后 `patch_component(upsert=True)` 挂
`elevation_profile` 组件（profileRef/unit/verticalDatum；成功刷新权威
mapspec/指纹，失败 additive 不推翻层挂载）→ 端点白名单扩 `ref:profile-`
（同一 `{chart}` 协议）。配套：`mutate_component` options 合并补
points↔profileRef 互斥（镜像 chart↔chartRef）；`_FACTORY_BY_TYPE` 补
elevation_profile/scene_disclosure 工厂。

### D5 失效裁决生产接线（analysis_binding_store）

- 绑定持久化在会话 map_state `_scene3d_analysis_bindings`（有界 FIFO ≤8、
  幂等覆盖、损坏条目剔除）——零第二存储；
- 生产消费方：`run_3d_analysis`（执行前对账旧绑定→`prior_stale_analyses`
  ACK、成功后登记）；`apply_scene_representation`（场景突变后
  `stale_analyses` ACK）；**相机通道显式不消费**（视域与镜头无关）；
- 语义收窄（G3D04 纯函数微调）：选择漂移检查只适用**观察者语义目标**
  （viewshed/line_of_sight 或显式 observer_feature_id）——线路/区域语义
  分析（profile/surface/volume）不是选择点的函数；登记时戳当前选择
  上下文（分析响应当前选择 → 同选择不判旧，换选才失效）；
- dem 检查复用 `verify_vertical_evidence` 单一真相；无 spec 上下文的只读
  诊断不把"未知"当"无源"（跳过该项，与纯函数契约一致）。

### D6 分析产品组合模板（`composition_packs/analysis_products.py`）

三模板：`mountain_los_obstruction`（视域+LOS+地形；scene_disclosure
required）、`route_profile_joint`（**纯 2D**：线路+剖面面板联合制图，
elevation_profile required，pdf/svg 矢量导出合法；主语义模型收窄为
graduated_line——不以 terrain×pdf 键抢既有报告模板）、
`earthwork_cut_fill`（挖填分区+统计+**methodology_note required**——无
方法披露的体积是伪精确）。conformance 反证测试钉住缺必填槽必 error。

### D7 任务级验收（F27 族 + scene3d_analysis 证据面）

- 14 场景：真数据正例（确定性合成 DEM，simulated 披露）/负例（无 DEM 源、
  垂直基准混算、nodata 观察点、坏参数）/状态迁移（换点判旧、camera-only
  fresh、中断恢复、print 2D 退化）/完整多轮旅程（剖面→换点→通视→改
  角度→导出）；
- 验收基建：`StepKind.MAPSPEC_SOURCE`（真实 UpsertSourceIntent 事务——
  DEM 供给注册面归 A 方向，scripted 走同一 mapspec 引擎）；
  `scene3d_analysis` TaskEvidence 面（绑定+裁决只读投影，期望可断言）。

## 不做（honest non-goals）

- 建筑净空/城市白模高度分析：无可靠 3D building geometry 源——
  fail-closed 不开放（capability 探测 typed unavailable）；
- skyline：算法未实现，decline 保持 honest pending；
- 通用 classify_crs（#1805）与 terrain 水文 DoS（#1797）抢修：仅做兼容
  验证与非重叠最小修复（`_parsed` 预初始化——E2E 显形的登记路径恢复）；
- DEM 供给管线（摄入/瓦片/限流）：归 A 方向 #1835。

## 后果

- 视域/剖面/土方分析结果形成"图层 + 剖面面板 + Agent 可观察 ACK +
  合规导出"的完整产品；失效判定精确到语义输入（拖点/换源判旧，
  camera-only 永不误判）；
- 新增契约面（profile_chart 认可键、绑定状态键、F27 证据面）均为
  additive；既有四量测/viewshed stats 数值零回归（oracle+工具双钉）。
