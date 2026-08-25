# Harness 代码审查报告

- 审查日期: 2026-08-24
- 审查人: Agent A（AI Agent/Harness 专项）
- 审查方式: 只读逐行精读 + 交叉验证（每个发现均已读上下文确认无既有机制覆盖）
- 范围: `app/services/chat/`、`app/services/gis_harness/`、`app/agent_pi_bridge.py`、`app/tools/registry.py` + `app/tools/__init__.py`、`app/services/session_data*.py`、`app/services/jobs/`、`app/api/routes/chat.py`，以及直接关联的 `tool_dispatch_service.py`、`tool_catalog.py`、`planning/`、`event_resume.py`、`task_tracker.py`

---

## 1. Harness 现状架构小结（真实调用链，附 file:line）

### 1.1 Legacy ChatEngine 路径（默认，`USE_NEW_AGENT` 关闭时）

启动装配（`app/main.py:58-66`）：`init_tools(registry)` 注册 ~149 工具 → `ToolCatalog(registry)` → `ChatEngine(registry, tool_catalog=catalog)`（`ChatEngine` 是 `ChatExecutionEngine` 的兼容壳，`app/services/chat_engine.py:39-41`）。

一个流式回合的真实调用链（`app/api/routes/chat.py:939-978` → `app/services/chat/execution_engine.py:1507`）：

1. `_get_or_create_session`（LRU 会话缓存 + DB 冷加载，execution_engine.py:614）→ 轮询式获取 per-session turn 锁（:1543-1548，#554 保活）。
2. `tracker.create`（:1564）→ `_maybe_plan`（:1597-1613，#435 心跳泵内等待）：
   - `classify_followup` 确定性分类（planning/followup.py:64）→ `should_plan` 门控（plan_orchestrator.py:260）→ 命中则 **一次非流式规划 LLM 调用**（plan_orchestrator.py:449-471 `make_plan` → `call_llm`，120s 平顶超时，llm_client.py:363）；
   - 规划成功后附着确定性 GIS intent/recipe（plan_orchestrator.py:479-489，仅作证据，不参与规划本身）。
3. 工具循环（最多 `max_rounds=60`，execution_engine.py:334）每轮：
   - `_select_tools`（:416-457）：ToolCatalog 三层选择 + 按用户轮衰减的 sticky 域（tool_catalog.py:152-279）+ 计划声明域并集；
   - `_compose_request_messages`（:556-583）→ `ChatContextAssembler.assemble`（context_assembler.py:203-396）：环境感知（map_state/inventory/event_log 单 pipeline 读取）、项目上下文块（指纹 LRU 缓存）、执行计划块、制图 verdict 块、6000-token 历史预算截断（history_compression.py:84-121）；
   - `_call_llm_stream`（#409 心跳泵 execution_engine.py:108-154；SSE token 批处理 :1698-1730）；
   - 工具波：并行 `tool_pipeline.execute_tool_call`（tool_pipeline.py:57）→ 共享 `ToolDispatchService.dispatch`（tool_dispatch_service.py:233，dedup 锁 + 失败释放槽位 + ref 存储 + MapSpec 授权挂载）→ `registry.dispatch`（registry.py:466：别名批量解引用 :976、Pydantic 校验、tier-3 chokepoint 拒绝 :655、策略化执行 INLINE/ASYNC/THREAD/CELERY :915、300s 墙钟预算 :821）；
   - 结果按 tc 顺序回填 LLM 上下文 + 落库（`_persist_tool_messages`，execution_engine.py:735-776）；孤儿 tool_call 修复（:706-733，F9）；no-progress 熔断（:2128-2172，阈值 3）；
   - 计划打勾：仅"非可疑成功"推进（:1952-1967，P1-A 限定通配 plan_orchestrator.py:579-629）。
4. 回合收尾：`_flush_plan` 持久化 canonical 计划（:1115-1132）、`_trim_session_tail`（:664-704）、TurnEvidence 结算（:2286-2303）。

### 1.2 GIS Harness（确定性意图 → 配方 → 产品）

- `resolve_map_request_intent`（gis_harness/intent.py:379-465）：纯正则/词表规则解析 typed intent，无 LLM；LLM hint 只能经 `merge_intent_hints`（intent.py:483-540）白名单合并且 protected task 不可降级。
- `RecipeRegistry.select_candidates`（recipes.py:494-562）：确定性六元组排序（几何失配 > task 命中 > 显式形态信号 > 交集 > 项目验证加成 > priority）。
- `MapProductPlanner`（gis_harness/planner.py:166-537）：intent → draft plan（能力面声明 + 能力→工具解析表 CAPABILITY_TOOLS :43-59）→ 数据回来后 `finalize_with_profile` 确定性 eligibility 复检 + 降级决策记录。
- Agent 工具面三个入口（gis_harness/tools.py）：`webgis_map_intent`（:135-214，纯确定性）、`webgis_map_product`（:231-684，资格复检 + 角色绑定 + 组件落 MapSpec）、`webgis_component_update`（:700-777，局部组件突变）。意图/配方对 LLM 是**硬约束语义**（护栏在 merge/eligibility 两侧代码化），但入口本身依赖 ToolCatalog 把工具选进 LLM 视野。

### 1.3 Pi 路径（`USE_NEW_AGENT=1`）

`chat_stream`（chat.py:821-936）→ `pi_bridge.stream_prompt`（agent_pi_bridge.py:1872-2216）：单例子进程 JSON-RPC（pi_rpc_client.py），turn 级签名 token（pi_turn_context.py），事件→SSE 纯映射（pi_event_mapper.py），工具经 HTTP 回调 `dispatch_tool`（agent_pi_bridge.py:272-501）复用同一 `ToolDispatchService`；回合终止以 `agent_settled` 为准（#855）；全程心跳 + 180s 停滞预算 + **900s 整回合总预算**（agent_pi_bridge.py:85-100）。Pi 路径**刻意不走** legacy 规划链/ToolCatalog（chat.py:823-828，#726 裁决）。

### 1.4 状态与持久化

- `session_data_manager`（session_data.py:420-459 工厂）：Redis 后端（session_data_redis.py，L1 2s 进程内缓存 + WATCH/MULTI 顺序写 + 4h TTL 家族刷新）或内存后端；大对象入 ref + descriptor 伴生键。
- Durable jobs：`submit_durable_job`（jobs/submit.py:42-184，幂等键 + 原子认领防重复入队）→ worker 侧 `durable_job` 上下文（jobs/worker.py:302-447，看门狗线程推取消事实 + 心跳 + 原子产物提交）。
- SSE 断线续传：`TurnEventBuffer`（event_resume.py，进程内 ring + live-hold F20）。

---

## 2. 优点（已有机制简述，防止后续重复建设）

以下机制均已实现且经测试锁定，后续审计/开发**不要重复建设**，也不要把下面的问题报告成缺陷：

- **循环失控防护已三层**：`max_rounds=60` 轮数上限；连续 3 轮无进展熔断（no_progress，#685，非流式/流式双路径同谓词）；工具级 dedup（同参调用拦截 + 在飞/已完成文案区分 P2-9，失败释放槽位可重试，tool_dispatch_service.py:249-276）。
- **取消语义已分级**：协作式取消 token（CancellationToken）+ 工具波抢占（F28）+ 有界 straggler 等待（`_cancel_and_await`）+ durable job 看门狗（worker.py:212-293）+ "取消≠失败"终态分类（F7/F8）。孤儿 tool_call 修复（F9）+ 断连时已完成工具补落库（audit #817）已双向覆盖。
- **会话/锁生命周期**：per-session turn 锁 + 锁逐出宽限（CONC-F2）、clear_session 的 clearing 标记（进程内 #407 + 跨副本 #750）、背景任务 drain（F15）、内存尾巴裁剪（C-F12）。
- **上下文管理**：6000-token 历史预算按轮分组截断且保底 2 轮；工具 LLM 载荷 2500 字符硬闸（llm_result_formatter `MSG_MAX_CHARS`）；环境时间戳 300s 冻结以保 prompt 前缀缓存（#388）；项目上下文指纹 LRU（1 查询命中）；[最近对话上下文] 与历史窗口去重（design-v3 §4）。
- **错误自愈（Exception-as-Thought）**：`std_error_response` + `correction_hint`；三族失败形状归一（#529/#589 `is_error_like_result`）；可疑空结果尾注提示；TOOL_TIMEOUT 独立归类（#406）；未知参数显式拒绝（audit #828）；TypeError 参数绑定形态分类（audit #846）；tier-3 RCE 工具注册表级拒绝（SEC-F1）。
- **GIS Harness 确定性**：intent resolver 纯规则可回放；protected task 防 hint 降级（#780）；eligibility 复检反"一锤定音"（recipes.py:110-221）；制图挂载失败诚实上报 `cartographic_authoring_failed` 且不打勾（#716）；能力→工具映射与注册表对账（audit #825，`test_capability_registry_parity.py` 锁定）。
- **可观测性**：tool_metrics JSONL 单一 chokepoint（registry.dispatch）、TurnEvidence（turn 级 LLM/context/dedup 计数与诚实结算）、决策日志后台单线程写盘（audit #822）。
- **LLM 客户端**：按 (base_url, loop) 池化 httpx 客户端（keep-alive 复用）；流式 `<think>` 剥离与 tool_call delta 累积；心跳泵不破坏 provider 流（#409）。
- **Pi 桥**：turn 严格串行 + 锁等待期 keepalive（#554）；stale 事件排水；abort 的会话域守卫 + TOCTOU 快照（CONC-F1）；dispatch 结果缓存 rendezvous（ADR-0022）使 SSE 携带服务端 ref 视图。

---

## 3. 发现的问题

> 按严重度排序。P1=功能缺陷或明显性能/正确性损害；P2=显著优化机会；P3=改进项。

### H-1 [P2] 规划阶段无确定性短路：每个新目标回合（含寒暄类消息）固定多付一次串行 LLM 调用

- **问题描述**: legacy 路径的 `_maybe_plan` 对几乎所有非追问消息都会发起一次完整的规划 LLM 调用（`make_plan` → `call_llm`，非流式、120s 超时、与首个 token 串行）。而 GIS Harness 的确定性 resolver（`resolve_map_request_intent` + `RecipeRegistry.select_candidates`）此时已经能给出 task/domains/能力清单——`make_plan` 甚至在 LLM 调用**之后**才附着这份确定性 intent（仅作证据），完全没有用它来合成或短路计划。更极端的是纯寒暄消息（如"你好"：`classify_followup` 无任何关键词命中 → `unclear`，`should_plan` 的旧启发式对无活跃计划的短消息恒返回 True）也会触发一次规划 LLM 调用。
- **影响范围**: legacy 路径每个新目标/首轮对话的首 token 延迟（TTFT）固定增加一次 LLM 往返（典型 2-10s，坏情况 120s 阻塞在 #435 心跳泵内）；token 成本每会话+1。`LLM_PLANNER_MODEL` 默认空串（config.py:82）时用的还是主模型。已知 Pi 路径完全不走这条规划链（#726 裁决），说明该调用并非系统必需。
- **代码位置**:
  - `app/services/chat/execution_engine.py:1149`（非流式）与 `:1597-1606`（流式 planner 等待泵）
  - `app/services/chat/plan_orchestrator.py:449-471`（`make_plan` 无条件 `planner.call_llm`）
  - `app/services/chat/plan_orchestrator.py:260-289`（`should_plan` 无最简门：无域关键词、无活跃计划时仍返回 True）
  - `app/services/chat/plan_orchestrator.py:479-489`（确定性 gis_intent 在 LLM 调用后才附着，未参与规划）
- **原因分析**: design-v3 的 plan-first 设计先于 GIS Harness 落地；harness 建成后规划入口没有回头接入确定性产物。`Plan` 需要的三个字段里：`domains` 可由 `ToolCatalog.detect_domains` ∪ intent.task 映射确定性得出；`steps` 的 goal 文案在 `gis_harness/planner.py:229-243`（`purpose_map`）已有确定性版本；只有低置信请求才真正需要 LLM 语义补充。
- **优化方案**: 在 `AgentPlanOrchestrator.orchestrate_plan`（plan_orchestrator.py:563）加两级确定性短路，LLM 只兜底：
  1. **最简门**：`classify_followup` 为 `unclear` 且 `detect_domains(message)` 为空且消息长度 ≤ 阈值（如 12 字符）→ 直接返回 None（不规划、不调 LLM）。
  2. **harness 合成**：`resolve_map_request_intent(message)` 命中非 `fallback_distribution_default` 规则且 `confidence >= 0.65` 且 `select_candidates` 非空 → 用 recipe 的 `preferred_analysis` + `purpose_map` 直接合成 `Plan`（`set_plan` 照常、`plan.gis_intent/recipe_id` 照常附着），跳过 `call_llm`。低置信/fallback 才走现有 LLM 规划。
  3. 顺带把 `self.max_rounds = 60`（execution_engine.py:334）改为 env 可覆盖，与 `SESSION_CACHE_SIZE` 等既有惯例一致。
- **验证方式**: `pytest tests/unit/test_plan_orchestrator.py tests/unit/test_planner.py tests/test_chat_engine_planning.py -q`；新增用例断言"你好"与"成都小学分布"（高置信）两路径 `planner.call_llm` 调用次数为 0。

### H-2 [P2] legacy 回合无总时长预算：一个退化的回合可持有会话锁以小时计

- **问题描述**: legacy 引擎对回合只有轮数上限（60 轮）与单工具 300s 预算（`TOOL_TIMEOUT_S`），没有整回合墙钟预算。Pi 路径有 `PI_TURN_TOTAL_TIMEOUT=900s`（agent_pi_bridge.py:85）与 180s 停滞预算，legacy 路径完全没有对等物。一个每轮都"有进展"（绕过 no-progress 熔断）但工具缓慢（每轮 60-300s）+ LLM 慢（每轮最长 180s 读超时）的回合，理论上可运行 `60 × (300 + 180) ≈ 8 小时`，期间 per-session turn 锁被持有（execution_engine.py:1550 `async with _AcquiredLock(lock)`），同会话的下一个请求只能在锁上心跳等待。
- **影响范围**: 会话级可用性（后续消息被单回合绑架）；SSE 连接长期占用；服务端资源（会话锁注册表、tracker 任务、背景任务）。真实场景：LLM 对大结果集反复调用不同参数的分析工具（每次参数微调即绕过 dedup、且结果非空即算"进展"）。
- **代码位置**:
  - `app/services/chat/execution_engine.py:1665`（`for round_index in range(self.max_rounds)` —— 循环内无累计时间检查）
  - `app/services/chat/execution_engine.py:334`（`self.max_rounds = 60`，唯一上限，硬编码）
  - 对照：`app/agent_pi_bridge.py:85`（`PI_TURN_TOTAL_TIMEOUT = 900.0`）与 `:100`（`PI_EVENT_STREAM_TIMEOUT = 180.0`）
- **原因分析**: #435/#409 只补了心跳（连接保活），ADR-0052 只补了工具波抢占取消；总预算从未移植到 legacy 循环。心跳让坏回合"活着"而非"被终止"。
- **优化方案**: 在 `chat_stream`/`_chat_locked` 的回合循环开头加累计墙钟检查：`_turn_deadline = time.monotonic() + TURN_TOTAL_TIMEOUT_S`（env 可覆盖，默认对齐 Pi 的 900s），超时则 `fail_task(task.id, "turn total timeout")` + `rt_ev.settle(Outcome.FAILED, failure_class="turn_timeout")` + 发 `plan_finalized`/`task_error`/`done`（流式）或抛 `HonestTurnFailure` 子类（非流式，failure_class="turn_timeout"）。实现位置：execution_engine.py:1665 循环体首行与 :1166 循环体首行各加 3 行。
- **验证方式**: `pytest tests/test_chat_engine.py tests/unit/test_issue685_honest_settle_and_no_progress.py -q`；新增用例用假 LLM/假工具各拖 2s、`TURN_TOTAL_TIMEOUT_S=1` 断言回合在 1s 内以 `turn_timeout` 终止且锁释放。

### H-3 [P2] `/chat/completions` 把 #685 诚实失败一律折叠成 500 "Internal server error"

- **问题描述**: 引擎为非流式路径引入了带 `failure_class` 的语义异常（`EmptyCompletionError`/`MaxRoundsExhaustedError`/`NoProgressError`，execution_engine.py:167-176），流式路径把它们呈现为友好的 `task_error` 文案（"模型返回了空响应，请重试。"、"达到最大轮数"、"连续 N 轮无进展，自动终止"）。但路由层 `chat_completions` 的 legacy 分支用一个 `except Exception` 全部转成 `HTTPException(500, "Internal server error")`——失败分类、可读文案全部丢失，provider 空补全（应提示重试）与真实服务端 bug 对客户端不可区分。
- **影响范围**: 非流式 API 消费者；更实际的是**副作用重复**：max_rounds/no_progress 失败时工具已经执行过（可能建了图层、起了 durable job），客户端看到 500 语义后按"服务器错误"重发整条消息 → 整个工具链重跑（dedup 集合是 per-turn 的，不会跨请求去重）。
- **代码位置**:
  - `app/api/routes/chat.py:716-718`（catch-all → 500）
  - 抛出点：`app/services/chat/execution_engine.py:1417-1420`（NoProgressError）、`:1428-1430`（EmptyCompletionError）、`:1440-1441`（MaxRoundsExhaustedError）
- **原因分析**: #685 在引擎层落地时只改了 `chat()` 的 evidence settle（:1092-1101），路由层从未消费 `HonestTurnFailure` 类型。
- **优化方案**: 在 `chat_completions` legacy 分支增加显式分支（Pi 分支同理可加）：
  ```python
  except HonestTurnFailure as e:
      raise HTTPException(status_code=502, detail={"failure_class": e.failure_class, "message": str(e)})
  except SessionClearingError:
      raise HTTPException(status_code=409, detail="session is being cleared; retry later")
  ```
  （`HonestTurnFailure`/`SessionClearingError` 需从 `app.services.chat.execution_engine` re-export 到 `chat_engine.py` 兼容层。）502 表达"上游/provider 语义失败"而非服务器 bug，客户端可安全按文案重试。
- **验证方式**: `pytest tests/test_chat_api.py tests/unit/test_issue685_honest_settle_and_no_progress.py -q`；新增路由用例：monkeypatch 引擎抛 `MaxRoundsExhaustedError`，断言响应 502 且 detail 含 `failure_class == "max_rounds"`。

### H-4 [P3] legacy 非流式路径缺标题生成与工具决策日志（与流式/Pi 路径不对等）

- **问题描述**: `_chat_locked`（非流式回合体）成功返回前既不触发 `_generate_title`，也从不在工具结果对齐循环里调用 `_log_tool_decision`。标题生成只在流式路径（execution_engine.py:2213）和 Pi 路径（chat.py:867）触发；决策日志（design-v3 §6 的核心可观测性数据，回答"选错工具是检索问题还是区分度问题"）只在流式路径 :1970 记录。
- **影响范围**: 经 `POST /chat/completions` 建立的会话标题永远停在「新对话」（除非用户再走流式路径）；所有非流式回合的工具选择不进 `logs/tool_decisions.jsonl`，该数据集对非流式流量存在系统性盲区（会低估"工具未推给 LLM"类问题）。
- **代码位置**:
  - 标题：`app/services/chat/execution_engine.py:1432-1438`（成功返回块，无 `_generate_title`；对照流式 `:2213`、Pi 路由 `app/api/routes/chat.py:860-869`）
  - 决策日志：`app/services/chat/execution_engine.py:1331-1374`（非流式工具结果循环，无 `_log_tool_decision`；对照流式 `:1970-1976`）
  - 路由未补偿：`app/api/routes/chat.py:700-718`
- **原因分析**: `_chat_locked` 是从 `chat_stream` 拆出的姊妹路径，#376/F9/F28 等修复都刻意做了双路径 parity，但这两处观测面遗漏了。
- **优化方案**: ① 在 `_chat_locked` 成功 return 前（:1438 之前）加 `self._fire_and_forget(self._generate_title, session_id, message)`；② 在 :1357 附近的 `mark_step_done` 成功分支后补 `self._log_tool_decision(session_id, round_index, message, tool_name, tool_args_dict, outcome, len(tools or []), step_n=step_n_matched, failure_class=..., recovery_action=...)`（参数与流式 :1970-1976 对齐；`failure_class` 可在 error 分支由 `self._classify_failure(outcome)` 得出）。
- **验证方式**: `pytest tests/test_chat_engine.py tests/unit/test_chat_helpers.py -q`；新增用例断言非流式成功回合后 `AsyncHistoryService.update_title` 被调用、`logs/tool_decisions.jsonl` 新增对应行。

### H-5 [P3] 规划阶段与 token 流阶段不在抢占式取消覆盖内：取消最长要等 120s 才生效

- **问题描述**: ADR-0052 的抢占式取消只覆盖**工具波**（cancel_watch 与工具任务同池 `asyncio.wait`，execution_engine.py:1883-1899）。规划阶段（`plan_wait` 等待泵 :1600-1613）与 LLM token 流阶段（:1713-1740）都不监听 `task.cancel_token`——用户点取消后，回合要等 planner LLM 返回（非流式 `call_llm` 平顶 120s，llm_client.py:363）或当前 token 流自然结束，才在下一轮循环开头（:1681 `is_cancelled` 检查）生效。非流式路径更早：`_maybe_plan`（:1149）在 `tracker.create`（:1152）**之前**执行，规划期间连可取消的 tracker 任务都还不存在。
- **影响范围**: 取消响应性（规划阶段点击取消，UI 仍转 2-120s）；`clear_session` 的 quiesce（`cancel_inflight_turn` 只 cancel tracker 任务列表）在规划窗口内找不到可点燃对象。
- **代码位置**:
  - 流式规划等待：`app/services/chat/execution_engine.py:1600-1613`（`asyncio.wait({plan_wait})` 未加入 cancel token wait）
  - 非流式时序：`app/services/chat/execution_engine.py:1149`（`_maybe_plan`）先于 `:1152`（`tracker.create`）
  - token 流阶段无取消检查：`app/services/chat/execution_engine.py:1713-1740`
  - 对照（工具波的完整覆盖）：`app/services/chat/execution_engine.py:1883-1899`
- **原因分析**: #435 给规划阶段加的是心跳（保活），没有把 cancel token 一并纳入 wait 集；F28 只改了工具波。
- **优化方案**: ① 流式：若 `task.cancel_token is not None`，把 `task.cancel_token.wait()` 任务加入规划等待循环的 wait 集（与 :1893 同款），命中即 `plan_wait.cancel()` 并走既有取消收尾；② 非流式：把 `task = self.tracker.create(...)` 移到 `_maybe_plan` 之前（两行换位，`task` 在 `_maybe_plan` 内无使用），并在 `_chat_locked` 循环前的规划 await 处包一层 `asyncio.wait` 同款检查。token 流阶段可在 keepalive 分支（`_stream_with_token_keepalive` 的 timeout 路径）顺带检查 `is_cancelled` 并主动退出泵（可选，影响小）。
- **验证方式**: `pytest tests/test_chat_engine.py tests/unit/test_runtime_concurrency_round2.py -q`；新增用例：fake planner 挂 5s，发起回合后立即 `tracker.cancel`，断言回合在 <1s 内以 cancelled 终态收尾（而非等满 5s）。

### H-6 [P3] 流式路径空补全判定未 strip：纯空白补全会被当成功收尾

- **问题描述**: 空补全守卫（CORRECTNESS-4）在两条路径用了不同谓词：非流式是 `if not (content or "").strip() and not tc_list`（execution_engine.py:1428），流式是 `if not content and not tc_list`（:2182）。provider 返回纯空白 content（"  \n" 等，推理型模型收尾时偶发）时，流式路径会把它当有效答案：保存一条空白 assistant 消息、发 `content streaming_done` + `task_complete`（summary 为空白）——正是 CORRECTNESS-4 要消灭的"空气泡报成功"。
- **影响范围**: 流式路径的空响应体验回归（客户端看到成功终止的空白回复，且不会重试）；DB 里落一条空白 assistant 行。
- **代码位置**: `app/services/chat/execution_engine.py:2182`（流式，未 strip）对照 `:1428`（非流式，已 strip）。
- **原因分析**: CORRECTNESS-4 移植到流式路径时谓词少抄了 `.strip()`。
- **优化方案**: 把 :2182 改为与 :1428 完全一致的 `if not (content or "").strip() and not tc_list:`。同时建议把该谓词提为模块级小函数（如 `_is_empty_completion(content, tc_list)`）供两路径共用，杜绝再次漂移。
- **验证方式**: `pytest tests/test_chat_engine.py -q`；新增用例：fake LLM 流返回仅含 `"  \n"` 的 done 事件，断言收到 `task_error`（"模型返回了空响应"）而非 `task_complete`。

### H-7 [P3] Pi 路径失败/中断回合不落库 user 消息：历史记录丢失用户输入

- **问题描述**: Pi 流式路径的持久化回调 `_persist_pi_transcript` 以 `if not result.get("completed"): return` 开头——失败、停滞、中断（断连 abort）的回合**连 user 消息都不保存**。legacy 路径在回合一开始就保存 user 消息（execution_engine.py:1147、:1562），失败回合至少保留用户问了什么。刷新页面后（历史以 DB 为准），Pi 路径失败回合的用户消息直接消失。
- **影响范围**: `USE_NEW_AGENT=1` 部署下所有非成功回合的对话历史完整性；审计/回放（无法事后知道失败回合的输入）。
- **代码位置**: `app/api/routes/chat.py:848-849`（`if not result.get("completed"): return`）；对照 legacy `app/services/chat/execution_engine.py:1561-1562`。
- **原因分析**: audit #818 的目标是"与 legacy 持久化对等"，实现时把 user/assistant 两条写入绑在同一个 `completed` 门后，而 legacy 的语义是"user 即存、assistant 完成才存"。
- **优化方案**: 把 user 消息持久化从 `completed` 门里拆出来——在 `pi_event_generator` 注册 buffer 后立即 best-effort 保存（或在 `on_turn_result` 回调中无论 completed 与否先保存 user 消息，仅 assistant 文本与标题仍受 completed 门约束）。注意保持幂等：同一 turn 重放（断线续传不会重发 prompt，DUP-1 已保证）不会重复保存。
- **验证方式**: `pytest tests/unit/test_pi_dispatch_adapters.py tests/test_chat_api.py -q`；新增用例：fake bridge 以 `completed=False` 结束回合，断言 DB 中存在该 user 消息且无 assistant 消息。

### H-8 [P3] harness 前门工具的域标注窄于其任务族：proximity/accessibility 类请求看不到 `webgis_map_intent`

- **问题描述**: `webgis_map_intent`/`webgis_map_product` 注册为 `tier=2, domains=["statistics", "report"]`（gis_harness/tools.py:121、:218），只有用户消息命中 statistics/report 关键词才会进入 LLM 工具目录。但意图解析器支持的任务族远宽于此：`proximity_analysis`（"距离学校500米以内"）、`accessibility_analysis`（"等时圈/服务区"）、`change_detection`（"两期对比"）均有专属 recipe 与产品模板。以"距离学校500米以内的地铁站"为例：只命中 network 域关键词（"地铁"），statistics/report 均未激活 → harness 前门工具不可见，LLM 直接走 `buffer_analysis`（tier-1）裸工具路径，proximity 产品族（缓冲面+落点+统计面板组件的组装）与 harness evidence 全部缺席——与工具描述（"任何…周边/报告配图类请求的第一步"）和 PLANNER_PROMPT 的指引（plan_orchestrator.py:99-104）不符。
- **影响范围**: 邻近/可达性/变化检测类请求的产品质量（无组件化产品输出）与 harness 可观测性盲区；decision_log 中这些域的 from_plan/recipe 证据缺失。
- **代码位置**:
  - `app/services/gis_harness/tools.py:121`、`:218`（domains 标注）
  - `app/services/tool_catalog.py:30-113`（DOMAIN_KEYWORDS：proximity/accessibility 类问法只落 network/temporal 域）
  - `app/services/gis_harness/intent.py:24-35`（TaskType 全族）与 `app/services/gis_harness/recipes.py:345-373`（proximity/accessibility recipe）
- **原因分析**: harness 工具落地时沿用了 statistics/report 的旧标注；后续 #715 只补了 statistics 域的行政计数词，没有随 intent 任务族扩展同步域标注。
- **优化方案**: 将两工具的 domains 扩为 `["statistics", "report", "network", "temporal"]`（proximity 主要落 network 词族、change_detection 落 temporal 词族），或在 `ToolCatalog.select_schemas` 给"意图前门"类工具单独的 always-on 待遇（tier=1 但描述已足够具体，149 工具下多 2 个 schema 的 token 成本可忽略）。改完后用 ToolChoiceAccuracy 决策日志验证周边类请求的 recipe 命中率。
- **验证方式**: `pytest tests/unit/test_tool_catalog.py tests/unit/test_tool_catalog_sticky_684.py tests/unit/gis_harness/test_harness_tools.py -q`；新增用例断言"距离学校500米以内的地铁站"与"等时圈分析"能选中 `webgis_map_intent` schema。

### H-9 [P3] `webgis_map_product` 重建计划时不传 `available_tools`，evidence 里的 candidates 二次选择丢 `project_verified`

- **问题描述**: 产品阶段用 `planner.plan_from_intent(intent, template_id=..., recipe_id=...)` 重建计划时**没有**传 `available_tools`（gis_harness/tools.py:265-269），而意图阶段传了（:189，audit #825 的"诚实报告 unavailable 能力"承诺）。结果是：产品阶段重建的 `data_requirements/analysis_steps` 里解析不到工具的能力状态退回 "pending"（意图阶段是 "unavailable"），两阶段 evidence 自相矛盾。同一函数里 `map_product_evidence.recipe_selection.candidates` 用 `planner.recipes.select_candidates(intent)`（:651）**不带** `project_verified` 再选一次——而实际推荐排序在 :180-182 是带的（ADR-0069 项目记忆），#723 注释声称"记录确定性 selector 实际考虑过的候选"，实际记录的排序依据与真实选择不一致。
- **影响范围**: harness evidence 保真度：recipe_outcome 审计、项目记忆成效分析、completeness 归因会读到与实际决策不同的候选序与能力状态；对 LLM 无直接影响（不改变执行）。
- **代码位置**:
  - `app/services/gis_harness/tools.py:265-269`（产品阶段 `plan_from_intent` 缺 `available_tools`；对照 `:189`）
  - `app/services/gis_harness/tools.py:649-652`（evidence 二次 `select_candidates(intent)` 缺 `project_verified`；对照 `:180-182`）
- **原因分析**: 产品组装路径是后加的（#784 绑定流），重建计划时只关心 recipe/template 连续性，未复用意图阶段的注册表/记忆参数；#723 修复时复制了调用但没复制参数。
- **优化方案**: 在 `webgis_map_product` 开头与意图阶段同款取 `available = set(registry.list_tools())`（容错降级空集）与 `verified = await _project_verified_recipes()`，传给 `plan_from_intent(..., available_tools=available or None)`；evidence 处改为 `planner.recipes.select_candidates(intent, project_verified=verified)`（一次取数两处复用，不增加查询）。
- **验证方式**: `pytest tests/unit/gis_harness/test_harness_tools.py tests/unit/gis_harness/test_planner.py -q`；新增用例：注册表里摘掉 `h3_binning`/`fishnet_grid` 后调 `webgis_map_product`，断言 evidence 中 grid_binning 能力为 `unavailable` 且与 `webgis_map_intent` 阶段一致；带 project_verified 的排序用例断言 evidence candidates 首位是项目已验证 recipe。

---

## 附：审查覆盖与未覆盖说明

- 逐行精读：execution_engine.py（2487 行全文）、plan_orchestrator.py、context_assembler.py、context_builder.py、history_compression.py、llm_client.py、tool_pipeline.py、planner.py（chat）、pi_event_mapper.py、event_resume.py、gis_harness 全部 6 文件、agent_pi_bridge.py（2243 行全文）、tools/registry.py、tools/__init__.py、session_data.py、session_data_redis.py、jobs/worker.py、jobs/submit.py、api/routes/chat.py、tool_catalog.py、tool_dispatch_service.py、planning/followup.py、decision_log.py、session_overview.py、components.py。
- 抽查（结构 + 热路径）：pi_rpc_client.py、jobs/store.py、task_tracker.py、mapspec store（get/save）、verdict_summary.py、product_templates.py。
- 已核对既有防护后**排除**的伪问题（勿重复上报）：大工具载荷进上下文（MSG_MAX_CHARS=2500 硬闸已覆盖）、`_TOKEN_ESTIMATE_MEMO` hash 碰撞（8192 上限 + 概率可忽略）、`get_mapspec` 每轮成本（map_state L1 + sidecar 已覆盖）、非流式 map_state 锁外写入时序（seq 门控已防乱序）、`_completed_keys` 跨会话增长（4096 清空）、`LRUCache` 线程安全（RLock）、`drain_background_tasks` 跨 loop 粘连（已修）、`_evict_idle_locks` 逐出顺序（in-use/宽限双守卫，正确性无损）。
