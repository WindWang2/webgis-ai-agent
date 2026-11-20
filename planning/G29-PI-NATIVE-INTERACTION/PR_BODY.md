# feat(pi): G29 原生运行中交互与用户指令工作台

## 用户问题

Pi 引擎的 GIS 任务一旦开始，用户在整个 run 期间没有任何受信的输入通道：
- 存在两个同名合法数据版本时，模型只能猜或拒绝；
- 用户要求「作图前让我核对标题/来源说明」时，只能在回合结束后再改一轮；
- 运行中想补充「改为街道范围」或「完成后顺便统计」，要么打断重来、要么无效。
宿主侧 `extension_ui_request` 落在 RPC reader 的两个分支之外被静默丢弃；
`issue()` 登记回执 future，不能当单向回答通道用 —— 上游协议无逐请求消费回执。

## 行为增量（真实闭环，非气泡）

**四方法原生交互**：GIS 工具结果携带 HMAC 签名封套 → 扩展校验签名后调用真实
`ctx.ui.select/confirm/input/editor` → vendor 阻塞等待 → 宿主登记（归属 G22 已验证
turn）并经 SSE `pi_interaction` 呈现卡片 → 用户经受权 REST 回答 → 单向
`extension_ui_response` 写回同一 vendor 进程 → 扩展把答案交回
`/pi-tools/interaction-resolve`（bridge secret + 签名 turn token + 活跃 turn）→
服务端按**保存的选项表精确反解**（不做宽松匹配）→ 工具结果内容回到**同一 agent run**
→ 真实领域结果（MapSpec 只消费被选 ref；标题/来源原样采用）。

**运行中追加指令**：`POST /turns/{tid}/messages`（mode=adjust→`steer` / queue→
`follow_up`），在 G22 control gate 内完成「turn 身份复核 + 写出」原子段，返回真实
vendor disposition（接纳 ≠ 已消费），队列状态可查（`GET .../queue`：vendor get_state
投影 + 台账）。

**诚实状态机**：`pending → answer_written → resolved | cancelled | timed_out |
invalidated`。四个事实分开记录（提交接纳 / stdin 写出 / 扩展继续 / 领域完成）；
vendor 的 confirm 超时默认 `false` 与用户明确选「否」按宿主可得证据分类披露；
等待用户计入可信等待（不误判 stall；总预算按记账展宽，上限
`PI_INTERACTION_MAX_WAIT_S=900s`；editor 无原生 timeout 由宿主策略合法取消收口）；
turn teardown / abort / 进程死亡 → pending 一律失效，旧卡不可作答；双标签页至多一次
有效消费；迟到回答返回当前终态不二次写 stdin。

**fire-and-forget**：notify/status/widget/title/set_editor_text 以有界只读投影披露
（1s 合并窗；不要求回答；editor 建议不静默覆盖草稿）。

## 复用与范围

复用：G22 turn 身份/交接门/事件 ring、澄清收口（不重做，需求 IR 卡与运行中卡并存）、
abort/clear_queue、ToolRegistry/ToolDispatchService、MapSpec 权威、require_owned_session
模式。不新增：第二 agent loop / 事件总线 / 工具注册表 / MapSpec 存储 / journal writer。
vendor pin 保持 v1.5.2（G22 已对齐，升级非本任务成果）。

新文件：`pi_ui_protocol.py`（纯协议层）、`pi_runtime_interaction.py`（交互服务）、
`pi_interaction_envelope.py`（受信封套）、`pi_interactions.py`（路由×2）、
`user_decision_tools.py`（`webgis_user_decision` 生产工具）、前端
`runtime-interaction-card.tsx` + types/adapters/api/reducer 接线。
最小接线：rpc reader 分支 + `send_ui_response` 单向写出 + stdin 行锁、mapper 纯翻译
（`CHAT_EVENT_TYPES` +4）、bridge 等待记账/失效/冲刷、`webgis_layer_upsert` 裸名分支
（URL/path 零漂移）、扩展 ctx.ui 闭环、i18n（zh/en）。

## 实际验证（本地，未等待线上 CI）

- **固定 pin 真实旅程** `tests/integration/test_pi_g29_runtime_interaction_journeys.py`
  （opt-in `PI_JOURNEY_E2E=1`）：真实 vendor v1.5.2 子进程 + 真实 webgis-tools 扩展 +
  真实 dispatch/REST/SSE，仅 LLM 由本地 stub。**1 passed（120s）**：
  - J29A 同名选择：选项含版本日期/范围 → 用户选 2024 版 → `resolved` → 续行工具以
    返回 ref 绑定 → **MapSpec 只含被选 ref**；J29B 标题核对：editor 多行中文（标题+
    来源说明）→ 同 run 采用，MapSpec 一致；J29C：steer/follow_up 均接纳 + 真实
    disposition + 队列可见；J29D：取消 → 不绑定任何数据源；越权 answer → 404。
- 单元 28 passed（协议互斥/封套签名跨语言一致性/状态机幂等/白名单/超时/上限）；
  前端 vitest 56 passed（卡片 IME/键盘/终态 + use-sse-stream 回归零漂移）+ `tsc` 0 错误；
  既有回归 cartography/sse-contracts/clarification/pi-integration/g22/g28 全绿。
- **既有失败（非本任务回归，干净 base 同条件复现）**：`test_pi_rpc_client_810::
  test_stop_reaps_after_sigkill_810`、`test_streaming_lifecycle::
  test_stream_prompt_aborts_pi_on_disconnect`（环境/时序）。

## Review / Fix / Re-review

- **独立评审**（未参与实现的 reviewer，对 base 全量 diff 对抗性审查）：
  APPROVED-WITH-NITS —— 0×P0/P1；2×P2（awaiting 表类级共享+不修剪、
  同名候选静默截断）、1×P2 文档过度声明、9×P3。
- **修复**：全部 P2/P3 清偿（含新增 committed 跨语言签名单测：node 加载
  生产 index.mjs verifyEnvelope 正/篡负例；expire_due 锁跳过；cap 判定先于
  配对；steer i18n 双 locale；台账 LRU；跨会话守卫补齐；死代码删除等）。
- **复审**：逐条 file:line 复核 → **APPROVED**（残余 3×P3 nit 亦已清偿）。
  修复后真实 pin 旅程复跑通过。记录：`planning/G29-PI-NATIVE-INTERACTION/REVIEW.md`。

## 兼容 / 回退 / 未测项

- 兼容：全部加性（新事件注册入词表；新端点；layer_upsert 仅裸名分支且 URL/path
  形态零漂移）；kill 开关无需专用 env——封套只在同名歧义/显式决策请求时产生。
- 回退：revert 本 PR 即回到原行为（无 schema/迁移变更）。
- 未测/限制（如实）：Playwright 浏览器冒烟未跑（DOM 语义由 vitest + L4 API 层覆盖）；
  `input` 方法未单独 L4 旅程（L1 白名单 + L3 卡片覆盖）；跨 pod（turn 不在本进程）时
  steer 返回 TURN_NOT_LOCAL（单进程验证，多 pod 亲和属 G22 既有约束）；交互快照为进程内
  视图 + ring 重放 + GET 水合，跨进程重启后旧记录只读终止（boot_id 门），不做
  SessionStore 持久化复活。
