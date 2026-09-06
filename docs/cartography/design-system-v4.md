# Cartographic Design System V4

> 分支：`feat/cartographic-design-system-v4` · 上游：ADR-0101（V3）
> 状态：本文档描述 V4 落地后的实际能力（非 roadmap）。planned 能力诚实登记。

## 1. V4 是什么

V3（ADR-0101）建立了「Map Product = Model + Roles + Composition + Slots +
Variants + Theme + Layout + Output」的产品公式。V4 把它升级为完整的
**Design System**：

| 维度 | V3 | V4 |
| --- | --- | --- |
| Map Models | 50（31 native / 19 planned） | **80（76 native / 4 planned）** |
| Component types | 19 | 19（不变 —— 类型词表稳定，能力在变体层扩展） |
| Component variants | 62 | **100** |
| Component templates | 65（3 planned） | **100（0 planned）** |
| Composition templates | 28 | 28 |
| Themes | 5 | **11**（+scientific/publication/government/remote_sensing/terrain/risk_communication） |
| Palettes | 21 | 22（+Gray 灰度带）+ **3 个双变量色阵**（独立语义族） |
| Chart kinds | 4（bar/line/pie/scatter） | **18 native + 1 planned（violin）** |
| Layout solver | V2 单遍贪心 | **V3 约束式**（列单位/碰撞组/占用/紧凑/诊断） |
| Label engine | 无 | **确定性标注引擎**（候选/碰撞/keep-upright/callout） |
| Golden corpus | 141 | **503** |

## 2. 单一真相源（未建立第二套 registry）

V4 的所有扩展都是对既有 registry 的**原位扩展**：

- `app/lib/cartography/model_library.py` + `model_packs/`：模型目录；
- `app/lib/cartography/component_registry.py`：组件描述符（V4 增量字段：
  states / size_range / collision_class / responsive / interactions /
  accessibility）；
- `app/lib/cartography/component_templates.py`：变体模板；
- `app/lib/cartography/component_renderers.py`：live/exporter 支持矩阵
  （机器真值）；
- `app/lib/cartography/composition_templates.py` + `composition_packs/`；
- `app/lib/cartography/themes.py` / `palettes.py` / `bivariate.py`；
- `app/lib/cartography/chart_kinds.py`：图表 kind 词表 + 七态状态机 +
  Agent 操作表（后端权威，前端 catalog 导出镜像）；
- `app/lib/cartography/design_system.py`：四 registry 的**单一投影**
  （manifest / map_model_requirement / component_requirement）——不是新
  registry，是只读投影。

## 3. 模型库 V4（80 = 76 native / 4 planned）

### 3.1 planned → native 的判定标准

native 化必须满足**全链真实**：渲染链存在（converter/前端编译器）+ 数据
契约成文（data_preconditions_zh）+ 降级链可解析。V4 原生化的 17 个模型
逐一说明：

| 模型 | native 化路径 |
| --- | --- |
| hillshade | 服务端 Horn 法预渲染灰度（raster_render.py）+ raster 层；MapLibre 原生 hillshade 图层（raster-dem 源）未接线 —— pitfalls 如实披露 |
| elevation_tint_hillshade | 分层设色 × 晕渲 alpha 合成（render_mode=hillshade_blend） |
| classified_raster | 断点分级离散色阶（render_mode=classified） |
| sar_intensity_surface / sar_change_detection | 通用栅格渲染链 + dB/对称色标数据契约 |
| temporal_trend_surface / anomaly_surface / uncertainty_surface | 栅格渲染链 + 趋势/背景态/方差契约 |
| bivariate_choropleth | 双字段 3×3 分位分级（bivariate.py）+ 色阵 match 投影 + bivariate 图例 |
| uncertainty_choropleth / uncertainty_point_symbol | 透明度/描边宽双编码契约（UNCERTAINTY_OPACITY） |
| dot_density_map | 确定性 Halton 撒点（dot_density.py，上限截断+披露） |
| point_cluster | 前端 cluster source + 三子层编译（簇圆/计数/未聚类点） |
| route_map / accessibility_network / network_centrality_map | 线链语义通道（rank 宽度插值/分级线）+ 产物契约 |
| sensitivity_analysis_presentation | 情景对比经面板/图表族（chart_needs 绑定） |

### 3.2 诚实 planned（4 个）

- `dasymetric_map`：控制要素重分配算法未实现；
- `before_after_swipe`：runtime 无 swipe 交互语义（导出侧双帧等价物已
  在 pitfalls 说明）；
- `small_multiple_map`：多画幅组合运行时未实现；
- `cartogram_map`：面积保持变形算法未实现。

planned 必须登记 pitfalls（validate 强制），golden corpus `planned-gate::`
维度锁定其不得获得任何绑定组件实例。

## 4. 组件系统 V4

### 4.1 变体矩阵（100）

- **chart_panel**：4 风格 + 18 kind 变体（chart_kinds 词表；kind 变体是
  preset 句柄，data-shape 家族内改写类型，形状不兼容诚实保持原 type）；
- **legend**：academic/compact/report/horizontal + **bivariate**（3×3
  色阵）/ **uncertainty**（透明度阶梯）/ **size**（∝√值圆环）/
  **line**（线宽分级）/ **composite**（多层复合）；
- **annotation**：text/callout/group + footer/timestamp/projection_note/
  data_source/highlight（版面附注族）；
- 其余：categorical nested、graticule projected、north_arrow
  dual_convention、title banner/compact、subtitle compact、statistics
  explanation、table dense、methodology data_quality、inset hierarchy。

### 4.2 V4 描述字段（组件能力契约）

每个描述符携带：`states`（合法状态；chart_panel 为七态词表）、
`collision_class`（chrome/legend/panel/canvas/none —— 布局求解的防重叠
分组）、`responsive`（none/collapse/reflow/hide）、`interactions`（与
AGENT_CHART_OPERATIONS 同词表）、`accessibility`（role + label_zh +
keyboard_operable）。测试锁定：碰撞类词表封闭、可达性元数据全量在场。

### 4.3 渲染真值

live 渲染与导出绘制同链（ADR-0081 parity 原则不变）。V4 导出链补齐：

- **table_panel 导出**：canvas 有界表格（8 行 + 截断披露），exporters
  翻转为 png/pdf/svg；
- **图表 kind 导出**：18 native kind 逐 kind canvas 绘制；violin 明确
  「暂不支持导出」披露条（不生成空组件冒充）；
- **bivariate 图例导出**：3×3 色阵 + 双轴标（与 live BivariateMatrix
  同源逐格）。

## 5. 浮动统计图表系统

### 5.1 七态状态机

`hidden / visible / collapsed / expanded / floating / docked / anchored`，
有向迁移表（hidden 只能经 visible 回场；floating/docked/anchored 三三
互转）。权威在后端 `chart_kinds.py`；前端 `chart-state.ts` 镜像 +
placement 派生/投影 + serialization/replay 快照。docked 态由 dockSlice
承管（刻意不进 MapSpec —— 与既有 dock 契约一致）。

### 5.2 用户 / Agent / 回放共用一份真相

用户 FloatingChrome 手势与 Agent `chart_*` 命令走**同一**突变通道
（`commitComponentPatch` → mapspec/mutations patch_component，
ownerToken + revision CAS）。Agent 命令：chart_set_state / move /
resize / switch_type（kind 变体通道）/ highlight（selection store 广播）
/ close / restore / collapse / expand。服务端 `control_floating_chart`
工具做词表与状态机预检（非法迁移拒绝、planned kind 引导回退）。

### 5.3 图表 kind 词表（18 native）

recharts 族：bar / horizontal_bar / grouped_bar / stacked_bar / line /
area / scatter / histogram / pie / donut / radar / rose / timeseries /
cumulative。自绘 SVG 族（recharts 无原生支持，诚实自绘）：box_plot
（五数概括）/ heat_matrix（行×列色阵）/ kpi_card / ranking_list。
planned：violin（核密度分布渲染未实现 —— 不伪造；引导退回 box_plot /
histogram）。

## 6. Layout Solver V3

`solve_layout_v3` = V2 严格超集（同模块；V2 API 字节级稳定）：

- **列单位容量域**：width_units（宽面板占多列，first-fit）；
- **碰撞组**：collision_group 同组不得落同槽（图例族组级防重叠）；
- **占用约束**：zone_occupancy / avoid_zones（地图内容/UI 占用扣减容量，
  参与者沿候选链迁移）；
- **compact 模式**：容量与预算收紧（小视口/窄图幅）；
- **可解释输出**：每个放置带 reason（requested / fallback_zone /
  collision_group_move / avoid_zone_move / compact_reflow /
  required_overflow_kept），未解决冲突进 `conflicts`，降级建议进
  `fallback_plan_zh`；
- 确定性：单遍贪心 + 稳定全序（required → priority → id），同输入永远
  同输出；V2 形输入结果与 V2 一致（回归锁定）。

前端像素真值仍在 `frontend/lib/map-components/resolve-layout.ts`
（分工契约见 layout-solver.md）。

## 7. Label Engine Foundation

`app/lib/cartography/label_engine.py`：纯确定性标注布局（服务**导出
画布/SVG**与语义检查的确定性估计；交互面仍由 MapLibre GPU 标注承担）：

- 候选生成：point 8 方位（右上首选）/ line 等弧长锚点 + keep-upright /
  polygon 质心+内点 / shield 单候选；
- 碰撞：均匀格网空间索引 + AABB；repeat_distance 同文本去重；
- 降级：位移超限 callout（引线端点显式输出）→ suppressed（原因可读：
  collision / below_min_zoom / empty_text / repeat_distance /
  no_candidates）；
- CJK 加权字宽估计（estimate_label_box）。

## 8. Theme / Palette V4

- **11 主题**：V3 五主题 + scientific（期刊）/ publication（出版印刷，
  print 严格校验）/ government（政务）/ remote_sensing（遥感暗底）/
  terrain（地形）/ risk_communication（风险沟通，色盲安全优先）；
- **对比度诊断**：WCAG 2.x contrast_ratio / meets_wcag_contrast /
  palette_contrast_diagnostics（非法输入 fail-closed）；
- **双变量色阵**：BIVARIATE_MATRICES 独立语义族（非单色 ramp 伪装）；
- **模型主题绑定**：default_theme 字段（terrain/remote_sensing/risk/
  scientific 四域 11 个模型），经 design_system 校验闭环；
- 校验器真实执法：print 主题推荐必须全 print_safe（灰度 ΔL≥0.06）、
  colorblind_safe_first 主题推荐必须全色盲安全（V4 开发中曾逮住 risk
  主题误荐 Set1 → 修正 Dark2）。

## 9. Workflow / Agent 契约（本分支不重写 planner）

本分支提供（仅描述层）：

- `design_system.build_design_system_manifest()`：四 registry + 能力矩阵
  的单份带版本快照（schemaVersion 4）；
- `design_system.resolve_map_model_requirement(model_id)`：
  `map_model_requirement`（数据需求/前置条件/图层构成/组件/主题/图例/
  图表/交互/导出约束/降级链）；
- `design_system.resolve_component_requirement(type, variant)`：
  `component_requirement`（variant 词表/位置域/状态/碰撞类/交互/导出
  支持/可达性）；
- Agent 图表操作：`control_floating_chart` 工具 + 前端 chart_* 命令
  （§5.2）。

Workflow planner 与 Pi runtime 未改动。

## 10. Golden Cartography Corpus（503）

结构化 digest（非像素）：resolve → compose → validate → solve。维度：

| 维度 | 用例数 | 覆盖 |
| --- | --- | --- |
| base | 273 | native 模型 × 兼容模板（前 5） |
| variants | 100 | 全部 native 组件变体钉选全链 |
| stress | 60 | 30 模型 × 中英极端长标题 × A4 双版式 |
| profiles | 30 | 6 模型 × 5 page profile |
| layout | 12 | V3 约束场景（compact/占用/碰撞组/列单位/exclusive/avoid） |
| labels | 12 | 标注引擎确定性场景 |
| multi | 6 | 多层绑定（heat+choro/双年/flow/bivariate/cluster/hillshade） |
| planned-gate | 4 | planned 泄漏门 |
| edge | 6 | 钉选拒绝/接受、空标题、极端图例标签、缺 context |

刷新协议不变：`GOLDEN_CORPUS_UPDATE=1` 写盘后故意 fail，人工 review
diff 后入库。corpus 扩容过程中**真实抓出并修复**了一个 V3 遗留契约缺口
（sar_change_report 模板 required legend 槽因 colorbar 兼容清单未含
sar_change_detection 而必然缺失 → 图例族 compatible_map_models 全量补齐）。

## 11. 验证矩阵

```bash
# 制图整 lane（release-blocking 语义与 CI 一致）
pytest -m cartography --no-cov -q
# 目录文档新鲜度门
python -m app.lib.cartography.catalog_docs --check
# 前端
cd frontend && npm run typecheck && npm run test -- --run && npm run build
```

## 12. Known Limitations（诚实登记）

- MapLibre 原生 hillshade 图层（raster-dem 源）未接线：hillshade 视觉由
  服务端预渲染 PNG 承担 —— 预览/导出像素一致（同源），但无法随视角实时
  重算；
- SVG 导出仍是位图嵌 SVG 容器（真矢量管道 #844 已删，未重建）；
- violin / box_plot 的分布平滑（KDE）渲染未实现（violin planned）；
- before/after swipe 交互未实现（导出侧双帧并排是等价物，planned）；
- dasymetric / cartogram / small-multiple 需要的算法/运行时未实现；
- dock 状态刻意不进 MapSpec（dockSlice 承管）—— chart 状态机的 docked
  投影返回 null 是契约而非缺陷。
