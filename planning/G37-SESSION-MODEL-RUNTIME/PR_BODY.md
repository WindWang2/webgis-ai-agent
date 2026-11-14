# G37：会话模型运行配置 —— 允许目录内会话选择、实际生效回执、真实用量账本与预算 admission

## 用户问题

用户此前无法回答五个基本问题：我为这个会话选的模型生效了吗？为什么某模型不可选？当前这轮用的是哪个模型？这轮/累计花了多少（钱的事实来源是什么）？下一轮会不会超预算？设置页只有 admin 全局只读配置；spawn 只注册单模型；`message_end` 的原生 usage 全仓零消费者；会话路径没有 LLM token admission。

## 行为增量（base 1276692737 → 本分支）

1. **管理员允许目录（WP01/02）**：`session_catalog` —— 基础模型（`model_config.resolve_llm_config` 单一解析点）+ `SESSION_MODEL_CATALOG_EXTRA`（同一已授权 OpenAI 兼容端点、服务器端凭证；descriptor ≠ 授权）。目录项含能力（unknown 保持 unknown）、可用性与不可用原因（credential_missing/invalid_id）、`catalog_generation()` 指纹。
2. **会话 preference + CAS（WP03）**：新表 `chat_session_model_prefs`（键=既有 conversations.id）；inherit|explicit、思考档请求、会话 token 上限（≤ admin `SESSION_MODEL_TOKEN_CAP_MAX`）；revision CAS —— 双标签并发明确 409，不静默丢更新。
3. **spawn 目录投影（WP04）**：`models.json` 写全部目录模型（共享文件仅管理员允许项 + `$OPENAI_API_KEY` 引用；settings.json default 仍是基础模型）—— 会话选择绝不重写公共 defaults。
4. **turn 边界应用（WP05/06）**：`PiBridge` 在 isolate 之后、prompt 字节之前：解析→能力门（工具不支持/上下文窗不足拒绝）→预算 admission→`set_model`→`get_state` 回读。requested/effective/responseModel/thinking requested+effective 全部分列；explicit 失败/回读不同模型 → 拒发 prompt（`model_apply_error`）；`set_thinking_level` 因端点 `supportsReasoningEffort=false` 不发送，UI 明示「不可控」（M14/M15）。worker 复用/new_session/respawn 后按 `(session, provider, model, spawn_epoch)` 跟踪强制重应用（M05/M06）。
5. **运行回执 + 用量账本（WP07/08/09）**：新表 `chat_turn_runtime`（每 turn 回执+汇总）/`chat_usage_records`（每条 `message_end` 终态 usage；(turn_id, call_seq) 唯一 → 重放/重复回调不重复计费）。reasoning⊂output、cacheWrite1h⊂cacheWrite 不另加；toolResult nested usage 只记一次；费用三态：provider-reported（`totalFromProvider=true`）/estimated（Pi 目录价或宿主价格表 `llm_price.v1`）/unknown —— **零价不当免费**。落盘 fire-and-forget，失败 → complete=false → UI 显示 incomplete。
6. **预算 admission（WP10）**：prompt RPC 字节发出前的确定性检查（累计实数 + 下一轮投影 > 上限 → `session_budget_exceeded` / policy_reject，可行动提示）；不宣称 call-level 硬费用封顶（vendor `before_provider_request` throw 被捕获，不能作门 —— 实读后不采用）。
7. **API/UI/工具（WP11）**：`GET/PUT /chat/sessions/{id}/model`、`GET /usage`（`require_owned_session` owner 守卫）；additive SSE `model_status`（task_start 之后，不破坏首事件契约）；前端 `SessionModelSelector` 工作台（目录/不可用原因/继承/当前/待生效/思考档/用量/coverage，服务器回执为真相，CAS 冲突自动刷新）；Pi 只读工具 `webgis_model_catalog` / `webgis_session_model_status` / `webgis_session_budget_status`（经既有 dispatch，session_id 注入，不扩 allowlist）。
8. **既有竞态修复（M06 旅程暴露）**：Pi reader 世代钉扎 —— 外部 kill→respawn 换代后，旧 `_read_responses` finally 无守卫地翻全局 `_process_died` 并 fail 新代 pending，把健康新进程毒死。reader 现绑定本代 proc（循环/读行/finally 全守卫），不偷读/不毒害新代。

## 复用与范围

复用 `model_config`（单一解析点/代际）、`ModelDescriptorRegistry`（能力元数据）、turn lease/单 writer/control gate、`config_generation` 换代回收、`require_owned_session`、ToolRegistry→dispatch、既有 quality-e2e/旅程基建。不新增第二套 agent loop/provider registry/预算引擎；不改 vendor gitlink（b4afdddf）；`llm-config.tsx` admin 页未动。辅助 LLM（title/planner）与 compaction 模型工作不在账本覆盖范围（UI/工具如实披露 not_included）。

## 实际验证

- 单元 38 例通过（目录/CAS/usage 提炼与幂等/费用三态/能力门/admission/应用编排/stream_prompt 协议路径）。
- **真实 Pi 全链**（`tests/integration/test_pi_g37_session_model_journeys.py`，node stub LLM 记录每次请求的 model + 回包 usage/responseModel + uvicorn 生产 app + 真实 vendor v1.5.2 子进程）通过：J0 显式选 g37-b + `webgis_map_intent` 真实 dispatch + 2 条 usage 记录 + responseModel；J1 三会话并发/交错/回切不串模型，继承会话恒用默认；J2 预算拒绝时 stub 请求计数零增（prompt 字节未发出）；J3 kill→观测死亡→respawn 后偏好重应用、累计不归零；J4 Last-Event-ID 重放不重发不重计。
- **浏览器全链**（`tests/integration/test_g37_browser_journey.py` + `e2e/journeys/j18-g37-session-model.journey.ts`，stub LLM + uvicorn + `next dev` + Playwright/Chromium）：UI 选择 g37-b → 服务端回执 effective=g37-b + 用量入账 → API 收紧上限后 UI 可见预算拒绝。
- 迁移 `g3701` upgrade/downgrade/upgrade 实跑（sqlite）；alembic head 单调（g3701 ← 0106）。
- 前端 `tsc --noEmit` 零错误、eslint（改动文件）零警告、vitest（store 解析 3 例）通过；zh/en i18n 同步。
- 既有回归：test_pi_e2e / test_pi_bridge_lock / test_pi_model_config_generation / test_chat_api / test_session_ownership / test_g37 组合 71 通过；**2 个 base 既有失败**（`test_prompt_drain_timeout_raises_and_sends_abort`、`test_prompt_send_failure_sends_abort`：MagicMock 不可 await 的 abort 断言，干净 base 复现取证，非本 PR 引入、未修改）；`test_pi_rpc_client_810::stop_reap` 同为 base 既有。

## Review / Fix / Re-review

独立 reviewer（未参与实现）全量 diff 评审 → 修复 → 复审记录见 `planning/G37-SESSION-MODEL-RUNTIME/REVIEW.md`。

## 迁移与回退

纯加性：3 张新表（g3701），`downgrade -1` 干净回退。无既有表/列变更；`SESSION_MODEL_CATALOG_EXTRA` 缺省空 = 目录仅基础模型，行为与旧版一致（单模型）；SSE/API/UI 均为加性，旧消费者零漂移。

## 未测项与已知边界（如实）

- 真实供应商账单不可得（无真实凭据）—— 费用事实只能来自回包 totalFromProvider / 价格表，分层披露。
- compaction/branch-summary 的原生 usage 上报路径未在旅程触发（stub 不产生压缩）；账本 coverage 面如实标注 not_included。
- 浏览器旅程按仓库车道纪律双 opt-in（PI_JOURNEY_E2E=1 + REQUIRE_BROWSER=1）。
- 无线上 CI 等待（按任务书）；本地关键验证全部实跑。
