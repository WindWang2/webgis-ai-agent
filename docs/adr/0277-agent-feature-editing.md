# ADR 0277 — Agent 驱动的持久数据要素编辑（预览/确认/撤销闭环）

- 状态：Proposed（/goal 03）
- 日期：2026-10-29
- 关联：ADR-0227（Interaction IR V1）、ADR-0257（mutation 信封）、ADR-0180（锁绑定透传）、V5-C（ref_lifecycle 失效权威）、V5-E（content_revision）

## 背景与动机

master 上「数据集要素级写入」为 absent：摄入产物是会话 ref（`{"geojson":FC}` 信封），
读取面（整包/分页/单要素/MVT）齐全，但没有任何"修改第 N 条要素的属性/几何"的生产
路径；要素也没有稳定 ID（单要素查询靠源 id 键碰运气）。用户目标体验——框选三所学校、
让 Agent 改类型并校正到道路旁、可预览可撤销、地图与数据版本同步——需要一整条新的
受信写通道，而草图（transient）、MapSpec mutation（显隐/样式）、Interaction IR
（遥测）都明确不是这条通道。

## 决策

### D1 写路径 = 会话 ref 原位 overwrite + PatchSet journal

- 编辑对象限定：`ref:` 可解析、geojson 信封、≤20000 要素；fabric 外部源/raster/
  MapSpec inline-only 层一律 typed 拒绝（`dataset_not_editable`，不泄跨租户存在性）。
- 提交 = 会话锁（或 turn 锁透传）内 read → CAS（expected content_revision + 载荷指纹）
  → 重新求值 diff（不信任预览缓存）→ `session_data_manager.overwrite`（自动 bump
  revision/重算 descriptor/走 ref_lifecycle 唯一失效权威）→ journal 入账 → 锁外
  MapSpec 盖章 + 广播。
- 原始上传文件（`data/uploads/*/original.geojson`）绝不触碰；"可逆"由 journal 的
  before/after 逆 delta 承载（有界 128 条 + 64KB/数据集，FIFO 丢弃计数披露），
  非全量快照。
- 否决 new-ref-per-edit：地图层引用漂移与验收「地图与业务数据版本同步」冲突；
  修复管线的 new-ref 语义属数据集级修复，不是交互编辑。

### D2 稳定身份 = 首次编辑时确定性物化 `_wfid`

`_wfid = "wf1-" + sha256(f"{载荷指纹}:{下标}")[:16]`，仅在实际发起编辑时写入
properties（诚实可见字段），journal 记 materialize 事实；物化前目标解析走前后端
同源 FEATURE_ID_KEYS 链。链完整性指纹定义在**物化后空间**（物化是一次性数据集身份
升级，不属于可撤销的编辑效果——undo 恢复数据编辑而非去物化）。

### D3 信任边界 = preview/commit 两相 + 三通道

- Preview（纯读，TTL 10min，绑 session+ref+revision+指纹）产出有界 diff（属性
  before/after；几何只给顶点数/bbox——不搬全量）。
- 用户卡通道（`EditProposalCard` → `POST /feature-edits/plans/{id}/decision`，
  owner 鉴权）：**坐标类变更（move/snap）与一切非 safe 计划的唯一合法通道**。
- Agent 工具通道（`webgis_feature_edit_commit`）：仅当 plan.safe（纯属性、零
  findings、≤50 要素）**且** owner 已设策略 `allow_safe_attribute`；否则 typed
  `CONFIRMATION_REQUIRED`。策略存储在 map_state，仅 owner 可写，模型不可触。
- undo/redo：REST（owner）与工具（journal head 语义）都走同一 CAS 写路径。

### D4 锁纪律（与仓内既有模式一致，不发明第二种锁）

chat turn 全程持有非重入会话锁（RUN-03）→ 工具路径经 harness_kernel ContextVar
绑定检测父已持锁即**透传**（session_plan/artifact_registry 同款），同波并发再以
进程内 per-session 编辑锁串行；REST 路径正常取分布式会话锁。MapSpec 盖章
（新窄 intent `RefreshDataRevisionIntent`，origin=system）**绝不在编辑锁内调用**
（引擎自取同把锁会自死锁）——turn 内转后台任务等锁执行，失败有界重试后披露
`stamp_pending`（数据已正确，戳由下一笔 mutation 收敛）。

### D5 词表（最小、幂等、fail-closed）

属性：`set_fields`（类型/枚举校验——distinct ≤24 短字符串视为 categorial，词表外
拒绝并回传 allowed）、`rename_field`（全库）。几何：`repair_geometry`
（make_valid/close_ring/dedupe_vertices，data_quality 算子 per-feature 化）、
`move_by`（dx_m/dy_m 米制必填，测地平移，≤10km 防呆）、`snap_to_ref`（ref_id +
tolerance_m ≤500m；容差内无候选整体 fail-closed，不允许部分吸附的静默成功）。
位移用 pyproj Geod 逐顶点测地变换（非度近似）。snap 字段名用 `ref_id` 以搭
registry 参数解引用的既有 skip 名单（嵌套 dict 层级生效——transparent resolution
会把 ref: 字符串内联成整个 FeatureCollection）。

### D6 观察面与地图联动

- IR `feature_selected` payload **additive** 扩展 `selected_ids`（多选 ≤8；manifest
  双闸 + 前端镜像同步）——框选/多选对 Agent 可见；13-kind 词表不动。
- situation `InteractionContext` **additive** 新增 `feature_edits` fact（journal
  最近 ≤5 条回执投影：kind/actor/变更要素数/数据版本）——Agent 下一 turn 可观察
  「数据真的改了」。schema dump 由 scripts/situation_inspect.py --dump-schema 再生。
- 地图刷新：盖章 intent 把 ref 的新 content_revision 写到 MapSpec 源 → SSE mapspec
  镜像（revision 门）→ 前端 data-plane 以 content_revision 缓存键重拉受影响 ref
  （单飞/ETag；MVT 走 ref_lifecycle epoch 失效）——只 setData 不 restyle，
  user-wins 显隐/样式零影响。另发 `dataset_updated` WS 广播（消费面可选）。

## 后果

- 正面：要素级编辑成为有审计、有 CAS、可撤销、租户隔离的一等能力；Agent 与用户
  的信任边界显式化（safe 属性面可自动化，坐标变更恒需确认）。
- 代价：journal 有界（超界丢最旧，undo 链头部完整性仍在）；编辑数据集规模上限
  20k 要素；MapSpec inline-only 层不可编辑（诚实拒绝并在卡片/工具信息中说明）。
- 风险与缓解：turn 内盖章延迟（后台任务 + stamp_pending 披露 + 下一笔 mutation
  收敛）；枚举推断误判（仅 distinct ≤24 全短字符串字段；finding 回传词表供改口）。

## 测试证据

tests/unit/feature_edit/（core 27 + routes 12 + tools 12 + concurrency 3 + e2e 1）+
IR/situation/contract 邻域全绿；E2E journey 覆盖验收场景全链（真实摄入管线→IR 多选
→工具预览→CONFIRMATION_REQUIRED→owner approve→隔离不变量→盖章→situation 观察→
undo→历史→幂等重放）。前端 tsc/eslint/vitest（卡片状态机/adapters/reducer/api）绿。
