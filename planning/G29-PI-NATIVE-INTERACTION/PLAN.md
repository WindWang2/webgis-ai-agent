# G29 PLAN — 架构与工作包执行计划

## 顶层链路（验收主链）

```
GIS 工具发现问题(歧义/用户要求核对)                浏览器
  │ dispatch 返回签名封套 (details.webgis_interaction_request)
  ▼                                                ▲ SSE: pi_interaction
webgis-tools 扩展 execute() 校验签名                │
  │ ctx.ui.select/confirm/input/editor (真实原生调用)
  ▼                                                │
vendor 阻塞 Promise ── extension_ui_request ──► PiRpcClient sink(WP02)
                                                   │ 注册+归属 turn(WP03)
用户浏览器回答 ──► POST /answer(owner 校验) ──► one-way stdin 写出(WP02/04)
  ▼                                                ▼
vendor resolve(native id) ──► 扩展拿到答案 ──► POST /pi-tools/interaction-resolve
                                                   │ 选项表反解(WP05)
  ▼                                                ▼
原工具/后续合法工具继续（layer_upsert 绑定 ref / 组件工具采用标题）
  ▼
真实领域结果（MapSpec 修订/图层/导出）+ G28 事件（WP06/12）
```

## 新增文件（全部 G29 所有权）

1. `app/services/chat/pi_ui_protocol.py` — 纯协议层：
   - `UiRequestMethod` 词表；`parse_ui_request(obj)`（方法白名单/字段类型/界：title≤160、
     options≤12×≤160 字符、message≤4000、prefill/placeholder≤8000、timeout∈[5s,PI 上限]）；
   - `build_ui_response(native_id, *, value/confirmed/cancelled)`（互斥校验）；
   - `DIALOG_METHODS` / `FIRE_FORGET_METHODS` / `dialog_default_value(method)`。
2. `app/services/chat/pi_runtime_interaction.py` — 运行中交互注册表（单进程权威 + SessionStore
   有界投影）：
   - `RuntimeInteraction` 记录：host 交互 id `iax-<hex12>`、native_id、method、session/turn、
     worker(bridge) 身份、状态机 `pending → answer_written → observed_resolved |
     cancelled | timed_out | invalidated`（`observed` 由扩展 resolve 回调或 turn 续接事件标记）、
     有界 question 载荷、选项表（display→ref/value）、deadline、来源（envelope/tool）、
     envelope 关联。
   - `attach_to_rpc(rpc, bridge)`：reader sink；归属 = `bridge._current_turn`（无 turn →
     标 `orphan`，仅展示不参与业务消费）；会话 pending 上限（3）超限 → 记录并丢弃（dialog
     拒绝展示？不 —— 上限在封套铸造时拒绝；native 到达时超限 → 立即写 cancelled 并记
     `superseded_by_cap`）。
   - `submit_answer(session_id, interaction_id, answer, actor)`：owner 由路由保证；白名单/类型
     校验；**至多一次**状态翻转；幂等重答返回当前态；迟到（终态后）→ 409 语义记录。
   - `cancel(...)`、`expire_due(...)`（host sweeper；无原生 timeout 的方法用宿主策略
     `PI_INTERACTION_MAX_WAIT_S` 默认 900s；有原生 timeout 的以原生为准，宿主仅在原生
     窗口 + 宽限后标 timed_out 不再写 stdin）。
   - `invalidate_turn(session_id, turn_id, reason)` / `invalidate_worker(bridge, reason)`：
     turn 注销、进程死亡、abort 时调用；发出 `pi_interaction_update(invalidated)`。
   - `accredit_wait(turn_id) -> seconds`：为 stream_prompt 提供「可信等待」记账。
   - SSE 投影：向 `rpc.events` 投递合成事件（`extension_ui_request` 原样+`_webgis` 富化；
     `extension_ui_update`；`extension_ui_fire`），由 mapper 翻译。
   - 持久化：SessionStore `runtime-interactions` 键存有界（≤20）终态+pending 快照，
     供 GET 水合与重连；不承诺恢复 Node 内存 Promise。
3. `app/services/chat/pi_interaction_envelope.py` — 工具侧可信封套：
   - `mint(session_id, turn_id, tool_call_id, kind, payload, options)`：HMAC(bridge_secret)
     签名（env id、sid、turn、toolCallId、kind、options digest、exp）；注册 awaiting_native
     记录；per-turn 待答封套上限（2）。
   - `verify(token)`（扩展侧对称校验数据 + 服务端 resolve 时二次注册表校验）。
   - `consume_answer(envelope_id, native_id, display_answer, ctx)`：选项表精确反解 →
     领域负载（chosen ref / 文本）→ 工具结果 content；标记 observed_resolved。
4. `app/api/routes/pi_interactions.py` — REST 面（全部 require_owned_session + 删除守卫 +
   限速）：
   - `GET  /chat/sessions/{sid}/runtime-interactions`（pending+近期终态，重连水合）
   - `POST /chat/sessions/{sid}/runtime-interactions/{iid}/answer` {value|confirmed|cancelled}
   - `POST /chat/sessions/{sid}/runtime-interactions/{iid}/cancel`
   - `POST /pi-tools/interaction-resolve`（bridge secret + turnToken + 活跃 turn；扩展回调）
   - `POST /chat/sessions/{sid}/turns/{tid}/messages` {message, mode: adjust|queue}
     → control gate 内身份校验 + steer/follow_up 写出 → 真实 disposition 返回
   - `GET  /chat/sessions/{sid}/turns/{tid}/queue`（get_state 投影：pendingMessageCount/
     steeringMode/followUpMode + 已知 disposition 台账）
5. `app/tools/user_decision_tools.py` — 生产决策工具 `webgis_user_decision`（Journey B 发起
   面 + 通用有界问答）：kind∈{confirm,select,input,editor}、有界选项/草稿、业务上下文
   （target=mapspec_title|attribution|dataset_choice|other + component ref）；返回封套。
6. `frontend/components/chat/runtime-interaction-card.tsx`(+test) — 卡片（select radio 组/
   confirm/单行 input/多行 editor；键盘 + IME 安全（isComposing 不提交）+ 焦点恢复 +
   aria；状态机 open→submitting→submitted|terminal(expired/cancelled/invalid)）。
7. `frontend/lib/types/runtime-interaction.ts` — 载荷类型。

## 修改文件（最小接线）

- `app/services/chat/pi_rpc_client.py`：`_read_responses` 增加 `extension_ui_request` 分支 →
  `self._ui_request_sink(obj)`（不落 event 队列，由 sink 决定）；新增
  `send_ui_response(response: dict)`（复用 executor 写路径；**不登记 pending future**；
  进程不在/管道断 → 如实 PiRpcError）。字节上限/序列化失败处理与 issue() 同纪律。
- `app/services/chat/pi_event_mapper.py`：`_EVENT_HANDLERS` 增 3 键（extension_ui_request/
  extension_ui_update/extension_ui_fire → SSE `pi_interaction`/`pi_interaction_update`/
  `pi_ui`）；纯函数、负载从事件 `_webgis` 字段取。
- `app/agent_pi_bridge.py`：①bridge 构造/start 时 attach sink；②stream_prompt 事件循环：
  stall 分支与 total 检查前询问 `accredit_wait`（等待用户不累计 stall；total 期限加记
  账等待，仍有 `PI_INTERACTION_MAX_WAIT_S` 全局帽）；③turn teardown（finally）调用
  `invalidate_turn`；④abort 路径使该 turn pending 交互失效（用户停止语义）。
  （注意：非流式 prompt() 同样记账。）
- `app/extensions/webgis-tools/index.mjs`：execute 包装 —— `details.webgis_interaction_request`
  存在 → verify sig → `ctx.ui.<method>`（timeout 来自封套，ms；signal 透传）→ 答案
  POST `/pi-tools/interaction-resolve`（display answer + envelope id + native id）→
  返回其 content 为工具结果；undefined（原生超时/abort）→ POST cancelled 分支。
  fire-and-forget 不需要扩展动作。`webgis_user_decision` 加入 native dump 面（注册即达）。
- `app/tools/cartography_tools.py`：`webgis_layer_upsert` 裸名 source 分支 —— 数据目录
  精确名匹配：0 → 原行为；1 → 绑定 + 披露；≥2 同名 → 铸封套返回（options 带
  名称/范围/日期版本/ref）。**绑定动作在回答后由模型用返回 ref 续做**（受权消费在
  resolve 端点反解 + 工具链续行）。
- `app/api/routes/chat.py`：SSE 生成器无需改（新事件经 mapper/ring 自动流通）。
- `app/main.py`：include 新 router。
- `frontend/lib/hooks/use-sse-stream.ts`：处理 `pi_interaction`/`pi_interaction_update`/
  `pi_ui` + 追加指令 UI 状态（activeTurn 时输入框提供 adjust/queue 两模式）。
- `frontend/components/sidebar/chat-tab.tsx`：渲染卡片 + steer 入口。
- i18n 文案键。

## 关键设计裁决

1. **回答路径不阻塞等待上游 ACK**：stdin 写出成功 = `answer_written`；「已解决/继续」由
   扩展 resolve 回调（observed_resolved）或后续 tool_execution_end/agent 续接事件关联。
   界面在 written→observed 间如实显示「已送达，等待任务继续」。
2. **每 turn 至多 1 个在飞阻塞问题，队列上限 2**：封套铸造时执行；native 请求到达时
   若已有 pending 且来源无封套 → 仍展示（原生请求合法）但业务封套侧排队语义在扩展侧
   （扩展在第二个封套上先等待第一个 resolve —— JS 顺序 await）。
3. **同名歧义候选上限 8**（封套选项表上限；全量候选过 mint，>8 返回错误引导缩小范围——不静默截断，review P2-2）。
4. **steer 写出在 control gate 内**（与 abort 同款原子段：校验 entry 同一性 → issue →
   释放门 → collect）；响应 disposition 如实返回；用户文字经 `bind_turn_prompt` 同款
   provenance？ —— 否：steer 走 vendor 原生入队，宿主仅在会话事件账（harness record）
   记 user provenance，不重签 turn token。
5. **不新建第二事件总线**：合成事件复用 rpc.events 队列 + 现有 mapper/SSE ring。
6. **幂等与竞态**：状态机单点翻转（registry 内同步 dict + 锁），双标签页第二次提交返回
   首次终态；abort/下一 prompt 竞争由 turn 失效钩子兜底；迟到 stdin 写出对 vendor 无害
   （pending 已删 → 静默忽略），宿主如实标 `late_write_ignored`。
7. **等待预算**：`PI_INTERACTION_STALL_EXEMPT=1` 生效时 stall 计数清零但 heartbeat 继续；
   total deadline = start + PI_TURN_TOTAL_TIMEOUT + Σaccredited(≤PI_INTERACTION_MAX_WAIT_S)。

## 工作包映射

- WP01 → BASELINE.md + 本文件 + INTERFACES.md（方法支持矩阵）
- WP02 → pi_ui_protocol.py + pi_rpc_client.py 接线 + 单测
- WP03 → pi_runtime_interaction.py + 单测（生命周期/幂等/失效）
- WP04 → pi_interactions.py 路由 + 单测（owner/白名单/跨会话/竞态）
- WP05 → pi_interaction_envelope.py + index.mjs 接线 + 单测
- WP06 → layer_upsert 歧义分支 + webgis_user_decision + 领域续行
- WP07 → bridge 记账 + expire sweeper + 取消语义
- WP08 → 前端卡片/投影/水合 + vitest
- WP09 → steer/follow_up 端点 + 队列可见 + 前端入口
- WP10 → 失效钩子 + 恢复水合 + 澄清兼容回归
- WP11 → 上限/错误码/日志脱敏 + 压测小场景
- WP12 → tests/integration/test_pi_g29_runtime_interaction_journeys.py（真实 pin 旅程）

## 验证矩阵（TEST_MATRIX.md 维护）

L1 协议/单元（无子进程）→ L2 桥级（fake rpc + 真注册表）→ L3 真实 pin 旅程
（stub LLM + 真扩展 + 真后端 + 真浏览器语义断言经 API 面；浏览器 DOM 由 vitest +
必要 Playwright 1-worker 冒烟）。
