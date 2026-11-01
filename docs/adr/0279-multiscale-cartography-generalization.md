# ADR-0279：多尺度制图综合与尺度表达档案（GOAL 04）

状态：Proposed（随 zcode/goal-multiscale-cartography-v1 PR）
日期：2026-11-02 · 基线 master 6c5e71c2c

## 背景

MapSpec v1.6 已有语义缩放表达协议（`representation.py` 窗口计划 +
前端 minzoom/maxzoom 门编译），但存在三个断链：

1. `generalized` / `aggregate_grid` 是 artifact-backed capability seam
   ——「声明即信」，无工件生产/校验/消费链（converter 从不传
   `artifact_modes`）；
2. 几何综合只有 MVT 瓦片内 Douglas-Peucker（半像素、逐瓦片），无
   整图级语义取舍/拓扑安全综合；`boundary_detail` 四档全仓无消费者；
3. 语义重要性（道路等级/行政级别）→ 要素取舍无规则层：缺字段时
   无法判定，有字段时无词表证据链，易伪造「主干道路」。

另有三个跨面事实约束本设计：filter 缺陷族（open #1809/#1810/#1811：
headless 编译器/SVG 导出不消费 `layer.filter`）；比例尺换算双源分裂
（后端 `scale_rules` 256px 瓦片基准 vs 前端 `meters-per-pixel.ts` 512px
MapLibre worldSize，同 zoom 差 2 倍）；统计口径（分类 breaks/要素计数）
必须锚定原始数据而非渲染副本。

## 决策

### D1 扩展 v1.6 协议，不建第二表达协议
新包 `app/lib/cartography/multiscale/`（profile/importance/generalize/
plan/export_adjudication/semantic_checks_scale_profile）只补 v1.6 的
capability seam：`generalized` 窗从「声明即信」升级为「真实工件 + 窗口
源覆盖」。`representation.py` 既有函数签名与行为零改动（只读复用）。

### D2 非破坏性 = 新 ref + 窗口源覆盖（MapSpec v1.9 additive（v1.8 由 GOAL 03 / PR #1877 时态层占用 —— 版本号协调，本 PR 登记 1.8 为占位、1.9 为本语义））
- `MapSpecRepresentationWindow.source`（additive）：artifact 窗携带时
  该窗渲染副本/导出裁决从指定 source 取数；raw 窗恒无（主层语义）。
- `MapSpecLayer.scale_profile`（additive 开放 dict）：ScaleRepresentation-
  Profile.v1 投影（权威形状在 multiscale/profile.py）。
- 派生工件存**新 session ref**（`gen:` 前缀），主源条目与原始 ref
  逐字节不变（测试以快照锁定）；分析/统计通道继续读原始 ref。
- 版本注册 1.7→1.8 纯 additive；ts_projection/前端版本词表/contract
  manifest 三向同步（漂移闸测试既有）。

### D3 语义重要性 fail-honest（不伪造主干）
规则引擎证据三源：descriptor `field_schema`（类型）× 聚合通道真实值
分布（基数 ≤32）× 有序词表（道路等级中英双语别名 / 行政级别数值
0-5）。**值域全匹配**才产生排序；裸数值等级字段（1 高还是 5 高无
证据）与词表外值一律拒绝（`no_trusted_importance_field` → 不取舍，
保留原始几何/聚合表达）。用户 override 赢（字段真实存在且序值与数据
交集非空）。多候选按同义词特异性 → 覆盖面 → 字典序确定性裁决，
全部候选留痕（可解释）。

### D4 拓扑安全分级综合
- `bounded_simplify`：局部等距投影（数据集原点 equirectangular，回执
  披露近似）→ `simplify(preserve_topology=True)` → 逐要素不变量复核
  （有效性/类型族/部件数/洞数/面积误差界/线长损失界）——任一违反
  **该要素回退原几何**并记因，绝不输出坏几何；
- `shared_boundary_protect`：坐标频次 ≥2 的共享锚点必须在简化结果
  存活；不存活逐次减半容差重试（≤4 次）仍败则回退原几何 —— 行政
  边界共享角点不裂缝的确定性保证（集合级复核进回执）；
- `safe_no_generalization`（用户 pin）：几何零改动；
- 微环丢弃仅在 `min_ring_area_m2` 显式声明时发生并计数。

### D5 绕开 filter 缺陷族
多尺度要素取舍一律在**工件生成期物化**（派生 FC 已含选择结果），
不改运行时 `layer.filter`；窗口切换复用 MapLibre 原生 minzoom/maxzoom
门（v1.6 既有机制）。#1809/#1810/#1811 的修复属既有跟踪 issue，本
方向不认领也不复制其风险。

### D6 显示/计算尺度显式分口径
档案每带同时携带 `display_*`（512px MapLibre 基准 1:N，与前端
meters-per-pixel.ts 同式）与 `computation_*`（256px 瓦片基准 m/px，
与 scale_rules 同源），快照在模型构造期统一推导；分类/统计恒在
`original_source` 上计算（`classification_basis` + legend breaks 指纹
入档案），渲染副本/像素分辨率不得改变分析结果。

### D7 统计口径披露
每带 `stats`（original/displayed/suppressed/geometry_modified +
degrade_reasons）结构化入档案；`carto.scale.profile` 语义检查核
（结构/悬空工件窗/分类口径/取舍证据对偶）接入 `evaluate_cartography_
semantics`；导出诊断码 `layer_generalized_artifact`（双端发射，死码门
登记）披露工件源消费。

### D8 Agent 闭环复用既有事务
三工具（`webgis_inspect_map_scale_profile` / `webgis_preview_scale_
representation` / `webgis_apply_scale_representation`）经 ToolRegistry；
apply 的 commit = 工件存新 ref → UpsertSource×N + UpsertLayer×1 顺序
CAS 链（`apply_gis_mutation`，前一步 `mutation_revision` 为链基线）→
回执环幂等（`_scale_profile_receipts`）。不建第二事务/第二 MapSpec
写入面。

## 后果

- 正面：generalized 窗获得真实生产/消费闭环；线/面语义取舍有证据链；
  50k/200k 实测（13.4s/56.1s，numpy 向量化投影 + 单遍多带 + 线亚像素
  DP 快路径）；
- 代价：窗口克隆子层可能引用工件源（前端三处消费点同步适配，
  parity 测试锁定）；档案声明是另一份带级元数据（由语义检查核与
  导出裁决一致性约束）；
- 已知边界：`aggregate_grid` 工件（格网聚合）本期未实现（词表保留，
  capability seam 语义不变）；多点共线行政边界的整弧共享保护是
  锚点级（顶点存活）而非弧级拓扑重建（弧级需 topology 框架，超本
  期范围，披露于档案 topology_risk）。
