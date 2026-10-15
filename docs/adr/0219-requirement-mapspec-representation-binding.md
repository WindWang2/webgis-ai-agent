# ADR-0219: Requirement ↔ MapSpec Representation Binding（混合主动制图一致性）

- 状态：Accepted
- 日期：2026-10-16
- 方向：Requirement ↔ MapSpec 混合主动制图一致性（User-Wins Representation Binding）
- 基线：master `fd1acd7c`
- 前置：ADR-0215（Requirement IR）、ADR-0183（Mutation Envelope/Transactions）、W15（MapSpec 状态三分类与锁分区）、#1070（durable presentation owner）

## 背景与问题

ADR-0215 建立了 RequirementDocument（"用户要什么"）并明确留下后续项：
*"representation 面（用户隐藏图层/锁定图层/palette）目前只入 IR 与
digest，其 MapSpec mutation 接线属后续方向"*。侦察确认该接线为零——
两侧系统各自成熟但互不连通（recon 2026-10-16：`apply_user_patches` 与
`grammar_request_face` 均无生产消费方）。后果：

1. 用户在地图上直接做的制图选择（隐藏某层/换色带/锁图层/关组件）不进
   Requirement——下一轮 Agent 重新规划时只有 MapSpec 内的印记/锁可依赖，
   "为什么要这样画"无跨轮语义记录；
2. Requirement 中经用户确认的表达要求（palette/表达 pin）没有落地通道——
   生产上 `GrammarRequest.pinned_palette` 的唯一来源是工具参数（agent
   转述），结构化用户真相缺席；
3. 反向地，若在 planner/renderer 里各自"顺手"同步两份状态，就会形成
   双写与第二真相源——这正是本 ADR 要避免的。

另发现一处 master 缺陷：`app/services/gis_harness/tools.py` 的
`ensure_document` 调用漏传 store/session_id/query 三参形状（TypeError
被 fail-open except 吞掉），F02 的生产接线实际是死代码。本方向以修复
该缝为地基。

## 决策

实现**语义绑定（semantic binding），不是双写**：

```
User/Agent requirement decision
   → RepresentationBinding proposal（纯函数构造）
   → 现有 MapSpec Mutation transaction（apply_gis_mutation）
   → mutation receipt → binding 终态（applied/refused/superseded/…）

User direct UI MapSpec Mutation
   → 服务端已提交 receipt（门面 post-commit 平面）
   → semantic classifier（taxonomy）
   → RequirementDocument patch（actor=user，apply_user_patches）
```

MapSpec 仍是图面 desired truth；RequirementDocument 只保留"用户要什么/
哪些选择要跨轮保持"。落点 `app/services/gis_harness/representation_binding/`：

### C1 taxonomy（`taxonomy.py`）

对 registry 全部 23 个 intent kind 建立确定性分类：
`requirement_significant`（显隐意图/图层锁/组件开关·样式·位置/
palette/表达/标题内容）、`presentation_ephemeral`（pan/zoom 及
opacity-only——永不进 requirement）、`system_repair`（repair actor/
producer 档——绝不冒充用户偏好）、`data_science`（source/layer/time/
basemap）、`unbound`（bootstrap/快照等组织面）。一致性 gate
（`assert_taxonomy_covers_registry`）：新增 intent 未登记分类 → raise
（fail-closed，不静默漏分）；未知 kind → unbound + reason code。
undo 重定基（`undo_rebaseline`）仅对 user 的 Rollback/RestoreStyle 生效。

### C2 binding contract（`contracts.py`）

`RepresentationBinding`（schema `representation_binding.v1`，
extra=forbid）：source requirement path / target intent kind（**只允许
现有 registry kind 或既有 grammar face**，不新造命令词表）/ actor /
provenance / lock semantics / expected MapSpec revision（CAS）/
requirement revision / reason / reversible / no-reanalysis /
binding fingerprint（规范化输入 sha256 前 16 hex，确定性）。终态枚举
proposed→applied|refused|superseded|ineligible|skipped。
**binding 是动词不是表**：无持久 ledger——幂等由引擎 `mutation_id
= rb:<fingerprint>` dedup 承载，跨轮感知由 C6 摘要实时计算，不引入
新 state store。

### C3 requirement → MapSpec（`requirement_to_mapspec.py`）

- `build_binding_proposals(doc, mapspec, expected_revision)`（纯函数）：
  只读**字段级 origin=user** 的表达面（"显式且已接受"以 ADR-0215 的
  字段级 ownership 判定——user 即最高优先；文档 superseded 不绑定）。
  hidden→`PatchLayerPresentationIntent`（族扩展：roads 覆盖 roads-1）、
  locked→`PatchWorkbenchDeltaIntent(locksAdd)`（绝对值语义幂等）、
  组件显式禁用→`PatchComponentIntent`（显式要求配备 True 由
  completeness 权威承接，不重复施工）；**palette/geometry_kind/
  channels 走既有 grammar face**（`grammar_request_face` →
  GrammarRequest pin → resolve_symbology 唯一裁决），不构造 mutation、
  不强写 paint。
- `apply_binding`：经 `apply_gis_mutation(origin="agent",
  actor="requirement_binding", expected_revision=CAS,
  mutation_id=rb:<fingerprint>)` 正式事务；mutation 被拒 → binding
  绝不假称 applied（refused + error_code）；CAS 过期 → superseded +
  有界重试一次；user-wins 守卫拒绝携带稳定 `error_code=
  user_wins_presentation`（门面 `_user_wins_result` additive 键），
  绝不靠 error_msg 子串识别。**批内 CAS 链**：一轮内多条绑定逐条
  落地，每条以上一条成功回执的 `mutation_revision` 为基线（避免
  同批自陷 stale——review P1#1）。
- eligibility：geometry_kind 经 model_library（含 IR 词表↔注册 id 的
  **确定性桥表**：heatmap→visual_heatmap 等；歧义项不桥——不猜）；
  未注册 → ineligible + 披露，交用户改选。数据/表达适配在求解期由
  grammar 的 rejected/disclosure 承接。
- no-reanalysis 由构造面成立：全部 mutation 目标都是 presentation 类
  intent，不触发数据获取与科学计算。
- 生产接线：`webgis_map_intent` result 的 omittable
  `representation_bindings` 键（fail-open）。
- sync 工具消费面：进程内 **pin 快照**（turn 开始的
  `apply_pending_bindings` 发布，FIFO ≤64；`create_thematic_map` 等
  sync 工具纯 dict 读）——快照是 best-effort 派生缓存而非第二真相源，
  缺席即回退工具参数既有行为（快照只在同进程 turn 开始时刷新——
  不含 webgis_map_intent 的 turn 使用上一轮快照，是披露的边界）。
  cartography 工具把 pin 合并进 resolve_symbology 输入——**两个
  method 路径**（agent 显式传 method 与缺省裁决）都合并（review
  P1#4：合并位于分支外，palette 变量统一被 pin 覆盖），冲突以
  `requirement_palette_pin_applied` check 披露。

### C4 UI mutation → Requirement（`semantic_projection.py`）

- 挂点：`gis_world_state/mutation.py` 门面 post-commit 通知平面
  （provenance/collab 事件同段；单笔 + batch），fail-open，
  kill-switch `GIS_REQUIREMENT_PROJECTION=0`。
- **只信服务端已提交 receipt**（`MapSpecResult.mapspec` = 提交后权威
  spec）；前端 optimistic state 永不进入；`duplicate` 重放跳过
  （首次提交已投影）。
- 门控：`origin=="user"`（服务端权威归因）+ taxonomy
  requirement_significant/undo_rebaseline；agent/system/repair/ephemeral/
  未知 kind 一律不投影。
- 写面：`apply_user_patches(actor="user")`，op_id 从 mutation_id + 语义
  路径确定性派生（幂等）；hidden/locked 写**绝对值集**（从提交后
  spec 派生：`presentation_owner=user ∧ expected_visible=False` 的层族
  基 id；workbench 锁集）→ 并发/乱序自愈收敛。user RemoveLayer 同样
  写绝对值集（删除后 spec 天然清除 ghost 成员、保留存活家族成员的
  用户语义——review P1#2）。**并发写入门**：requirement service 的
  `ensure_document`/`apply_user_patches` 读写全程持有 per-session 进程内
  asyncio 门（loop 感知）——turn 路径与 UI 投影路径在进程内互斥，
  消除 load→modify→save 交错的静默丢更新（review P1#3）；跨进程仍是
  既有 fail-open 披露边界。duplicate 重放不跳过投影（首次投影失败时
  幂等补偿——review P2#6）；batch 聚合为单次 load→apply→save。
- undo/redo（user Rollback/RestoreStyle）：**重定基**——两个语义集按
  提交后 spec 重算，撤销不留幽灵锁。
- 有意不投影的面（已知边界，见下）。

### C5 conflict policy（`conflicts.py`）

20 个稳定 reason code 的裁决矩阵（`resolve_conflict` 纯函数）：每个
code 恰一裁决（winner/action/disclosure）；未知 code fail-closed →
refuse。禁止 silent last-write-wins：所有拒绝/跳过/回退携带 code。
CAS 过期是并发事实（retry_with_fresh_revision）而非冲突。

### C6 agent context（`agent_context.py` + provider）

`representation_binding_summary`（有界：用户 pin palette/表达、
hidden/locked ≤8、disabled components、最近语义编辑 revision、
requirement↔mapspec 漂移信号 ≤4）经新 provider
`RepresentationBindingProvider` 注入 typed context assembly（新 domain
`REPRESENTATION_BINDING`，rank 插在 control-plane 前、wrap fence、
char cap 1200、kill-switch `GIS_REQUIREMENT_CONTEXT_BLOCK=0`）。不重复
注入完整 MapSpec/Requirement 文档。

## 不做什么（边界）

- 不建双向实时字段镜像；不把 MapSpec 字段复制进 Requirement；
- 不让 Requirement service 直写 MapSpec store（C3 只经
  apply_gis_mutation 事务）；
- 不重写 mutation transaction / 锁 / CAS（全部复用）；
- 不拦截用户相机手势（SetView 等 transient 永不进 requirement）；
- 不重做 #1600/#1589 的并发 bug；
- **投影已知边界**：逐层 paint/布局坐标/ReorderLayers 不进
  requirement（MapSpec 是这些面的权威，requirement 不镜像）；UI 上
  的样式面板改色不反推 palette 名（paint→palette 反查不确定，palette
  pin 只来自用户话语/澄清）；`ws_legacy` 直写通道（ADR-0183 D4 已披露
  的债）无 CAS 回执，不投影；basemap/scene/scenario 本期不绑定；
  component 的 user-wins 硬守卫不在引擎呈现守卫面——agent 可临时翻转
  用户禁用的组件，下一轮 C3 绑定回放收敛回用户语义（窗口期不一致
  已知并披露；引擎级 AUTO_SAFE legend 抑制仍在）。

## 后果

- 正面：用户直接制图选择跨轮保持（hidden/locked/components 进
  requirement + 既有 durable 印记/锁双保险）；requirement 表达要求经
  正式事务与 grammar face 落地；actor/provenance/CAS/user-wins 全链
  可测；style-only 绑定零数据重算。
- 代价：每次成功 user mutation 增加一次 fail-open 的 requirement
  读改写（绝对值集，有界）；typed context 增加一个 prompt 块（≤1200
  chars，kill-switch 可关）；taxonomy 需随 registry 演进显式维护
  （gate 强制）。
- 退役路径：三个接线面（binding/projection/context block）各有独立
  kill-switch，可分别回退到接线前行为；`RepresentationBinding` 是
  值对象无持久化，删除包即完全退役。

## 测试

- 单元：`tests/unit/gis_harness/representation_binding/`（taxonomy
  表/gate、契约有界/确定性/往返、冲突矩阵全表、提案构造（纯函数）、
  投影门控/幂等/重定基、context 摘要/provider、pin 快照/sync helper、
  review 修复回归：并发写入门/显式 method pin）。
- 端到端（真引擎 + 真事务）：`tests/cartography/
  test_representation_binding_scenarios.py`（C7 场景：roads 不复活/
  palette 保留/legend 禁用/锁-解锁/pan-zoom 零污染/过期 CAS 不投影/
  undo 无幽灵锁/双 tab CAS/duplicate 幂等 + 多绑定单轮全落地）。

## 已知边界（独立 review 后修订）

- 逐层 paint/布局坐标/ReorderLayers 不投影（MapSpec 权威；skip code
  `projection_not_mirrored`）；
- UI 样式面板改色不反推 palette 名（反查不确定；pin 只来自用户话语/
  澄清）；
- `ws_legacy` 直写通道无 CAS 回执，不投影；
- pin 快照只在 turn 开始刷新（跨 turn 陈旧面披露）；
- `grammar_face_pins`（归一化+桥表）与 IR `grammar_request_face`（raw
  值）是两个 face 读法——前者服务求解合并，后者仍是 IR 六路投影之一，
  尚未收敛为单实现（defer，漂移风险由测试钉住桥表）；
- UI 投影不带 turn_id（route 无 turn 语境）→ `updated_turn` 记 0；
  user 投影会把 accepted 文档降回 draft（"用户修订重新打开"语义）；
- component 的 user-wins 硬守卫不在引擎呈现守卫面：agent 可临时翻转
  用户禁用的组件，下一轮 C3 绑定回放收敛（窗口期不一致披露）；
- 跨进程 requirement 双写仍是 fail-open（seq fencing best-effort），
  与 ADR-0215 既有披露一致。
