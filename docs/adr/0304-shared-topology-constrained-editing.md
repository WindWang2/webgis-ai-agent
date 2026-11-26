# ADR 0304 — 共享拓扑约束编辑（跨要素共边/线网顶点级编辑）

- 状态：Proposed（goal-gis-topology-authoring-20261125）
- 日期：2026-11-26
- 关联：ADR-0277（agent feature editing）、G31 跨层拓扑工作台、G44 字段契约、G32 持久提案

## 背景与动机

G14 提供了单要素/结构编辑的受信写通道（preview/commit/CAS/journal），但编辑
始终以「目标要素」为中心：改一个面要素的几何，共享这条边的邻面毫不知情 ——
相邻行政/地块面的边界在一次顶点移动后出现裂缝或重叠。生产矢量维护（宗地、
行政界、道路网）的语义单位是**共享空间结构**（共享边、交汇节点、线网端点），
不是单要素。本 ADR 把既有编辑词表向跨要素约束编辑扩展：顶点/节点变更按共享
结构同步传播、约束违规在预览期以「基线/新增」分离呈现、提交走既有单一写路径。

## 决策

### D1 匹配规则用户可选（相同坐标 ≠ 同一业务节点）

- `exact`（默认）：原始 (x, y) 完全相等才是同一节点 —— 诚实默认，不引入隐式
  容差合并。
- `tolerance`：显式 tolerance_m ≤ 100m（比 snap 的 500m 更严 —— 节点合并是
  大范围传播动作）；容差内命中多个不同节点 → typed `shared_node_ambiguous`
  （候选披露，绝不静默就近）。容差合并是 union-find 组装（层级兼容前提下），
  比较预算 2e6 次，超限 `index_too_large`。
- `level_field` 守卫：声明字段值不同（桥/隧/匝道层级）的要素顶点永不合并；
  缺值与有值亦不合并。连接类 op（connect_endpoint）在两端层级值不同时
  typed `connect_level_conflict` —— 不误连立交。
- 容差匹配不跨反经线（分桶按原始经度）；跨反经线数据集须 exact 模式。

### D2 有界可重建索引（不是第二数据库）

`shared_topology.build_shared_index` 对已物化 FC 纯读构建节点/边索引：
- 顶点预算 MAX_SHARED_INDEX_NODES = 500k（超限 typed 拒绝，不做有损采样）；
- Polygon/MultiPolygon（含洞环）、LineString/MultiLineString、Point；Z 路径
  move/connect 保留 Z，insert/delete/reshape 对 Z 路径 typed 拒绝；M（4 元素）
  不支持；
- 环按逻辑顶点登记（闭合首尾记 0 位），op 应用后同步闭合重复点；
- 每次 derive 惰性构建、结构性 delta 后失效重建 —— 不持久化、不跨请求缓存。

### D3 五个共享 op（封闭词表 additive；坐标类恒不 safe）

`move_shared_vertex` / `insert_shared_vertex` / `delete_shared_vertex` /
`reshape_shared_edge` / `connect_endpoint`：

- 一次共享 op 触及要素 ≤ MAX_SHARED_TOUCHED_FEATURES = 10（与
  MAX_GEOMETRY_TARGETS 同量级，journal undo 保真预算不变）；
- 每个被触及要素一条 update delta（geometry_before/after 全量内嵌）→
  undo/redo 由既有 journal 逆 delta 承载，**不新增执行路径**；
- 共享边插入点两侧逐字节一致（投影坐标只算一次）；reshape 按各要素自身
  方向（正/反）同步插入新路径；delete 后环自动再闭合；
- 操作后几何经 `_validate_after` 门（与 move_by 同款）：空/非法面 →
  `geometry_invalid_after`，绝不静默 make_valid。

### D4 基线/新增违规分离（任务书 WP2 验收语义）

共享 op 的 plan 在声明拓扑规则上做**双面检查**：pre（基线）与 post（编辑后）
同一 affected 范围（全部 delta 身份 + 目标）。post 中身份键不在基线里的违规
= 新增（阻断，`topology_violation`）；延续基线的 = 存量（`topology_context`
披露，不阻断）。身份键 = (rule, wfid, other/duplicate_of)；无身份键的
否定性信号（如预算截断）恒按新增保守处理 —— 绝不因基线存在而豁免。
非共享 op 保持既有单面检查语义（任何邻域违规即阻断）。

### D5 typed impact plan（只扩展既有 plan，不建第二真相）

plan additive 字段：
- `impact`：selected / touched_features / affected_neighbors（成员关系判定：
  shares_edge / incident_node，≤64 条 + truncated 披露）/ measure_delta
  （逐要素测地面积/长度 before/after，pyproj Geod）/ atomicity（dataset 域
  揭示：跨数据集原子提交不支持且从不尝试）；
- `topology_context`：new/existing 计数 + 存量违规投影（≤24）；
- `summarize_plan` 投影 impact_summary / topology_context（Pi/卡片有界可见）。

### D6 原子性与信任边界不变

- 提交仍走 `executor.commit_plan` 唯一写路径（CAS + journal + 合同冷复核 +
  MapSpec 盖章）；plan `safe` 分类自动排除一切坐标类 op —— 共享编辑恒需
  用户确认（提案卡/owner REST），Pi 只能提出；
- 跨数据集：本词表操作作用域是单 ref FC；impact.atomicity 显式揭示
  `cross_dataset: blocked` —— 不做 best-effort 半提交；
- D1–D5 全部边界常量入 `contracts.py` 单一登记点并被测试钉住。

### D7 顺带修复的两个既有缺陷（域内基线修复，随本 ADR 提交）

1. **journal 逆 delta 顺序**（P1）：`invert_deltas` 原正序输出 —— 同要素多
   几何 op 的 plan undo 停在中间态而非原态（derive 的 deltas 是顺序累积态，
   op1.after 即 op2.before）。修复为逆序回放；单 delta/跨要素场景顺序无感，
   created 原位重插按 index 升序（apply 内排序），removed 移除与序无关。
   用 replace_geometry+move_by 同要素双 op 复现（undo 结果 ≠ 原字节）后修复。
2. **pyproj ≥3.8 数组广播**：`move_by` 的 Geod.fwd 对数组 lon/lat + 标量
   az/dist 在 pyproj 3.8.0 抛 "Array lengths are not the same"（面/线要素
   位移全挂；既有测试只覆盖点要素所以绿）。修复为显式全数组调用，标量路径
   不变。

## 后果

- 正面：共边/线网编辑成为预览可审、原子可撤销的一等能力；邻面同步由服务端
  权威计算（UI 不做 shadow copy 真相）；Agent 与用户同一 plan→确认→CAS→复检链。
- 代价：容差模式在密集区可能歧义频发（fail-closed 提示改 exact）；Z 几何的
  insert/delete/reshape 不可用（typed 拒绝）；共享编辑触及面 ≤10。
- 风险与缓解：容差合并的病态聚类（比较预算 + 簇成员上限诚实拒绝）；闭合重复
  点同步（环逻辑顶点登记 + 应用后再闭合，测试钉住）；undo 保真（逐要素全量
  before/after，256KB entry 闸不变）。

## 测试证据

tests/unit/feature_edit/test_shared_topology.py（42 例）：索引（共享边/三面
交汇/洞环/悬挂端点/预算/容差歧义/层级拆分）、五 op（双侧一致/投影/再闭合/
方向/rejected 面）、Z 语义、plan 集成（impact/基线分离/自然 id/journal undo
往返）、split_topology_findings 身份分离。既有 feature_edit（123）+
topology_workbench + feature_proposals 套件全绿；ruff 干净。
