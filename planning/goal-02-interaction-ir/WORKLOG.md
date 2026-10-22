# Goal 02 Interaction IR V1 — 工作日志（自动执行）

## 2026-10-18 勘察（Phase 1）

- master = `ddd10fb4`（fetch 后无前进）；9 个 open PR 全部读取（见 PR_OVERLAP_MATRIX.md）。
- Subagent A（只读勘察）完成 authority map；关键结论：
  - `gis_situation/observation.py::record_interaction` 已有摄入骨架（词表/白名单/寻重/环/锁/ack），
    但无 client_seq/event_id/replay，且 `situation_interaction` 通道**前端零生产者**（/ws 感知通道死代码）。
  - 前端已有 `map-action-acks.ts` outbox 工程范式（仅覆盖 agent 动作回执）。
  - 4 套互不兼容序号：viewport_seq / interaction ring seq / map action_id / envelope mutation_id。
  - 双端契约 manifest 模式（carto_ir）可复用。
  - ADR 水位：已合并最新 0219；0220/0222/0223 被在飞分支占用 → 本方向预计 0224（`scripts/allocate_adr.py` 分配）。
- 环境限制：`vendor/pi` 子模块因网络代理不可达无法克隆（主工作树同样未初始化）——pi-bridge 相关测试
  的本地行为记录为环境基线，与本方向改动无关的失败按 baseline 差异处理。

## 架构决策（Phase 1 定稿）

### D1 事件 ≠ 状态真相（复用边界）
- Interaction IR 只记录"发生了什么 + 与哪个 authority revision 关联"。
- 用户 layer 可见性/排序/样式 mutation 仍走 MapSpec lifecycle（user-mutation.ts → POST /mapspec/mutations）；
  IR 事件携带 `client_mutation_id` + 结局（committed/superseded/failed），服务端 ingest 时对
  `map_state["_mutation_dedup"]`（`c:<id>`）只读交叉校验 → `verified` 标志。**mutation 被拒绝不投影为成功**。
- 不新建 preference store：palette/style 归 #1614 representation binding（本方向只链接）。

### D2 持久层：复用 map_state 有界键，不建新表
- 候选：Turn Journal（turn 作用域 harness 事件，无 client 多实例语义，fail-open 诊断账本——语义不符）、
  `session_data.append_event`（deque(20) 无 seq/幂等——太弱）、新 DB 表（无限增长风险 + 无证明必要）。
- 决策：`map_state["_interaction_ir_ledger"]`（环 128 条 + 字节预算 96KB）+
  `_interaction_ir_clients`（per-instance 水印，≤8 实例 FIFO）+ `_interaction_ir_meta`（server_seq/dropped 计数）。
  生命周期（clear/delete/TTL/Redis 降级）全部免费复用 session_data。ADR 说明。
- ring 淘汰 = drop disclosure（`dropped` 计数），不假装保留全部历史。

### D3 传输：REST 批量端点，不复活死 /ws，不侵入 collab WS
- collab WS 承载 doc/mutation 同步（高价值低频）；交互事件是 fire-and-forget 遥测语义，
  与 map-action-ack 同族 → 同款 REST 批量（`POST /sessions/{sid}/interaction-events`，批 ≤50，per-IP 限速，
  require_owned_session，session 锁 fail-closed 写）。重连 replay = outbox 幂等重试（event_id + per-instance 水印）。

### D4 幂等/排序核心 = 纯函数状态机
- `LedgerState + apply_event(state, raw) -> (state', ack)` 纯函数；ingest（真实 store）与 replay（corpus）
  走**同一代码路径** → 回放确定性由构造保证。
- 分类词表：`accepted / accepted_with_gap / duplicate / stale_seq / rejected_kind / rejected_payload /
  rejected_actor / overflow_dropped`。
- 去重主键 = per-(client_instance_id) 单调水印（ack 丢失后重试 → stale_seq 幂等）；event_id in-ring 检查兜底批内重复。
- actor 面：客户端端点只接受 `actor="user"`（防伪造 agent/system 事件）。

### D5 确定性 = 序号驱动，wall-clock 仅 advisory
- projection/replay/fingerprint 一律由 (client_instance_id, client_seq, server_seq) 驱动；
  `created_at` 仅存不用于排序/投影 → 回放不依赖 wall-clock。
- fingerprint = sha256[:16] 规范化 {schema,kind,actor,instance,seq,payload,mutation 链}，排除时间戳。

### D6 高频合并
- 前端 outbox：`replace_pending` 策略 kind（viewport_settled / temporal_scrub_settled）在队列中只保留最新待发；
  服务端不存每帧 viewport（事件本身只在 settle 后产生）。
- 队列上界 256 溢出丢最旧 + 计数披露。

### D7 隐私
- feature_selected/inspected payload：layer_id + feature_id + 字段**名** allowlist（≤8，仅名字）+ geometry_digest；
  **任何 kind 的 payload 白名单都不含原始 properties**。服务端 normalize 强制（未知键丢弃）。

### D8 前端接线面（最小 hooks）
- viewport_settled：map settle 防抖；
- feature_selected/deselected/inspected：use-feature-selection commitSelection / inspect 面板；
- layer_visibility/opacity/style/order_changed + layer_removed：user-mutation.ts 五个入口（事件带结局）；
- aoi_drawn/edited/deleted：sketch 通道；temporal_scrub_settled：时间轴 settle；comparison_mode_changed：对比开关；
- measurement_created/cleared：量测动作（若接线成本高，词表保留 + 记录未接线）。

### D9 契约双端闸
- `tests/unit/gis_harness/interaction_ir/contract_manifest.json`（权威 JSON）：
  backend pytest 断言 ≡ contracts.py 常量；frontend vitest 断言 ≡ `frontend/lib/interaction/contract-manifest.ts`。

### D10 生成物清偿
- 新端点 → openapi snapshot（API_SNAPSHOT_UPDATE=1）、endpoint-scope-matrix.csv、api-docs.md、
  check_generated_staleness --update。

## Phase 2+ 记录

（实现开始后追加）

## Phase 2 实现（2026-10-18）

### 后端
- `app/services/gis_harness/interaction_ir/`：contracts（13 kind 词表 + 白名单 + 有界常量）、
  ingest（纯函数状态机 + store 壳）、projection（有界投影）、replay（同路径回放）。
- `app/schemas/interaction_event_schema.py`：InteractionEventV1 wire 契约（extra=forbid）。
- `app/api/routes/interaction_events.py`：POST 批量摄入 + GET 投影；main.py 注册。
- `app/services/gis_situation/compiler.py`：compile_interaction 优先 IR ledger（回退 legacy ring）；
  _interaction_sequence 并入 IR server_seq。
- ADR-0227（分配器给 0220 与 open PR #1615 撞号 → 手动 0224 + watermark 224，ADR 内说明）。

### 前端
- `frontend/lib/interaction/`：contract.ts / contract-manifest.ts（双闸镜像）/ outbox.ts
  （身份/seq/合并/水印/keepalive）/ bus.ts（emitter + 几何摘要）。
- 接线：map-panel（viewport settle/空点取消/弹窗关闭/量测完成）、use-feature-selection（选中）、
  user-mutation（presentation/style/remove/reorder 四类 mutation 事件，真实 mutation id 穿透）、
  sketch-editor（AOI 增删改）、workbenchSlice（比较模式）、use-workspace-session（切换冲刷 ×2）。
- remove 链路重构：removeLayerFromSpecOnce 回报真实 attemptMutationId/revision（RemoveLayerReport）。

### 词表纪律执行
- 评估后剔除：temporal_scrub_settled（无时间轴 UI）、feature_inspected（与 select 同刻）、
  measurement_cleared（无单点清理钩子）—— ADR「词表边界」记录。

### 本地 gates（全部通过）
- ruff check（改动文件）✓；check_import_boundaries ✓；git diff --check ✓
- 后端：interaction_ir 80 + situation 族 54 + contract 守卫 20 + 邻域（acks/session_data/gis_harness）
  2419 + mutation transactions 39 ✓
- 前端：tsc --noEmit ✓；vitest interaction 15 + 邻域（mapspec/store/hooks/map components）1261 ✓
- 生成物：openapi snapshot 再生 ✓；endpoint-scope-matrix ✓；api-docs ✓；staleness 指纹 ✓

### 基线记录
- vendor/pi 子模块本地不可达（网络代理）—— pi-bridge 相关测试若有失败属环境基线，非本方向引入
  （本次跑动的测试族未受影响）。

## Phase 3 独立 review + 清偿（2026-10-18）

- Reviewer（subagent，未参与实现）从 origin/master...HEAD 全量重读：0 P0 / 5 P1 / 4 P2 / 6 P3。
- 全部清偿（详见 ADR-0227「独立 review 清偿」节）：
  - P1-1 渲染断裂（_render_interactions IR 双形态）；P1-2 恢复面剥离 IR 内部键；
  - P1-3 reorder 假 committed；P1-4 agent 相机/comparison 归因污染（FE-3 判别 + origin 参数）；
  - P1-5 failed/superseded 跨会话 emit；P2-1 假 gap（新实例宽容 + 切会话重置 seq）；
  - P2-2 duplicate-tab 实例隔离（加载后缀）；P2-3 降级不带 watermarks；P2-4 读失败 fail-closed 503；
  - P3 全清（死分支/死代码/未用参数/常量断言/list 收紧/override 新鲜度文档化）。
- 修复面回归：后端 test_review_remediation.py（7）+ 前端 user-mutation.interaction-ir.test.ts（5）
  + map-panel.interaction-ir.test.tsx（3，手势判别机器验证）。
- 重跑 gates：后端 gis_harness 全族 2400 通过；IR+契约+situation 123 通过；前端邻域 917 通过；
  tsc 0 错；ruff/边界/diff-check 干净。

## Phase 4 最终同步（2026-10-18，push 前）

- master 前进 ddd10fb4 → e8b3269a（#1616 澄清闭环合入，map-state GET 迁至
  chat_session_views.py）；新 open PR #1620（Pi ABI，占用 ADR-0224）/#1621（project
  runtime，占用 ADR-0220）—— 与本方向语义无重叠。
- ADR 重编 0224→0225（第三例撞号：0219/0220 后），watermark→225，全仓引用同步。
- rebase origin/master：生成物冲突按 ours 通过后统一再生（openapi/scope-matrix/
  api-docs/staleness）；P1-2 剥离面迁移至 chat_session_views.py（#1616 迁移的
  map-state GET 新家），remediation 测试随迁（router 前缀 + ownership override）。
- Rebase 后 gates：IR 88 / gis_harness 族 + clarification 2424 / 契约守卫 22 /
  前端 mapspec+interaction+map-panel 534 / tsc 0 / ruff 0 / boundaries OK /
  staleness 指纹对齐。

## Phase 5 尾注（2026-10-18，ADR 终编 0225→0227）

- 最终合并（PR #1623，master c8c66119）时 0225/0226 已被 RenderWork Protocol
  与 durable project cartographic runtime 占用 —— Interaction IR 再重编
  0225→0227，`adr_watermark`→227（`docs/integration/ownership.json`）。
- 合并时后端引用（`app/services/gis_harness/interaction_ir/*`、
  `app/api/routes/interaction_events.py`）、ADR 文件名与 ownership watermark
  已同步；前端 15 个文件约 30 处注释锚点、ADR-0227 编号自述与本尾注属本轮
  文档清偿（上文 Phase 4 的「0224→0225，watermark→225」为历史记录保留）。
