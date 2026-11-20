# G37-SESSION-MODEL-RUNTIME · BASELINE

TASK_ID = `G37-SESSION-MODEL-RUNTIME` / TASK_SLUG = `g37-session-model-runtime`

## 执行时快照（2026-11-14，北京时间）

| 项 | 实测值 |
|---|---|
| remote / nameWithOwner / default branch | `https://github.com/WindWang2/webgis-ai-agent.git` / `WindWang2/webgis-ai-agent` / `master` |
| 主工作区 HEAD（只读，不清空） | `816705218a5aa6202bf15d745cb31cddb3437f44`（origin/master 祖先；未提交 `M vendor/pi` + `.claude/skills/*` 一律保留） |
| base SHA（fetch 后） | `1276692737a8cf58d0604fb40ae0e70fc61779a2` = 研究快照 SHA |
| open PR | **0**（查询成功，非失败） |
| 最近 closed PR | 读取 60 条；#2094–#2100 = G29–G35，#2101–#2116 = R1 修复，全部已合并 |
| #2094–#2116 GitHub reviews / inline / conversation | 研究快照断言为空；独立评审在 `planning/G2x-…/REVIEW.md`（已实读 G22/G29/G28/G35） |
| feature branch / worktree | `zcode/g37-session-model-runtime-20261114T151704Z` / `webgis-worktrees/g37-session-model-runtime-20261114T151704Z` |
| Pi gitlink（origin/master） | `b4afdddf44e059c67ccf5097e5317d913521b470` = v1.5.2（G22 #2093 已对齐；不作为新成果） |
| Pi worktree 初始化 | 网络 submodule clone 超时（G35 同款 HTTP/2 问题）→ 离线 `git clone --shared .g35-pi-gitdir` @ pin + 硬链复制 G35 已构建 `node_modules`(717M)/`dist`(22M)；`dist/bundle/rpc-entry.js` 存在 |
| 未改 vendor gitlink / .gitmodules | 是（`git status` 佐证） |
| 主机 | 16 核 / 62GiB；node 存在；项目 venv `/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python`（3.13.17，fastapi 0.141.1） |
| Goal 预算 | 任务书要求 ≥320,000,000 tokens 持续工程预算；宿主未暴露可查询的 usage-budget 接口 → 如实记录「未配置/未核验」（与 G35 PR_HANDOFF 同款诚实披露） |

## 生产基线（origin/master 实读，关键事实）

- **配置单一解析点**：`app/services/chat/model_config.py` — `resolve_llm_config()`（Settings ⊕ 运行时 override），`config_generation()` sha16 指纹（#2061 / ADR-0288 W08）。
- **描述符**：`model_runtime/descriptors.py` — `ModelDescriptorRegistry`（defaults/env/file/override），能力保守 unknown；**描述符注册 ≠ 使用授权**（WP02 红线）。
- **spawn**：`pi_rpc_client._spawn_process` 写 `PI_AGENT_DIR/models.json`（单模型 webgis/<base>，`compat.supportsReasoningEffort=False`，model `reasoning:true`）+ `settings.json` default；`PI_PROVIDER/PI_MODEL` env 启动期尽力 set_model（失败仅日志）。
- **turn 流**：`agent_pi_bridge.stream_prompt`：lease(单 writer) → register turn → kernel begin_turn → drain stale → bind_turn_prompt → **`_isolate_vendor_session`（换 GIS session 时 new_session）→ `request("prompt")`**。模型应用必须插在 isolate 与 prompt 之间。
- **配置换代**：`recycle_if_config_stale`（chat.py:758 每个回合边界调用；在飞 turn 延迟）。保留不动。
- **事件面**：`pi_event_mapper` 消费 message_update/tool_execution_*/agent_end/agent_settled/auto_retry_*/compaction_*/G29 UI 事件；**message_end / usage / get_session_stats / thinking_level_changed 全仓 0 消费者**（实 grep 佐证）。
- **governor**：`session_budget.SessionBudgetLedger`（turn⊂goal⊂session⊂global；live/cumulative 维度；`record_context_tokens`）；LLM 价格先验 `estimation._LLM_USD_PER_1K_TOKENS=0.002` provisional；#2060 有界性问题归 R1。
- **会话持久**：`Conversation`（owner = user_id / owner_token 摘要）+ `Message`；会话路由守卫 `require_owned_session` / `_guard_body_session`。
- **工具面**：`ToolRegistry`（`app/tools/__init__.py` 模块表装配）；Pi 侧冻结 `NATIVE_TOOL_NAMES`（default_active，G35 红线不可加）+ 动态注册超集经 `webgis_execute` 派发；`session_id` 由 dispatch 注入（G35）。
- **前端**：`llm-config.tsx` 只读展示全局配置（admin）；会话内无任何模型选择/用量 UI。
- **既有 Pi 旅程车道**：`tests/integration/test_pi_stub_llm_journeys.py`（node stub LLM + uvicorn 生产 app + 真实 vendor bridge + VERDICT json）；`test_pi_model_config_generation.py`（#2061）。

## Pi pin b4afdddf 关键契约（vendor 实读，出处见 INTERFACES.md）

- RPC 命令：`get_state`→`{model?, thinkingLevel, isStreaming, pendingMessageCount,…}`；`set_model{provider,modelId}`→`data:Model` 或 error `Model not found`（auth 失败 throw→error response）；`get_available_models`；`set_thinking_level{level}`→**success 无 data（实际级别不回执，必须 get_state 回读）**；`get_available_thinking_levels`；`get_session_stats`→快照聚合（usage/compaction/branch_summary/toolResult 全量）。
- `message_end` 先于 `appendMessage`（无 entryId 可读）；终态 usage/responseModel/stopReason 在事件本身。`model_select` **不在 RPC stdout**（仅 extension runner）。`thinking_level_changed` 是 stdout 事件但仅级别真变时发。
- usage 语义：reasoning⊂output；cacheWrite1h⊂cacheWrite；total=input+output+cacheRead+cacheWrite；`cost.totalFromProvider?=bool`；ToolResultMessage.usage=nested（不得与父重复计）。
- `before_provider_request` handler throw 被捕获继续 → **不能用作硬预算门**；admission 必须宿主侧 prompt 字节发出前。
- `setModel`（RPC 路径）只作用当前 Pi session（无 persist）；new_session 后模型回到 settings.json default。

## 必须保留的既有不变量（G22/G29/G35 实读）

- stdin 全部写经 `_stdin_write_lock`；控制面 RPC 经 `_control_gate` + `_ActiveTurnEntry` 同一性交接（G22-P1）。
- turn 单 writer（`pi_writer_gate`）+ turn token；`agent_settled` 是唯一 turn 终结。
- `default_active == NATIVE_TOOL_NAMES`（G35 R3：往 default_active 加工具曾破坏 4 个契约测试并完全回滚）。
- `config_generation` 换代回收语义（#2061）；在飞 turn 不中途换配置。
- deferred 工具发现路径（`list_available_tools` → `webgis_execute`）。
