# ADR-0156: 图面整饰自动排版 —— 版面描述中间层与冲突自愈

- 状态: Accepted（本 PR 落地）
- 日期: 2026-09-13
- 关联: ADR-0084（Cartographic Layout Engine）；ADR-0101（Design System V3/V4）；ADR-0118（Rendering V5）；ADR-0126（Rendering V6 Publication Engine）；adaptive-cartography 01/02/03/04/09 线

## Context

组件骨架完整（20 类型词表 / 19 descriptor / 18 live 渲染器，P0 勘察校正），但
版面仍是「摆上去」而非「排出来」：

1. **碰撞只告警不自愈**：`LAYOUT_COLLISION` warning 无修复建议；
   `COMPONENT_LINK_CYCLE` fail + not_repairable，断环靠渲染回退
   (priority,id) 序。P0 基线（20 MapSpec × 4 版式，golden corpus）：compose
   直出（未经 solver）口径 LAYOUT_COLLISION 告警 **72/80（90%）**，根因单一
   （top-center exclusive 双组件）；注入越界浮动件后 COMPONENT_OUTSIDE_CANVAS
   100% 触发。
2. **缺项靠兜底**：live/export 缺 north_arrow/scale_bar 时被动注入
   `__fallback_*`，无按输出用途 × 内容要素的主动必配清单；数据来源未知时
   无占位署名（省略署名违反制图伦理）。
3. **组件能力缺口**：无数字比例尺（1:xx）、无真北/磁北偏角注记、经纬网密度
   不可调（唯一由 zoom 表决定）、无图廓四角坐标注记、图例 unit 存在却不渲染、
   无 out_of_range 表达。
4. **版面决策不可审计**：无决策工件 —— 09 线无法评审、10 线无法回归、
   live/export/后端 SVG 三通道兜底语义不一致（后端 include_chrome 不注入
   fallback，user-wins）。

## Decision

### 1. 版面描述中间层 —— CompositionDescriptor（本线产出，08 线消费）

`frontend/lib/layout/composition-descriptor.ts`（version=1，纯数据可序列化）：
`page`（版式档 profile + 画布）/ `elements[]`（落位 + provenance 三值
`spec|autofill|fallback` + 修复轨迹）/ `decisions[]`（可审计工件，与后端
`component_composer.CompositionDecision` 同构）/ `chrome`（numericScale /
declination / graticule 配置）。live 由 `composeMapLayout` 产出；export 侧
只读消费 `chrome` 段（整饰描述边界内）。provenance 三值让「用户声明 / 主动
补全 / 被动兜底」在所有通道可对账 —— 后端 include_chrome 维持 user-wins
不虚构，中间层携带 provenance 供其采纳。

### 2. 冲突自愈（P1）：选位 → 选位 + 自愈

- **后端 V4**（`layout_solver.solve_layout_v4`，V2/V3 零改动）：V3 求解后
  仍有 suppressed/conflicts 时执行策略链 **改 anchor → 缩尺寸 → 折叠进
  溢出面板（bottom-center 槽容量覆写 + collapsed 标记）→ 隐藏最低优先**
  （donor = optional 已放置者中 (priority, id) 最大者）。duplicate_singleton
  抑制不参与自愈（重复本身非法）；required 无让位对象时保留原位（V3 语义）。
- **修复规划器**（新建 `component_composer.plan_layout_repairs`）：对
  MapSpec 现状组件产出同词表动作链（含 singleton 重复隐藏、floating 重叠
  折叠建议 —— user-wins advisory），`semantic_checks.LAYOUT_COLLISION` 以
  `repairability=auto_safe + suggested_fix` 附带（**status 保持 warning** ——
  quality_loop 只自动修 fail，user-wins 语义零回退）。
- **断环**（`component_graph.break_component_cycles`）：每环断开最低权重边
  （权重 = 端点 priority 和，平局 (dst,src) 字典序），产出 remove_links
  证据；`COMPONENT_LINK_CYCLE` 附
  `repairability=auto_with_semantic_risk`（断边是语义手术，quality_loop
  现有语义即「需 explicit intent」，不制造不可执行的 auto 通道）。
- **前端执行器**（`frontend/lib/layout/composition-repair.ts`）：对共享
  求解器 `resolveComponentLayout` 的碰撞输出执行同一策略链（user-pinned
  绝不挪动/折叠/隐藏），步骤轨迹进 descriptor。
- 验收：修复建议应用到 P0 语料 → 三类版面检查归零
  （`tests/cartography/test_ac07_zero_regression.py`）。

### 3. 缺项主动补全（P2）

`required_components_for(purpose, content)`（后端单一语义源 + 前端镜像，
双侧单测锁定）：purpose ∈ {screen_16_9, screen_4_3, a4_portrait,
a4_landscape}（画布未知 → screen_16:9，§0.5）；内容要素 = 投影信息 /
数据来源 / 专题层 / 统计面板 / 区位语境。规则：

- 全用途基线：title / scale_bar / north_arrow / attribution；
- print 追加：legend（专题层）、graticule（投影）、inset_map（区位）；
- 数据来源未知 → attribution 占位 `数据来源：—（待补充）` + advisory
  （禁止省略署名）。

**诚实渲染边界**：仅 chrome 族（scale_bar/north_arrow/attribution 占位）
可自动注入 —— 数据承载件（title/legend/graticule/inset）无数据可填时注入
即伪造，只进 advisory 决策。`__autofill_*` id 与 `__fallback_*` 安全网
并存：fallback 命中即补全规则的失败信号（decisions 计数）。显式
enabled:false 的类型永不注入（『不要指南针』语义保持）。

### 4. 组件能力升级（P3–P5、P7）

- **P3 数字比例尺**：`numericScaleAt(zoom, lat)` —— 1:N 按 96dpi 像素物理
  尺寸换算；纬度（cos φ）修正在 metersPerPixelAt 内（当地真实尺度，高纬度
  不再谎报）。与图形条并存（默认 both，options.scaleDisplay 可配 bar/numeric）。
  赤道/中纬/高纬三档对照闭式理论误差 ≤5%（测试锁定）。
- **P4 图廓注记 + 偏角**：`graticule-labels` —— 注记格式随跨度自适应
  （≥10° 整度 / [1°,10°) 度分 / <1° 度分秒），四角经纬度标注；
  `magnetic-declination` —— bbox 中心偶极子近似（DGRF2020 地磁极），恒标
  `approximate`，指北针旁注记，`showDeclination:false` 可关。
- **P5 经纬网密度**：`selectGraticuleInterval` —— 双维联合约束（经/纬向
  线数均落 [3,10]），explicit（options.interval）> adaptive > zoom 表回退；
  全球级跨度取最粗档如实出超（不虚构）。
- **P7 图例统一**：`legend-labels` 消费 legend_spec v2 增量（`unit` /
  `nodata_label` / `out_of_range_label` / `method` / `k`，局部窄类型，
  不改共享 types.ts）；图例卡（graduated/categorical）与色条（continuous/
  divergent）统一单位尾注、nodata 色块标签、out_of_range 虚线条目、
  method 披露、k 类目数；v1 payload 干净回退。

### 5. inset_map 状态收口（P6）

P0 勘察确认 inset_map 已全链 native（live inset-map.tsx 静态 SVG 投影 +
export drawChromeInset 同链；registry runtime_status="native"）。本线钉住
真值：runtime_status / 支持矩阵 / ComponentType 注册 / required_context
防空选四项回归锁定（`test_inset_map_native_ac07.py`）；渲染器静态源扫描
断言不 mount 第二个 maplibre runtime、不读主图数据源（source 隔离）；
修正 gis_harness/components.py 的 planned 注释漂移。

## Consequences

- 正面：版面从「摆上去」到「排出来」且每一步可审计（decisions 工件进
  descriptor，09 线评审 / 10 线回归同源）；P0 语料三类版面检查归零；
  chrome 缺项从被动兜底升级为主动补全 + 失败信号计数；数字比例尺 / 偏角 /
  密度 / 图廓注记 / 图例单位与缺失值表达齐备。
- 代价/限制：autofill 注入 attribution 占位使 live 缺署名地图出现
  「数据来源：—（待补充）」（有意行为变化，诚实署名原则）；密度自适应在
  全球级跨度出超 [3,10]（如实披露）；磁偏角为偶极子近似（数度级偏差，
  恒标 approximate）；V4 链尾隐藏仅对 optional 组件生效。
- 兼容：V2/V3 求解器 API 与行为零改动（559 项既有回归锁定）；quality_loop
  自动通道零改动（LAYOUT_COLLISION 保持 warning、断环走 semantic-risk
  通道）；`__fallback_*` 路径保留为安全网；前端既有 103 项 chrome/组件
  测试零改动通过；无 DB migration。
- 协调点：版面描述中间层由本线与 08 线共用 —— 结构定义见
  `frontend/lib/layout/composition-descriptor.ts`（PR 置顶）；legend_spec
  v2 字段由 03 线冻结，本线只读消费（局部窄类型，03 合入后由类型通道接管）。
