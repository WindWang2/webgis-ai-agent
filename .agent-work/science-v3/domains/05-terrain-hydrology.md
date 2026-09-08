# 05 Terrain & Hydrology 审计

- 对象：worktree `/home/kevin/projects/webgis/webgis-ai-agent-science-v3`（分支 `feat/spatial-science-geoai-platform-v3`，HEAD 16d1c70）
- 日期：2026-09-07 · 审计者：地形分析/水文学专家（只读）
- 核心文件：`app/lib/gis/algorithms/terrain.py`（26 descriptor + 15 契约）、`app/lib/geo_analysis/terrain.py`（2431 行实现层）、`app/tools/terrain_analysis.py`（1561 行工具层，15 工具）、`app/services/rs/band_math.py`（slope/aspect/hillshade 真相源）
- 验证动作：64/64 单测通过；216/216 terrain oracle 回放通过；registry `validate()` 0 issue（含 conformance 节点级 AST 存在性校验）；自行执行 8 项数值探针（epsilon 路由汇点分布、D∞/D8 Σacc parity、Strahler Y 树、nodata 洞、TWI floor、viewshed 全域扫描、rasterio 仿射中心约定）

---

## 1. Census 表

26/26 descriptor 均 `runtime_status=native`、`scientific_status=VALIDATED`、`crs_class=RASTER_GRID`、`deterministic=True`、`random_seed_policy=deterministic`。registry 载入与工具存在性、parity（工具签名↔契约参数名）全部通过。

| descriptor | 实现（lib 函数） | 工具 | 方法声明/实现核对 | 数值口径要点 | conformance |
|---|---|---|---|---|---|
| terrain.slope | band_math.compute_slope（Horn 3×3） | compute_terrain | Horn 1981 ✅ 声明=实现 | edge 复制延拓；cell_size_x 承接 cos(lat)（stac 在线路径有测试） | ✅ compass 2 例 |
| terrain.aspect | band_math.compute_aspect | compute_terrain | Horn 1981 ✅ | 下坡罗盘角（#379 修复语义）；精确平地→NaN | ✅ |
| terrain.hillshade | band_math.compute_hillshade | compute_terrain | Horn 1981 ✅ | 罗盘光照闭式模型；0-255 clip | ✅ 闭式对照 |
| terrain.tpi | topographic_position_index | terrain_derivatives | weiss2001 ✅ | 窗口含中心；积分图 box-sum；边界收缩 | ✅ 线性坡≡0 |
| terrain.tri | terrain_ruggedness_index | terrain_derivatives | Riley 1999 原式（引用 wilson2007 为语境）✅ 已披露 | sqrt Σ(z−z_nb)²，8 邻域 | ✅ 手算 |
| terrain.roughness | roughness | terrain_derivatives | **std 口径 ≠ Wilson max−min，已如实披露** ✅ | 积分图方差（大均值/小离散时精度损失披露） | ✅ |
| terrain.curvature | surface_curvature | terrain_derivatives | **Zevenbergen-Thorne 1987 ✅ 声明=实现**（Horn 家族仅用于 slope/hillshade/aspect，三者均已声明 Horn） | profile/plan 符号约定、z=x² → +2/0 fixture | ✅ 精确 |
| terrain.viewshed | viewshed | viewshed_analysis | **method_references=[]（缺引用）**；扇区视线角扫描（R3 型向量化），判据披露 | 扇区角离散 ≈1 像元弧长；无曲率/折射；近似±2% 披露但 `approximate=False` | ✅ 圆盘/锥遮挡 |
| terrain.flow | d8_flow + flow_accumulation | flow_analysis | tarboton1997（**D8 惯例应引 O'Callaghan & Mark 1984，标签不精确**） | ESRI 幂编码；米制最陡下降；并列取最低索引；acc 不含自身；flat_routing v2 additive（epsilon = Barnes 填洼后路由） | ✅ 碗形 24 |
| terrain.watershed | upstream_watershed | watershed_delineation | tarboton1997（同上） | 逆 D8 BFS；无河道 snap（披露） | ✅ 全 25 像元 |
| terrain.contours | extract_contours | extract_contours | **无引用**（matplotlib marching squares） | nodata 断线；世界坐标映射（index=像元中心，正确） | ✅ |
| terrain.sink_fill | fill_depressions | depression_fill | barnes2014 ✅ **真实 Priority-Flood**（heapq + counter 裁决；种子=边界+nodata 邻接；epsilon 变体逐像元抬升）— 非摆设 | meta 报 volume/cell count/max depth；50M 护栏 | ✅ 溢流高程精确 |
| terrain.dinf_flow | dinf_flow_direction(+accumulation) | dinf_flow_analysis | tarboton1997 ✅ 8 三角面、面外截断、角度比例分流实现完整 | -1 平地哨兵（不填洼，披露）；角度∈[0,2π) 数学约定 | ✅ 坡面角精确+Σacc=D8 |
| terrain.flow_length | flow_length | flow_length_analysis | tarboton1997+strahler1957（弱相关） | downstream/upstream(MAX 口径披露)；米制步长 | ✅ 直线通道 |
| terrain.streams | extract_streams | stream_network | strahler1957 | accum≥threshold；无自动率定（披露） | ✅（经 strahler 测试） |
| terrain.strahler | stream_order | stream_network | strahler1957 ✅ 语义正确（唯一 max→同级，并列→+1） | 高程降序拓扑；order_distribution meta | ✅ 1+1→2 |
| terrain.morphometry | watershed_morphometry | watershed_morphometry_analysis | strahler1957 ✅ | 周长=4 邻域边缘和；basin length=MAX upstream（披露）；form factor/elongation | ✅ 圆形流域 ≤1e-6 |
| terrain.twi | topographic_wetness_index | topographic_index | beven_kirkby1979 ✅ | SCA=(accum+1)·A/w，κ=1 flat 口径披露；tanβ floor 1e-6 | ✅ 均匀坡恒定 |
| terrain.spi | stream_power_index | topographic_index | beven_kirkby1979 ✅ | SCA·tanβ；平地→0 | ✅ |
| terrain.ls_factor | ls_factor | ls_factor_analysis | wischmeier_smith1978+desmet_govers1996 ✅ | McCool m 表分档；n=1.3 固定披露 | ✅ 手算 1e-10 |
| terrain.openness | terrain_openness | terrain_openness_analysis | yokoyama2002 ✅ | 16(4-64) 方位×半径 1..R；米制距离；无采样方位剔除 | ✅ 平地≡0 |
| terrain.geomorphons | geomorphons | geomorphon_analysis | jasiewicz_stepinski2013 ✅ 决策表与 GRASS r.geomorphon 级联一致（探针核对） | 三元码/flatten/far；无采样腿按平（披露） | ✅ 峰/洼/坡 |
| terrain.landform | landform_classification | landform_classify | weiss2001 ✅ | 双尺度 TPI/SD+百分位 10 类决策表；SD=0→DegenerateData | ✅ |
| terrain.hillshade_multi | hillshade_multiazimuth | multiazimuth_hillshade | horn1981 ✅ | 与 band_math 逐位一致（真相源复算）；mean/min | ✅ 逐位相等 |
| terrain.horizon_angle | horizon_angle | horizon_angle_analysis | steyn1980+yokoyama2002 ✅ | 射线遇 nodata 即停（截断=无遮挡，披露）；钳 0 | ✅ 墙 arctan 精确 |
| terrain.sky_view_factor | sky_view_factor | sky_view_factor_analysis | steyn1980 ✅（cos²均值即 Steyn 公式） | 与 horizon 共用 `_horizon_rasters`（不重复逻辑）✅ | ✅ Steyn 公式复算 |

**缺席项（本次审计范围要求核对、仓库确实没有）**：breaching、HAND、Shreve order、watershed hierarchy、solar radiation、多向 SCA（κ 分解 / MFD）、Zevenbergen-Thorne 坡度变体。详见 §8。

## 2. Fake-native / 声明不实 findings

**未发现 fake-native**。26 个 descriptor 全部有真实 lib 实现 + 注册工具 + 通过中的 conformance 测试；`validate()` 0 issue（含工具存在性与 conformance 节点 AST 校验）。数值抽查（epsilon 填洼、D∞ 分流、Strahler 级联、TWI floor）均与声明语义一致。声明诚实度整体高于一般水平（roughness 口径偏离、D∞ 不填洼、horizon 截断低估、SCA κ=1 近似等均主动披露）。

发现的问题（非 fake，但「声明与实现/引用不符」）：

- **F1 [MAJOR] terrain.viewshed 无方法引用且 `approximate=False`**：扇区中心射线采样是 R3 型近似（descriptor 自己写明「扇区角离散 ≈ 最大距离处 1 像元弧长」），却零引用（R3：Wang, Robinson & White 2000；或 Franklin/Ray 家族）且不声明 approximate。registry 有 `approximate` 字段正是此用途。
- **F2 [MAJOR] 水文工具组合链断裂（工具层）**：`depression_fill` 只返回统计/样本，**不持久化填充后 DEM**；而 `dinf_flow_analysis`/`stream_network`/`topographic_index`/`flow_length_analysis`/`watershed_morphometry_analysis` 均无 `flat_routing` 参数、内部一律 `flat_routing='none'`。结果：descriptor 与工具描述推荐的组合「fill(epsilon>0) → D∞」在工具层**不可执行**——用户无法把填充面传给任何后续水文工具。真实含洼地 DEM 上，D∞/河网/TWI/流程长度全部在破碎流路上计算。只有 `flow_analysis`（契约 v2）有内置 epsilon 路由。
- **F3 [MINOR] watershed_delineation 边界采样点半像元偏移**：工具层 `centers_col = edge_cols + 0.5`（app/tools/terrain_analysis.py:526-527）。rasterio 仿射 `transform*(col,row)` 已映射到**像元中心**（实测 `t*(0,0)`=UL 像元中心），+0.5 使边界点整体偏东/南半像元。lib 层 `_cell_to_world`（contours 用）是正确的。
- **F4 [MINOR] 引用标签不精确**：`tarboton1997` 在 method_references.py 中标注 "D8 flow direction / flow accumulation"，但该文是 D∞ 论文（D8 惯例引用 O'Callaghan & Mark 1984 / Tarboton et al. 1991）；`terrain.flow/flow_length/watershed` 引用它属弱关联。contours 无引用（marching squares 可引 Marascuino-Montani 或至少声明 matplotlib 事实源，hillshade_multi 反而做了 truth_source 披露，风格不一）。
- **F5 [MINOR] 全域 `approximate=False` + `uncertainty_outputs=[]`**：openness/geomorphons/horizon/SVF 的方位离散、hillshade_multi 的 combine、SCA κ=1 等均为方法级近似，registry 提供的 approximate 维度在 terrain 域完全未使用——approximate 声明体系在域内形同虚设（不算说谎，但声明体系未被利用）。

## 3. 契约缺口

1. **slope/aspect/hillshade 无参数契约**（`parameter_contract_ref=""`）：`compute_terrain` 的 `products` 列表、z_factor、azimuth/altitude 均游离在契约体系外（其余 15 契约齐全且 parity 测试通过）。z_factor 在 lib 层支持（terrain_derivatives 有），但 compute_terrain 路径无法传。
2. **flat_routing 未覆盖 D∞**：`dinf_analysis` 契约只有 product；D∞ 契约建议「先 fill」但如 F2 所述链路不通。建议 dinf_analysis 升 v2 加 `flat_routing`（与 flow_analysis v2 对齐）。
3. **epsilon 累计抬升量未披露**：meta 报 `filled_cell_count` 但不报 max/mean cumulative lift；1e-5/像元在 10⁶ 像元宽平地累计 10 z 单位，足以改变后续坡度类产品的语义。
4. **水文拓扑类算法无「已填洼」输入校验**：stream_network/TWI 接受任意 DEM，无法声明/检测输入是否为 sink-free 面（可在 meta 输出 sink 统计并给 warning）。
5. **流长度/河网/TWI 未暴露 D∞ 口径**：契约把 D∞ 局限在 dinf_analysis 的 accumulation；TWI 的 SCA 只能来自 D8（descriptor 局限已披露）。
6. **compute_terrain 无本地 DEM 路径**（强制在线 STAC）；本地 DEM 坡度/坡向/hillshade 需绕道 multiazimuth_hillshade / terrain_derivatives（无 slope/aspect 产品）——域内没有本地 DEM → slope 的直接工具。
7. `raster_band_required:1` 前置声明仅在 harness 层评估，terrain 工具不显式调用（靠 `read(1)` 隐式成立）——一致但属「元数据与执行弱耦合」。

## 4. 数值风险图（平地 flow direction、边界、nodata、overflow）

| 风险点 | 位置 | 严重度 | 现状/缓解 |
|---|---|---|---|
| 平地路由（默认 none） | d8_flow | 中 | 平地/洼地=code 0，累积在平地截断；已披露，解药=epsilon 模式。探针证实 epsilon 模式下**全部内部洼地获得路由**（残留 26 sink 全为边界=出口语义，正确） |
| epsilon 模式平地梯度方向依赖堆发现序 | fill_depressions | 低 | 确定性（counter 裁决）但方向为发现序而非几何最短；Barnes 2014 已知语义，未在 meta 披露「方向任意性」 |
| epsilon 累计抬升 | fill_depressions | 中 | 1e-5/像元 × 长平地 → 米级抬升；仅报 filled_cell_count（见 §3.3） |
| 边界=出口 | d8/watershed | 低 | 一致语义并披露；流域贴边时偏小（披露） |
| nodata 当排水口 | fill_depressions 种子 | 中 | nodata 边界像元成为种子 → 大 nodata 花边（如 SRTM 边缘带）会把邻近地形拉向缝排空；已披露但真实 DEM 常见，建议 warning |
| D∞ 缺角面跳过 | dinf | 低 | 披露；边缘像元只用可得邻域，无发明值 |
| viewshed 观察点邻接 nodata | viewshed | 低 | 退化为最近有效像元（披露）；bilinear 需 4 有效角 |
| TWI tanβ floor | twi | 低 | 平地=截断上界（披露）；SPI 平地=0 |
| overflow | flow_accumulation(int64)/acc | 无 | int64 上限 9.2e18，≤50M 像元无风险；direction int16 编码安全；dinf acc float64 守恒（探针 Σacc 1120=D8） |
| 积分图精度 | roughness/TPI | 低 | 大均值/小离散时浮点损失——meta 已披露 |
| curvature edge 复制 | surface_curvature | 低 | 退化为单侧差分+NaN support，披露 |
| 半像元偏移 | watershed_delineation 工具 | 低 | 见 F3 |

## 5. 规模/后端缺口（大栅格 chunk 化现状、内存上界）

- **全量读入**：所有 terrain 工具经 `_read_terrain_window` `src.read(1).astype(float64)`——整幅进内存。`RasterResourceGuard.check_grid` 默认按 4 B/像元估 1 GiB → 放行 ≤250M 像元，但 float64 实际 2 GiB 起（口径不一致）。
- **水文护栏**：`_guard_cells` 50M 像元先拒绝（estimate-before-allocate，`ResourceScaleMismatch`）——设计良好。但：
  - **fill_depressions 纯 Python heapq**：O(N log N) 但常数巨大，10M 像元分钟级、50M 接近不可用；堆本身 O(N) tuple ≈ 数 GB。descriptor 已披露「>10M 耗时显著」。
  - **flow_accumulation / stream_order / flow_length(upstream) 是逐像元 Python 循环**（`tolist()` + searchsorted）：50M 像元数十秒-分钟级；内存有界。
- **最坏的组合内存**：`terrain_openness`（pos_max/neg_max/az_has 三个 `(n_az,h,w)` 数组）与 `_horizon_rasters`（`(n_az,h,w)` float64）——**64 方位 × 50M 像元 × 8 B = 25.6 GB/数组，2-3 个数组 ≈ 50-77 GB**，全部在「50M 像元安全包络内」爆炸。护栏只封格子数、未封 `方位数×像元数` 联合积。**这是域内第一优先级修复**（per-azimuth 循环累加即可 O(h·w) 内存）。geomorphons 的 (8,h,w) int8 = 3.2 GB 尚可。
- **viewshed 无 lib 护栏**（未调 `_guard_cells`）：全网格 dist/theta/sector/bin_k/alpha ~6-8 个 (h,w) float64/int64；在 250M 像元读护栏下可达 10+ GB。扇区采样本身已分块（256×k_eff）——好的。
- **chunk/window 现状**：`app/lib/geo_raster/windowed.py` 提供带 halo 的分块执行 + 协作取消 token + 进度——**terrain 域完全未使用**。局部算子（slope/hillshade/TPI/TRI/roughness/curvature/openness/geomorphons/horizon/SVF）天然 window_safe+halo，可迁；全局拓扑类（fill/accumulation/strahler）需分块 Priority-Flood（Barnes 的 tiled 变体）。
- **后端选择层空转**：26 个 descriptor `backend_variants=0` → `select_backend` 恒返回 "no_variants_declared"，工具里的 backend diagnostic 恒为默认路径。numba 在依赖中但零使用；GDAL（`gdal.DEMProcessing` 的 slope/aspect/hillshade/fill/TWI）是现成的第二变体候选，词表里 `gdal`/`numba` 都在 BACKEND_VOCABULARY，却没有任何 terrain 算法声明变体。
- **取消支持**：registry 层有 OperationCancelled 分类（57d7df3 修复的正是 flow_analysis 注册处的语法），但 lib 层长循环（fill 堆、accumulation）无取消/进度轮询——一旦启动不可中断；windowed.py 的 cancellation token 未被复用。

## 6. 不确定性缺口

- 所有 26 个 descriptor `uncertainty_outputs=[]`：无 DEM 垂直 RMSE → slope/curvature/TWI 的误差传播（解析或 Monte-Carlo），无 per-cell 置信带。
- 输入质量不设防：DEM 垂直基准/单位错误（英尺 vs 米）仅靠用户传 z_factor，无自动量纲检测或 sanity warning（z_factor 契约只是参数）。
- 阈值类产品（streams/morphometry 排水密度）无敏感性报告（如多阈值曲线）——descriptor 披露「阈值敏感」但输出不带敏感性信息。
- SCF/horizon 的方位离散不确定度可解析估计（≤360/N 的角度量化），meta 只给定性披露。
- viewshed 可见性对 observer_height/DDEM 误差的敏感度无输出（山地 2 m 观察高差可翻转远距可见性）。

## 7. 参考文献验证

method_references.py 中 13 个 terrain 相关键全部存在且为真实文献、题录准确：

| 键 | 核对 |
|---|---|
| horn1981 | ✅ Horn, Hill Shading and the Reflectance Map, Proc. IEEE 69(1) 1981 |
| zevenbergen_thorne1987 | ✅ ESPL 12(1) 47-56 |
| tarboton1997 | ✅ WRR 33(2) 309-319 —— **但它是 D∞ 论文**；被当作 D8 引用（F4） |
| barnes2014 | ✅ Computers & Geosciences 62:117-127（Priority-Flood 原文，含 epsilon 变体——实现与其机制一致） |
| strahler1957 | ✅ Trans. AGU 38(6) 913-920 |
| beven_kirkby1979 | ✅ Hydrological Bulletin 23(1) 43-69 |
| wischmeier_smith1978 | ✅ USDA AH 537；（McCool 1987 m 表未单列引用，折叠在 assumptions——建议补 mccool1987 键） |
| desmet_govers1996 | ✅ JSWC 51(5) 427-433（n=1.3 为该实现惯例，已披露） |
| yokoyama2002 | ✅ PE&RS 68(3) 257-265 |
| jasiewicz_stepinski2013 | ✅（geomorphons 原文，决策表与原文图 3/GRASS 级联一致） |
| steyn1980 | ✅ Atmosphere-Ocean 18(3) 203-207（SVF=⟨cos²ψ⟩ 正是 Steyn 公式——引用精准） |
| weiss2001 | ✅ ESRI UC poster（TPI 决策表口径正确） |
| wilson2007 | ✅（roughness 用作语境而非公式——已诚实披露口径差异） |

**缺失引用**：viewshed（Wang/Robinson/White 2000 R3 或 R2）、contours（marching squares）、D8 本源（O'Callaghan & Mark 1984）、McCool 1987（m 表）、Fu & Rich 2002（若做 solar radiation）。

## 8. V3 建议清单

| # | 项目 | 方法引用建议 | 现状差距 | 可行性 | 测试策略 | 优先级 |
|---|---|---|---|---|---|---|
| V3-1 | **水文组合链修复**（F2）：depression_fill 输出可持久化 artifact；dinf/stream/wetness/flow_length/morphometry 加 flat_routing/输入面参数 | barnes2014 | 推荐组合不可执行（§2-F2） | 高（工具层薄改 + 契约 v2） | 工具级集成测试：fill→dinf 流路连续性；含洼地 DEM 的 Σacc 守恒 | **P0** |
| V3-2 | **openness/horizon/SVF 内存重构**：按方位循环累加，消 (n_az,h,w) 物化；加 `azimuths×cells` 联合护栏 | yokoyama2002; steyn1980 | 25-77 GB 风险（§5） | 高（算法结构不变） | 现有 fixture 结果逐位回归 + 大网格 mock 内存断言 | **P0** |
| V3-3 | **breaching（选择性开凿）** | Lindsay (2010) J. Hydrol. 384:198-209「efficient hybrid breaching-filling」; Lindsay & Creed (2005) | 只有 fill 无 breach；深窄洼地 fill 造成巨大填方 | 中（最短路径/最小代价树，Dijkstra on 网格） | 合成深窄槽：breach 体积 < fill 体积且流路打通；体积守恒 | **P1** |
| V3-4 | **D8/D∞ parity 属性测试套件** | tarboton1997; O'Callaghan & Mark 1984 | 仅 1 个 Σacc parity 用例；无随机 DEM 属性测试 | 高（无新实现） | hypothesis 式随机 DEM：无环、接收者严格更低、Σacc 守恒、epsilon 后 0 内部 sink、D8/D∞ 出口一致性；ties 确定性 | **P1** |
| V3-5 | **HAND（Height Above Nearest Drainage）** | Gharari et al. (2013) HESS 17:1207-1216; Rennó et al. 2008 | 完全缺席 | 中-高：stream mask 为种子的 constrained priority-flood，沿流路累 zz−z_stream | 合成河道：HAND=0 于河道、随距河高程差单调；nodata 披露 | **P1** |
| V3-6 | **viewshed 升级/声明**：exact R2/R3（X/W 运行角矩阵）或至少 `approximate=True`+曲率/折射选项 | Wang, Robinson & White (2000) IJGIS 14(5); Ratt 1995 | 扇区近似未声明 approximate；无曲率（已披露） | 中（R3 与现扇区结构相近，可向量化） | 与暴力逐目标射线在 256² 随机 DEM 上 parity ≤1 像元差异率阈值；解析锥形遮挡 | **P1** |
| V3-7 | **Shreve 链级 + 河源/链拓扑** | Shreve (1966) J. Geology 74; Horton 1945 | 只有 Strahler；无 magnitude、无链（link）分割 | 低（stream_order 同拓扑序一遍） | Y 树 magnitude=链接数精确；order_distribution 同现测 | **P2** |
| V3-8 | **流域层级（sub-basin partition）** | Pfafstetter 编码或 Strahler 级出口递归 | 只支持单 pour point 掩膜 | 中（递归 outlet 检测 + BFS） | 三级合成盆地：子流域面积和=父；编号唯一性 | **P2** |
| V3-9 | **catchment morphometry 扩充**：hypsometric curve/integral、Gravelius 紧凑度、bifurcation/area ratio（Horton 定律） | Strahler 1957/1964; Horton 1945 | 已有 form factor/elongation/relief/density | 低（掩膜+已有河网统计） | 合成形状解析值；Horton 比 ∈ 区间 | **P2** |
| V3-10 | **solar radiation（晴空短波/日照时数）** | Fu & Rich (2002) ASCE/ArcGIS Solar Analyst; Šúri & Hofierka 2004 | 完全缺席；`_horizon_rasters` 是现成基建 | 中：sun position + horizon 插值 + 大气透过简单模型（approximate 声明） | 解析平面：cos(入射角) 逐位；遮挡=horizon 判据 vs 独立复算 | **P2** |
| V3-11 | **多尺度 openness / 自适应 geomorphon flatten** | Yokoyama 2002（多半径）; Stepinski & Jasiewicz 后续/GRASS `-d` | 单半径/单 flatten | 低 | 双半径差值 fixture；噪声 DEM 上 flatten 自适应分类稳定性 | **P3** |
| V3-12 | **multi-scale TPI 栈**（N 窗口标准化 TPI 输出而非仅双尺度分类） | Weiss 2001 完整语义 | 已有双尺度 + 单尺度 tpi | 低（复用积分图） | 多窗口线性组合手算；SD 披露 | **P3** |
| V3-13 | **multidirectional hillshade 标准化**：16 方位默认档 + altitude 扫描组合（ESRI 兼容） | Horn 1981; ESRI MDOW | 已有任意方位 mean/min | 低 | 与单方位逐位（已有）+ 16 方位均值 golden | **P3** |
| V3-14 | **solar approximation 的 honest metadata**（若做 V3-10 必须配） | — | — | — | — | （随 V3-10） |

## 9. 现有算法增强建议

1. **D8 平局裁决披露**：tie_break=最低索引邻域（E 优先）在方位偏好上系统性偏东——可在 meta 增加并列像元计数，提示其对河网起点密度的影响。
2. **epsilon 自适应**：epsilon 可按 DEM 垂直精度/量程自动建议（如 z_range·1e-9），并输出累计抬升分布（§3.3）。
3. **fill 的 nodata 花边警告**：种子中 nodata-邻接像元占比高时给 warning（§4 风险）。
4. **accumulation 向量化**：按高程分层的 Kahn 向量化（np.add.at 按层）或 numba `@njit` 逐像元松弛，消 Python 循环；fill 堆循环同理（numba堆或 scipy.sparse.csgraph 拓扑）。
5. **stream_order 输出链级几何**：可把 order 栅格转线要素（复用 contours 的世界坐标映射管线）以支持制图。
6. **TWI/SPI 的 D∞ SCA 选项**：SCA 用 dinf 比例累积（κ 分解的最低配），缓解发散坡 SCA 低估（descriptor 已披露该局限——顺手兑现）。
7. **flow_analysis 输出接收者图（可选）**：`receiver` 数组已在结果 dict，工具层可按需导出稀疏邻接供 morphometry 复用，避免 watershed_morphometry 内部重复 d8+accumulation+strahler 三遍（当前 morphometry 一次调用内部重算 flow_length/accumulation/stream_order，成本 3×）。
8. **修复 F3 半像元偏移**（一行）+ contours/watershed 坐标管线统一走 `_cell_to_world`。
9. **compute_terrain 补本地 DEM 路径 + slope/aspect 本地产品**（§3.6），或至少把 slope/aspect/hillshade 纳入契约体系（§3.1）。
10. **conformance 增强**：给 sink_fill/dinf 增加与独立实现（如手写暴力 flood/暴力射线）的 parity 用例，呼应 oracle corpus「公开入口未覆盖」的运行器限制（terrain 域 216 个 oracle 全部打在私有基元层，公开入口只有 64 个单测守门）。

---

**审计结论**：terrain/hydrology 域是该仓库科学质量最高的域之一——实现真实、披露文化强、测试/护栏/oracle 三层齐全（57d7df3 修复后该区域健康）。P0 问题是工程性的：openness/horizon 家族的方位×像元组合内存（25-77 GB 包络内爆炸）与水文工具组合链断裂（fill→D∞ 不可执行）；P1 补科学面：breaching、HAND、viewshed 引用与 approximate 声明、D8/D∞ parity 属性测试。
