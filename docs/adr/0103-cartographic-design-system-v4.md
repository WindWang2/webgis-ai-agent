# ADR 0103 — Cartographic Design System V4

日期：2026-09-06
状态：Proposed（随 feat/cartographic-design-system-v4 分支交付）
前置：ADR 0101（Cartographic Template & Component Library V3）、ADR 0081
（导出 parity）、ADR 0084（确定性前端布局）、ADR 0078（legend_spec 单一
真值）

## 背景

V3 交付后系统具备 50 Map Models（31 native / 19 planned）、19 组件类型 /
62 变体 / 65 模板、28 组合模板、V2 布局求解、5 主题、141 例 golden
corpus。剩余缺口：约 19 个高价值 planned 模型（hillshade / SAR / 双变量 /
点密度 / 聚类 / 不确定性族等）、图表系统只有 4 种单序列 kind、布局求解
没有占用/碰撞组/诊断语义、无标注引擎、主题未覆盖专业制图场景、导出侧
表格/图表 kind/双变量图例不可渲染。

## 决策

### D1 — 模型库扩容到 80（76 native / 4 planned），native 化走全链真实

- 新增 `raster_render.py`（Horn hillshade / 分级栅格 / 设色晕渲合成 /
  双变量逐格分级）、`dot_density.py`（确定性 Halton 撒点）、
  `bivariate.py`（双字段 3×3 分级 + 色阵 + match 投影）；
- 栅格 converter 增加 `render_mode`（continuous 缺省不变 / hillshade /
  classified / hillshade_blend / bivariate）；
- 矢量 converter 增加 dot_density / bivariate / cluster / uncertainty /
  route 语义分支，契约缺失一律诚实降级 + 警告；
- 前端编译器支持 cluster source + 三子层发射（簇圆/计数/未聚类点）；
- planned 收缩到 4 个（dasymetric / before_after_swipe / small_multiple /
  cartogram），每个登记真实未实现原因。
- native 判定 = 渲染链存在 + 数据契约成文（data_preconditions_zh）+
  降级链可解析；MapLibre 原生 hillshade 图层未接线以服务端预渲染实现
  视觉，pitfalls 如实披露。

### D2 — 组件模块化：类型词表稳定，能力在变体层扩展

不新增 ComponentType 成员。变体 62 → 100（图表 18 kind preset、图例族
bivariate/uncertainty/size/line/composite、注记版面族 footer/timestamp/
projection_note/data_source/highlight 等）。V3 的 3 个 planned 前瞻模板
随渲染链落地转正，种子目录 planned 清零。描述符新增 V4 字段（states /
size_range / collision_class / responsive / interactions / accessibility），
全部带默认值纯增量。

### D3 — 图表 kind 词表与七态状态机（chart_kinds.py 单一权威）

18 native kind（14 recharts 族 + 4 自绘 SVG 族）+ violin planned。
状态机 hidden/visible/collapsed/expanded/floating/docked/anchored（有向
迁移表）；Agent 操作词表 AGENT_CHART_OPERATIONS。前端 catalog JSON 镜像
导出（schemaVersion 4）。用户手势与 Agent `chart_*` 命令共用
commitComponentPatch 突变通道 —— 同一份 MapSpec 真相，用户始终可覆盖；
服务端 `control_floating_chart` 工具做词表/状态机预检。

### D4 — Layout Solver V3 = V2 严格超集

同模块扩展 `solve_layout_v3`：列单位容量（width_units）、碰撞组互斥、
zone_occupancy/avoid_zones 占用扣减、compact 模式、可解释 reason、
conflicts 诊断 + fallback_plan_zh 降级方案。V2 API 字节级稳定；V2 形输入
结果与 V2 一致由测试锁定。前端像素真值仍在 resolve-layout（分工不变）。

### D5 — Label Engine Foundation

`label_engine.py` 纯确定性标注布局（点 8 方位候选 / 线 keep-upright /
面内点 / shield / repeat_distance / callout / 格网空间索引），服务导出
与语义检查的确定性估计；交互面标注仍归 MapLibre GPU。

### D6 — Theme/Palette V4

主题 5 → 11（scientific / publication / government / remote_sensing /
terrain / risk_communication）；WCAG 对比度工具（fail-closed）；双变量
色阵独立语义族；模型 default_theme 绑定。校验器执法真实（print 主题全
print_safe、cb-safe-first 主题全色盲安全 —— 开发中曾逮住 risk 主题误荐
Set1）。

### D7 — 导出 parity

table_panel 获得有界 canvas 表格导出（8 行 + 截断披露）；18 native
图表 kind 逐 kind canvas 绘制（violin 画披露条，不生成空组件）；
bivariate 图例 3×3 色阵导出。支持矩阵与 descriptor 双向对账同步翻转。

### D8 — Golden corpus 503 + design_system 投影

corpus 升到 500+（新增 variants 钉选 / profiles 版式矩阵 / layout 约束
场景 / labels 标注场景四个维度），digest 升级 V3 求解器（reasons/
conflicts）。corpus 扩容抓出并修复 V3 遗留契约缺口（图例族
compatible_map_models 未跟上模型扩容 → sar_change_report required 图例
槽必然缺失）。`design_system.py` 提供四 registry 的单一只读投影
（manifest / map_model_requirement / component_requirement）—— 供
workflow 请求契约，不重写 planner。

## 非目标（本分支不做）

- Workflow planner / Pi runtime / Data Fabric / `app/lib/gis/algorithms`
  内部不改动（converter 层最小兼容扩展除外）；
- MapLibre 原生 hillshade 图层（raster-dem 源）接线；
- 真矢量 SVG 管道重建；
- swipe / 多画幅 / cartogram / dasymetric 运行时。

## 后果

- 模型覆盖谱系完整（地形/遥感/时序/统计面/网络/决策），planned 收缩到
  4 个且全部有据可查；
- 图表系统成为 Agent 与用户共同可控的一等产品；
- 布局与标注具备确定性可测试的语义层；
- corpus 503 例锁定全部契约（任何漂移在本地 lane 即刻爆出）。
