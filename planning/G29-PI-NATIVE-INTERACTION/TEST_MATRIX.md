# G29 TEST_MATRIX — 验证分层与证据

执行环境：worktree g29-pi-native-interaction-20261112T144716Z；vendor/pi v1.5.2
(b4afdddf4) 本地构建（dist/bundle 84 files）；venv 3.13；node v24.21；重阶段全部
经 `webgis-heavy-stage.lock`。

## L1 单元/协议 — `tests/unit/test_g29_runtime_interaction.py`（29 passed）

含 committed 跨语言签名测试（node 子进程加载生产 index.mjs 的 verifyEnvelope：正向验签 + 嵌套 business 篡改负例）。

| 面 | 断言要点 |
|---|---|
| 协议解析 | select/confirm/input/editor/notify 全形态；未知方法/坏 id/空 options/超界 → UiProtocolError 码；editor 无原生 timeout；confirm 默认 false |
| 响应构造 | value/confirmed/cancelled 三态互斥（双设/全空拒绝）|
| 单向写出 | send_ui_response 不登记 pending future（泄漏红线）；进程不在 → PiRpcError；写字节即 vendor 契约 JSON |
| sink | 无 turn 归属 → orphan 计数；合成事件（extension_ui_request+富化）入 rpc.events |
| 封套 | 签名 roundtrip；篡改载荷/过期拒绝；per-turn cap=2；canonical JSON 递归键序 —— Python 铸造 → Node verifyEnvelope 跨语言双向验证（含中文/嵌套 business/篡改负例）|
| consume | 选项表精确反解（"人口" ≠ 选项拒绝，不宽松匹配）；一次性消费；timed_out（宿主记录）vs 用户 confirm=false 分类；无封套全文 → NOT_PENDING |
| 生命周期 | 双标签页幂等（第二次 already_answered，不写第二次 stdin）；白名单外值拒绝且状态不污染；跨会话 NOT_FOUND；worker gone → invalidated；原生超时 → timed_out 不写 stdin；invalidate_turn 后迟到回答 duplicate；waiting_for_user 记账；会话 pending cap → flush 后 cancelled:true 写出 |
| fire | 1s 合并窗；notify 保留 |
| 工具 | webgis_user_decision 铸封套；layer_upsert 同名歧义封套（选项含年份+范围）；URL/path 裸串零漂移 |

## L2 桥级（fake rpc + 真注册表）

`/tmp` 定位测试（已并入 L1 语义）：等待用户时 turn 在 total 预算超时后仍存活
（记账展宽，心跳继续），error 事件不发。核心断言已由 L1 + L3 覆盖。

## L3 前端 — vitest（56 passed, 3 文件）

- `runtime-interaction-card.test.tsx`（5）：select radio+Enter（中文长选项）；
  editor 多行保留 + IME isComposing Enter 不提交；confirm 是/否/取消三路；
  answered/timed_out 全禁用不渲染提交；input 空值禁用。
- `use-sse-stream.test.ts`（46，既有回归零漂移）、`clarification-card.test.tsx`（既有）。
- `tsc --noEmit` 0 错误。

## L4 固定 pin 真实旅程 — `tests/integration/test_pi_g29_runtime_interaction_journeys.py`
（PI_JOURNEY_E2E=1；**1 passed in 120.40s**，2026-11-13）

真实层：vendor v1.5.2 bundle rpc-entry 真子进程 + 真实 webgis-tools 扩展 +
真实 /pi-tools/execute→ToolDispatchService + 真实 SSE + 真实 REST 面。仅 LLM 生成由
本地 stub 承担（模型可换，协议/派发/写入/渲染不 mock）。隔离：fakeredis TCP
（RESP2 兼容补丁）+ 独立 sqlite + 独立端口 + 合成同名数据集（真实 store ref + 真实
artifact registry 行）。

| 旅程 | 断言（全绿） |
|---|---|
| J29A 同名数据选择 | `pi_interaction` SSE；select 选项含版本日期/范围；受权 answer 200 accepted；resolve 回调观察 `resolved`；续行工具用返回 ref 绑定；**MapSpec 权威只含被选 ref B、不含 ref A**；task_complete |
| J29B 标题/来源核对 | editor 原生请求；用户多行中文（标题+来源说明）answer；同一 run 续行 component_update；MapSpec 标题+来源行一致；task_complete |
| J29C 运行中追加指令 | steer(adjust) 与 follow_up(queue) 均 200 accepted + 真实 vendor disposition；`pi_turn_message` SSE；GET queue 投影 pending_message_count=2 + 台账 2 条 |
| J29D 取消 | cancel → `cancelled`；同一 run 继续但不绑定任何数据源（MapSpec 无 pop2）|
| 越权负例 | 无 owner token 的 answer → 404（不泄漏内容）|

## L5 既有回归（本分支跑过，全绿）

- `test_cartography_tools.py`(6)、`test_sse_contracts.py`+`test_clarification_loop.py`(38)、
  `test_pi_integration.py`+`test_streaming_lifecycle.py`(34/35，见下)、
  `test_pi_rpc_client_810.py`(3/4)、`unit/test_g22_recovery_reconciliation.py`、
  `unit/test_final_data_high_evidence.py`(19)、`unit/test_pi_bridge_pool_prod_routing.py`。
- **既有失败（非本任务回归，干净 base 同条件复现）**：
  - `test_pi_rpc_client_810.py::test_stop_reaps_after_sigkill_810`（环境相关）
  - `test_streaming_lifecycle.py::test_stream_prompt_aborts_pi_on_disconnect`（时序）
  - `webgis_layer_upsert` 以无 `layer.source` 的 layer 直调 → MapSpec 拒绝（master 同样
    行为，复现脚本 stash 对照；旅程以带 source 的合法形状调用）。

## 验证层级披露

- 浏览器 DOM：vitest（jsdom）+ 卡片键盘/IME/aria 断言；**未跑 Playwright**（本轮无
  新页面路由，SSE→卡片链由 L3+L4 覆盖；如实披露）。
- LLM：本地确定性 stub（协议面全真）。
- 四个 dialog 方法：select/editor/confirm 于 L3+L4 真协议往返；input 由 L1 白名单 +
  协议构造 + L3 卡片输入覆盖（未单独 L4 旅程，如实披露）。
