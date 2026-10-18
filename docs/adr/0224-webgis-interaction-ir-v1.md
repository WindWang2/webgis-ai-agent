# ADR 0224 — WebGIS Interaction IR V1：Human ↔ Map ↔ Agent 观察总线

- 状态：Proposed（PR 评审中）
- 日期：2026-10-18
- 方向：Goal 02 —— 统一、版本化、有界、可回放、可归因的用户地图交互事件契约
- 基线 commit：`ddd10fb4db05cf27446ff5a8ddb4b2917f505952`
- 前置 ADR：ADR-0180（GIS Situation）、ADR-0183（mutation 幂等键）、ADR-0194（画布 affordance）、ADR-0214（render observation 对账）、ADR-0216（turn journal）、ADR-0219（representation binding）
- 编号说明：分配器给出 0220，但 open PR #1615/#1616/#1618 已占用 0220/0222/0223（其分支不在本地扫描面内）—— 手动跳至 0224 并把 `adr_watermark` 推进至 224，撞号风险从"静默"变为"分配时可见"（与撞号史 0219/0220 的处理惯例一致：合并前 merge-sim 若红则重编）。

## 背景与问题

Agent 此前只能从轮首文本快照（`_cartographic_context_observation`）与 canvas affordance 间接感知用户在地图上做了什么。仓库内已有四套互不兼容的"交互痕迹"：

1. `viewport_seq`（F4 双写方序号，单 key）；
2. `_situation_interactions` 环（ADR-0180 S4 摄入骨架，`record_interaction`）—— **前端零生产者**（/ws 感知通道死代码）；
3. map-action ACK 的 `action_id`（仅 agent 下发动作回执）；
4. MapSpec envelope 的 `mutation_id`（`c:<client_mutation_id>`，仅 mutation 面）。

缺失的是一套正式协议，能回答："用户刚缩放到哪里/选了什么/画了什么/改了什么；哪些是明确的 cartographic preference；某次 Agent 决策由哪次交互触发；reconnect/多标签页/resume 后事件是否丢失、重复或倒序。"

## 决策

### C1 Interaction IR 是观察层，不是第二真相

事件只记录"发生了什么 + 与哪个 authority revision 关联"：

- 用户 layer 可见性/排序/样式/删除 mutation **仍走** MapSpec lifecycle（`user-mutation.ts → POST /mapspec/mutations`）；IR 事件在 mutation 落定后发射，携带 `client_mutation_id` + 结局。
- palette/style 偏好绑定复用 #1614 representation binding —— 本方向不新建 preference store。
- 框选/绘制未提交持久编辑的几何 → 纯 interaction fact（几何本体留客户端画布层，ledger 只存摘要）。

### C2 词表封闭、只收真实交互（13 kind）

`app/services/gis_harness/interaction_ir/contracts.py` 是词表唯一权威（前端镜像 `frontend/lib/interaction/contract.ts`；双端由 `tests/unit/gis_harness/interaction_ir/contract_manifest.json` 漂移闸锁定，carto_ir manifest 模式）：

| kind | authority | 合并 | 前端来源 |
|---|---|---|---|
| viewport_settled | observation | replace_pending | map-panel 100ms settle debounce |
| feature_selected / feature_deselected | observation | none | use-feature-selection / 空点 / 弹窗关闭 |
| aoi_drawn / aoi_edited / aoi_deleted | observation | none | sketch-editor 用户意图命令 |
| measurement_created | observation | none | handleCompleteMeasurement |
| layer_visibility/opacity/style/order_changed, layer_removed | mapspec | none | user-mutation.ts 五个提交入口（结局如实） |
| comparison_mode_changed | observation | none | workbenchSlice enter/exit |

**评估后未收录**（不为词表完整伪造）：temporal scrub（前端无时间轴 UI）、feature inspect（与 select 同一 UI 时刻）、measurement clear（无干净的单点清理钩子）、component move/resize（dock/workbench 无统一提交点）—— 均为 V2 候选，待真实 UI 钩子出现再入词表。

### C3 摄入 = 纯函数状态机，生产与回放同一路径

`LedgerState + apply_event(state, raw) → verdict`：不读时钟、不用随机源、不做 I/O。生产接线（`ingest_interaction_batch`：load → fold → persist）与回放（`replay_scenario`）共用同一 fold —— **回放确定性由构造保证**。裁决词表：`accepted / accepted_with_gap（披露 gap_from）/ duplicate（环内 event_id 兜底）/ stale_seq（per-instance 水印幂等）/ rejected（actor 伪造、词表外、payload 白名单外）`。

### C4 持久层：map_state 有界键，不建新表

- 候选否决：Turn Journal（turn 作用域、无多标签页 client 语义、fail-open 诊断账本）；`session_data.append_event`（deque(20)、无 seq/幂等）；新 DB 表（无限增长，无必要性证明）。
- 采用：`_interaction_ir_ledger`（环 128 条 / 96KB 字节预算，淘汰计入 `dropped` 披露，不假装保留全部历史）+ `_interaction_ir_clients`（per-instance 水印，≤8 实例 FIFO）+ `_interaction_ir_meta`（server_seq / dropped）。生命周期（clear/delete/TTL/Redis 降级）免费复用 session_data。
- 写入唯一入口：interaction-events 路由（session 锁内三键 read-modify-write，锁降级 fail-closed 503 —— 与 map-action-ack 同纪律）。

### C5 传输：REST 批量，不复活死 /ws，不侵入 collab WS

`POST /chat/sessions/{sid}/interaction-events`（批 ≤50，per-IP 限速 300/min，`require_owned_session`，删除 tombstone 双探测）+ `GET .../projection`（只读有界投影）。重连 replay = 前端 outbox 幂等重试（水印收敛）。客户端端点只放行 `actor=user` —— 防伪造 agent/system 事件。

### C6 causality bridge：事件 ↔ mutation 存证 ↔ requirement 链

- mutation 链接型事件在摄入时对 `map_state["_mutation_dedup"]`（引擎 `c:<id>` 幂等索引）**只读交叉校验**：命中 → `mutation_verified=true` + 权威 `verified_revision`；未命中（索引界 64 淘汰 / superseded 不进索引）→ `verified=false`，客户端申报保留但投影按 claimed 呈现。
- **诚实性不变量**：`mutation_outcome ∈ {superseded, failed}` 的尝试进 `attempted_not_applied`，绝不投影为成功 override。
- 链路：`InteractionEvent(client_mutation_id) → _mutation_dedup 存证 revision →（#1614 semantic_projection）RequirementDocument patch` —— Agent 可以证据回答"为什么保留这个图层"。

### C7 前端 outbox（`frontend/lib/interaction/`）

与 map-action-acks 同族工程范式（debounce 500ms / 批 ≤50 / 重试 ≤3 / transient-permanent 分类 / 队列 256 溢出丢最旧），新增：

- per-tab 身份：`sessionStorage` 维持 clientInstanceId + 单调 client_seq（只持久化一个数字，无队列持久化 —— 无 unbounded localStorage）；
- `replace_pending` 合并：viewport 风暴在队列内合并为最后 settled 态；
- 水印同步：ack 信封 `watermarks` 裁剪已记账事件（ack 丢失后的重试收敛，不无限重发）；
- pagehide best-effort：`fetch keepalive`（批 ≈15KB 远离 64KB cap；sendBeacon 仍不可用 —— 无法带凭据头）；
- 会话切换冲刷（use-workspace-session 两处 reset 点）；SSR/无会话 emit 静默丢弃。

### C8 有界投影（agent context 面）

`project_interaction_snapshot(ledger)`：viewport / selection / active_aoi / user_overrides（newest-first，≤16）/ attempted_not_applied（≤8）/ recent_interactions（≤12，viewport 合并为 1 条）/ dropped 披露 / reason-coded `omitted` / token_estimate。**序号驱动、不读时钟** —— 投影是 ledger 的纯函数。`compile_interaction`（GIS Situation）additive 优先读 IR ledger，旧 ring 缺省回退；复合 interaction revision 并入 IR server_seq。

### C9 隐私 / 数据最小化

- 任何 kind 的 payload 白名单**不含原始 feature properties**（服务端 normalize 强制丢弃未知键 + 截断）。
- feature 事件只带：layer_id / feature_id / 字段**名**（≤8）/ geometry_digest（坐标圆整 1e-6 后非密码学摘要）/ dataset_ref。
- kind 元数据带 sensitivity 分级（internal / limited）。

### C10 回放语料（`replay_corpus/*.json`，10 场景）

pan/zoom 风暴 500、select→hide→续答、AOI 画→析→改（stale 旧结果）、双标签页交错、重连重发整批、mutation 被 superseded、旧事件迟于新世代、会话切换清零、gap 披露、隐私字段剥离。每场景双跑断言逐位一致（确定性）。

## 词表边界与已知限制（诚实披露）

- **undo/redo 不是事件**：草图 undo 走 `replaceSketchFeatures` 重放、不经过用户意图包装 —— 撤销绘制后 `active_aoi` 投影可能滞后（V2 候选 `sketch_undone`）。
- **mutation 存证窗口**：`_mutation_dedup` 界 64 —— 超窗事件的 `mutation_verified=false`（claimed 级呈现，不冒充已校验）。
- **环 128 条**：更早历史被淘汰（`dropped` 披露）—— 本方向目标是 agent 近因上下文，不是审计系统（审计面已有 provenance 环 + turn journal）。
- **viewport 投影跨标签页语义**：last-settled-by-server-seq wins（用户活跃标签页即最后到达者）；同一标签页内由 client_seq 单调保证。
- **user_overrides 无失效语义（review P3-5）**：agent 事后经 MapSpec 改同层 presentation 后，override 仍陈述"用户最后一次明确意图"（观察层定位，不回写权威）。消费方应结合 mutation revision/时间线自行裁决；若需要"事件后又被改"信号，V2 在 entry 上带 last-seen mapspec revision。

## 独立 review 清偿（round-1，2026-10-18）

独立 reviewer 从 `origin/master...HEAD` 全量重读，5 P1 / 4 P2 / 6 P3，全部清偿：

- **P1-1** `gis_situation/projection._render_interactions` 适配 IR 双形态（`kind@server_seq` + 引用摘要子集；IR 新→旧取头 3，legacy 取尾 3）—— 此前 IR 条目被渲染为 `kind@None=null` 垃圾。
- **P1-2** map-state 恢复面剥离 `_interaction_ir_*` 三键 + `_updated_at` 伴生（后端私有，不下发浏览器；agent 消费走投影端点）。
- **P1-3** reorder 的 mutation 未发生（enqueue 会话守卫短路 → undefined）不再申报 committed。
- **P1-4** viewport 事件源加 FE-3 手势判别（`originalEvent` 缺席 = 程序性相机，不 emit）；comparison 的 emit 从 slice action 移语义到 `origin` 参数（spec 驱动推演不发）。
- **P1-5** failed/superseded 的 emit 统一过会话复核（emit 函数闭包守卫）—— 旧会话落定尝试不再进新会话 ledger。
- **P2-1** gap 只对已知实例（watermark>0）披露 —— 新实例（新 tab/复制 tab/刷新续号）首 seq 不再产假 gap；前端切会话重置 seq（对齐 per-(session, tab) 从 1 起契约）。
- **P2-2** 实例 id = sessionStorage 持久 base + 每次加载的内存后缀 —— 复制标签页克隆 storage 不再共享实例互判 stale。
- **P2-3** 持久化降级响应不携带 `watermarks`（水印只在对账落库时携带）。
- **P2-4** ledger 读失败 fail-closed：`InteractionLedgerReadError` → 503 背压，不再折叠为空态覆写共享账面。
- **P3-1/2/3/4/6** 死分支（feature_inspected）删除、bus 未用参数删除、outbox 死代码与合并条件清理、记录预算常量关系断言测试、list 内 dict 元素收紧。

每项修复均带机器回归（`test_review_remediation.py` / `user-mutation.interaction-ir.test.ts` / `map-panel.interaction-ir.test.tsx`）。

## 后续不变量

1. 词表 / 白名单 / 有界常量的唯一权威在 `contracts.py`；改词表必须同步 manifest JSON（双闸即红）。
2. 摄入语义变更必须先过 replay corpus（生产/回放同路径，corpus 红即语义漂移）。
3. 任何新事件源（V2 kind）必须先有真实前端交互钩子再入词表。
4. 观察层永远 fail-open 于交互主链路：emit/ingest 失败不得阻塞地图或 mutation。
