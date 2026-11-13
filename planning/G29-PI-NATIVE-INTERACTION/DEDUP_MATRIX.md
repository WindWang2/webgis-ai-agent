# G29 DEDUP_MATRIX — 基线查重（base ca7b3ec27）

判定词表：`present`（已存在，转为兼容回归）/ `partial`（部分存在，补剩余）/
`missing`（本任务新增）/ `owned-by-other`（他人所有权，仅消费）/ `blocked`。

| # | 能力/事实 | 判定 | 证据（生产调用链） | G29 动作 |
|---|---|---|---|---|
| 1 | `extension_ui_request` 解析（RPC 客户端） | **missing** | `pi_rpc_client.py:672-679` 两分支路由均不命中（有 type+id）→ 静默丢弃 | WP02 新增类型化解析 + sink |
| 2 | `extension_ui_response` 单向写出 | **missing** | `issue()` 必登记 pending future 等回执；原生 UI 无回执 → 不能复用 | WP02 新增 one-way writer |
| 3 | 运行中交互状态机（pending/answered/resolved/cancelled/timeout/invalid） | **missing** | 无任何模块持有原生 UI 请求生命周期（澄清 envelope 是收口期另一物） | WP03 新领域模块 |
| 4 | 受权回答入口（owner/白名单/幂等） | **missing** | 无 runtime 交互端点；`display-confirmation/ack` 是渲染代次确认（不同用途，present-different） | WP04 新端点，复用 require_owned_session 模式 |
| 5 | 工具→ctx.ui 可信发起（签名封套 + 选项引用表） | **missing** | 扩展 execute 直回调 Python，无任何 ctx.ui 调用 | WP05 封套 + 扩展接线 |
| 6 | 同名数据来源选择旅程 | **missing** | `webgis_layer_upsert` 裸名 source 走 URL/path 伪造路径（`cartography_tools.py:445-477`），无歧义决策 | WP06-A 挂接 layer_upsert |
| 7 | 标题/来源说明核对旅程 | **partial** | 有 `webgis_component_update`/layout 标题写入；无用户确认决策点 | WP06-B 新生产决策工具 + 既有组件工具采用 |
| 8 | 等待预算（stall/total 豁免 + heartbeat） | **missing** | `PI_EVENT_STREAM_TIMEOUT=120s` 会误杀 ctx.ui 等待（连续静默） | WP07 记账豁免 |
| 9 | 浏览器交互卡片（select/confirm/input/editor） | **missing** | `clarification-card.tsx` 是收口澄清卡（radio+REST），复用其模式不复制其状态 | WP08 新组件 |
| 10 | notify/status/widget/setTitle 只读投影 | **missing** | 前端无对应事件 | WP08 有界投影 |
| 11 | steer/follow_up 运行中指令 | **missing** | 仅 `PiBridge.abort` 用 clear_queue+abort；无 steer 发起方 | WP09 新端点 + control gate 内写出 |
| 12 | 队列可见性（disposition/pendingMessageCount） | **partial** | vendor `get_state` 有 pendingMessageCount；宿主不暴露 | WP09 暴露 + SSE |
| 13 | 断线恢复展示（活代际） | **partial** | G22 `TurnEventBuffer` ring 重放 SSE（含未来 pi_interaction 事件）；无 pending 查询端点 | WP10 复用 ring + 新 GET 水合 |
| 14 | 进程死亡/代际失效 | **partial** | `process_died_event`/`_fail_all_pending` 已有；交互记录无人失效 | WP10 失效钩子 |
| 15 | RequirementIR 收口澄清兼容 | **present** | `pi_turn_clarification.py`（agent_settled 期）+ clarification-card | 兼容回归 + 去重投影 |
| 16 | turn 隔离/交接门 | **present** | G22 `_control_gate` + `_ActiveTurnEntry` 同一性校验 | 复用（steer 也走门） |
| 17 | 权限模型（bridge secret + turn token + live turn） | **present** | `pi_tools.py:36-92` | 复用（resolve 回调同款） |
| 18 | MapSpec/组件工具采用用户决定 | **present** | `webgis_component_update`、`webgis_layout_set` 等 | 只消费 |
| 19 | Interaction IR（ADR-0227 浏览器交互埋点） | **present** | `interaction_events.py` 摄入/投影 | 不同用途（用户行为 IR ≠ 运行中原生问答）；不并入 |
| 20 | display_confirmation（ADR-0134 渲染确认） | **present** | `display_confirmation.py` | 不同用途（渲染代次 ack）；不并入 |
| 21 | G22 长会话/恢复目录 | **present** | planning/G22 + #2093 | 复用 + 回归 |
| 22 | G28 完成证据（goal_satisfaction） | **present** | #2090 | 旅程验收对齐其事件 |
| 23 | G24 选择句柄 / G23 durable job / G25 frame / G26 publication | present（他人所有权） | 各 planning/ | 只读消费，不改动 |
| 24 | Pi pin 升级 v1.5.2 | **present** | G22 #2093 已对齐 | 不作为成果；保持 pin |
| 25 | G09 参数化批量 / G21 OD 流 | owned-by-other | 曾分配，未检索到 PR | 不占用 |

## 结论

核心缺口全部在「运行中」交互链（1-14 中 missing 项）；已有权威（15-23）只复用不复制。
不存在与本任务重叠的 open PR（执行时复核：open PR 数 = 0）。
