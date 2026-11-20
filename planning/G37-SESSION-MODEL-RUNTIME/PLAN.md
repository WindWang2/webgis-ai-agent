# G37 PLAN

## 架构决策（与任务书 §3/§4 对齐）

**新模块**（业务逻辑放 `model_runtime/`，不堆回 bridge）：
1. `app/services/chat/model_runtime/session_catalog.py` — 管理员允许目录：base 模型（`resolve_llm_config().model`，恒可选）+ `settings.MODEL_CATALOG_EXTRA_MODELS`（同端点 comma list，部署管理员声明）。能力取 `ModelDescriptorRegistry.resolve`（unknown 保持 unknown）；可用性 = 凭证解析非空；`catalog_generation()` = sha16(base_url, key 在场位, sorted ids)。descriptor ≠ 授权（WP02）。
2. `app/services/chat/model_runtime/session_model_prefs.py` — 持久 preference（表 `chat_session_model_prefs`，conversation_id PK/FK）：mode=inherit|explicit、catalog_item_id、thinking_level、session_token_cap（≤ settings.SESSION_MODEL_TOKEN_CAP_MAX）、revision（CAS：UPDATE…WHERE revision=expected）。owner 校验在路由层（require_owned_session 同款）。
3. `app/services/chat/model_runtime/usage_ledger.py` — 表 `chat_turn_runtime`（每 turn 一行：requested/effective provider+model、thinking requested/effective、response_model、catalog/config generation、runtime_epoch、status、applied_at、usage 汇总+completeness、cost_source）+ 表 `chat_usage_records`（每 message_end 一行：kind=assistant|tool_result、usage JSON 有界、cost total/from_provider、stop_reason、(turn_id, call_seq) 唯一）。写入用短事务 `async_db_session()`，SSE 循环内 `create_task` 不阻塞流；持久失败 → 汇总行缺失/`complete=False` → UI 显示 incomplete（M32）。
4. `app/services/chat/model_runtime/session_model_service.py` — 应用编排：resolve(preference→catalog 校验→能力门 M16/M17) → `set_model` → `get_state` 回读（model+thinkingLevel；requested/effective 分开 M15）→ 失败如实 status（M09/M10，不假成功）。预算 admission：累计（ledger 实数）+ 投影（上下文估计+max_output）> cap → 拒绝（prompt 字节前，M30）。价格：totalFromProvider=true→provider-reported；描述符/价格表→estimated（带版本）；否则 unknown（M28/M29，零不当免费）。

**接线（局部加性）**：
- `pi_rpc_client._spawn_process`：models.json 写**全部目录模型**（共享文件仅含管理员允许项 + `$OPENAI_API_KEY`，原子写/代际不变）；settings.json default 不变。两个 worker 并发 spawn 写同内容 → 安全（M02/M03）。
- `PiBridge.stream_prompt`：`_isolate_vendor_session` 之后、`prompt` 之前插 `await self._apply_session_model(...)`（含预算 admission）；按 `(bound_session, applied_state)` 跟踪避免重复 set_model，spawn/new_session/换会话后强制重应用（M05/M06）。应用失败 → prompt 不发出，task_error（error_class=model_apply_error）。
- 事件观察：stream_prompt 的 drain 循环对每个原始事件先过 `self._runtime_observer(event)`（新增，默认 no-op）：message_end → 账本 record（assistant + tool_result nested 一次 M21）；agent_settled → 汇总 finalize。SSE 重放不重计（resume 只重放缓冲，不再入队）。
- SSE 加性事件 `model_status`（应用后：requested/effective/status/generation/reason）。
- 路由 `app/api/routes/session_model_views.py`（挂 chat.router）：GET/PUT `/chat/sessions/{id}/model`、GET `/chat/sessions/{id}/usage`。PUT 支持 expected_revision CAS（M11）；active turn → pending_next_turn（M04）。
- 工具 `app/tools/session_model_tools.py`：`webgis_model_catalog` / `webgis_session_model_status` / `webgis_session_budget_status`（只读，经既有 dispatch，session_id 注入；不扩 allowlist M34）。
- 迁移：alembic `g37_session_model_runtime.py`（3 表 + 索引）。
- 前端：`frontend/components/chat/session-model-selector.tsx`（目录/不可用原因/继承/当前/待生效/思考档/用量/coverage 折叠）挂聊天输入区；i18n zh/en。

**能力门（WP06）**：tools_needed（本产品 Pi 面恒需工具）→ descriptor.supports_tools()=False 明确拒绝；context_window 已知且 < 上下文估计 → 拒绝并指向压缩/默认模型（不猜）。思考档：`get_available_thinking_levels` 运行时交集；compat supportsReasoningEffort=False → 显示「不可控制」（M14）。

**验证分层**：单测（catalog/prefs CAS/ledger 去重/budget 投影）→ 协议 stub RPC 集成（stream_prompt 全路径）→ 真实 Pi 旅程（复用 test_pi_stub_llm_journeys driver 模式：node stub LLM 记录请求 model/参数、返回 distinct responseModel+usage；两模型 A/B 并发 + 第三会话继承 + 预算拒绝 prompt 零发送 M30 反向证明 = stub 请求计数不增）。

## 工作包顺序

WP01-02 catalog → WP03 prefs+迁移 → WP04 spawn 投影 → WP05 应用+回读 → WP06 能力门 → WP07 receipt → WP08-09 usage 账本 → WP10 admission → WP11 API+UI+工具 → WP12 真实旅程 → review/fix/rereview → 合流/PR。

## 已知边界（如实披露）

- 覆盖：Pi 主模型 message_end 终态 usage + toolResult nested usage（一次）；compaction/branch summary 经 get_session_stats 对账面（快照，不重复累加 M22）；title/planner 等 legacy 辅助调用**不在覆盖范围**（UI 披露）。
- 会话内跨进程持久真相 = 宿主 DB 账本（Pi 以 --no-session 运行）；crash 后未收割缺口标 incomplete。
- 价格：provider-reported（totalFromProvider）或 catalog estimate（来源+版本 llm_price.v1）；unknown 显示未配置。
- 不做：跨供应商回退、自动选模、call-level 硬费用封顶（before_provider_request 不可靠——vendor 实读）、第二套 provider registry。
