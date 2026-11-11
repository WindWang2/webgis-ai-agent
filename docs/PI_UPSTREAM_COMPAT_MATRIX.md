# Pi 上游兼容矩阵（PI_UPSTREAM_COMPAT_MATRIX）

> G01-PI-NATIVE-RUNTIME / PI-10；G12 更新（ADR-0288）。当前 pin：**v1.5.1**
> （gitlink `dd30101c`，上游 `earendil-works/pi`，原 `badlogic/pi-mono`）。
> 纪律：**不擅自升级**——升级需先更新本矩阵 + contract tests，走 feature-gated
> adapter（ADR-0282 语义）。

## 1. 依赖的 v1.5.1 契约面（本仓库消费的 API/协议）

| # | 上游契约 | 本仓库消费点 | 契约测试 |
|---|---|---|---|
| C1 | RPC wire 协议（request/response + 事件流，newline-delimited JSON） | `pi_rpc_client.py`（binary pipes、`_readline_bounded`） | `tests/integration/test_pi_vendor_contract.py`（活体） |
| C2 | 事件词表：`agent_start/turn_start/message_start/message_end/message_update/tool_execution_start/tool_execution_end/turn_end/agent_end/agent_settled` | `pi_event_mapper.py`（`agent_settled` 唯一终判；`agent_end.willRetry` 非终） | pi_doctor rpc 域（live 验证）+ `test_pi_event_mapper.py` |
| C3 | `prompt` 响应 `disposition` 字段（`handled` = input handler 消费，无 agent run） | `agent_pi_bridge`（send_failed 族诚实结算） | `test_pi_status_fail_closed.py` |
| C4 | delta-only `message_update` | `pi_event_mapper._extract_text_from_event`（快照语义 #816） | `test_pi_event_mapper.py` |
| C5 | pi-schema 参数校验（vendor 按注册 schema 校验工具调用参数） | `pi_native_surface` dump（`additionalProperties:false` 投影） | `test_pi_schema_coercion_golden.py`（真实 rpc-entry golden） |
| C6 | 扩展两段加载：`load()` 只注册、`session_start` 才可 `setActiveTools` | `index.mjs:260-291, 371-379` | pi_doctor extension 域 + `test_pi_extension_control_boundary.py` |
| C7 | `registerTool`（name/label/description/parameters/promptSnippet） | dump v2/v3/v4 形状 | `test_pi_dynamic_surface.py` |
| C8 | `--no-builtin-tools` / `--no-session` / `--mode rpc` / `--extension` CLI | `pi_rpc_client._spawn_process` | pi_doctor build/env 域 |
| C9 | `set_model` RPC（provider/modelId 需已注册） | `pi_rpc_client.start`（可选） | pi_doctor rpc 域（环境内） |
| C10 | abort RPC（会话路由、pending tool 快照语义） | `PiBridge.abort`（CONC-F1 快照） | `test_pi_cancellation_unified.py` |
| C11 | `--no-builtin-tools` 只清默认**工具**选择（`defaultToolNames=[]`，sdk.ts:282），内置**扩展**（tool-search/codemode/mcp）仍加载，以 `defaultActive:false` + `exposure:"model-only"` 注册，由宿主 `setActiveTools` 激活；`-ne/--no-extensions` 才卸载 | `index.mjs` 常驻激活集（PI_NATIVE_DISCOVERY） | vendor contract「无内置编码工具可达」+ discovery 旅程 |
| C12 | `tool_search`（BM25 + `Intl.Segmenter` 分词 + 本地 `$ref/$defs` 解析进检索文本 #11803）只搜 `exposure ∈ {codemode,deferred}` 且未激活者，命中 **merge** 进 active 集（`[...active, ...matches]` → 前门不丢） | `index.mjs`（发现入口）、dump v4 deferred 长尾 | A2/A3 旅程 + 契约测（中文/$ref 命中） |
| C13 | 嵌套调用（`ctx.executeTool`）：`tool_execution_start/update/end` 事件带 `parentToolCallId`（id `<callerId>/<n>`），结果归父工具 `nestedCalls`（256/8KB/32KB/500c 上限）；codemode=QuickJS 沙箱（无 Node/fs/network/timer），并发 4、内存 256MiB、worker 2、输出 10k tokens | `pi_event_mapper`（child 投影）、dispatch 缓存按嵌套 id 会合 | A4 旅程 + mapper 单测 |
| C14 | `clear_queue` RPC（Esc 语义：先清 steer/follow-up 队列再 abort）；abort 等 idle 后响应；停机：首 SIGTERM/SIGHUP 有序清理（dispose 等 tool settlement + credential writes，可数秒），第二信号立即强退，EOF 后信号不中断清理 | `PiBridge.abort`（用户级完全停止）、`PiRpcClient.stop`（EOF→有界等待→terminate→kill） | W07 单测 + chaos 回归 |
| C15 | `registerTool` 原生 exposure/namespace/annotations/reachesTools 字段（`ToolExposure = direct|model-only|codemode|deferred|hidden`；annotations 为 MCP 语义提示，**不是授权事实**） | dump v4 + `index.mjs` 注册透传 | `test_pi_dynamic_surface.py` v4 用例 |
| C16 | v1.5.1 根 `package-lock.json` 存在（v1.5.0 无）→ `build-vendor-pi.sh` 走 `npm ci`（可复现）；`jiti` 仍 `2.7.0`（bundle 加载锁版不变） | `scripts/build-vendor-pi.sh` | 构建产物 + 隔离包旅程 |

## 2. 引擎/环境下限

- node ≥ 22.19（vendor engines；本仓 pi_doctor build 域检查）— v1.5.0→v1.5.1→v1.5.2 不变
- Python 3.13（`pi_rpc_client` 类型注解 `X | None` 等）
- 上游 floor：`PI_MIN_VENDOR_TAG = v1.5.1`（`pi_compat`；floor 违反 → WARN +
  pi_doctor vendor 域 FAIL）

## 3. 升级评估清单（未来版本 X）

1. `git submodule` 切新 tag → `bash scripts/build-vendor-pi.sh`。
2. `python scripts/pi_doctor.py`（rpc 域 live 验证事件词表与 `agent_settled`
   终判是否漂移）。
3. `python -m pytest tests/integration/test_pi_vendor_contract.py
   tests/integration/test_pi_schema_coercion_golden.py`（wire 契约 + 强转 golden）。
4. `PI_JOURNEY_E2E=1 ... test_pi_stub_llm_journeys.py`（全栈旅程）。
5. 对照 C1–C16 逐项复核：事件词表增删、`disposition` 语义、扩展加载阶段、
   registerTool 形状、CLI flags、abort/clear_queue 快照、发现 merge 语义、
   嵌套事件形状、内置扩展激活面。
6. **禁止**：在新版发布证据（tag/release notes + 上述测试绿）之前引入未发布
   API；禁止跳过矩阵直接升 pin。

## 4. v1.5.0 → v1.5.1 差异裁决（ADR-0288，2026-11-10）

四栏口径（已消费协议 / 新能力 / 破坏性变化 / 项目策略）：

- **已消费协议零漂移**：`packages/coding-agent/docs/rpc-commands.md`、`json.md`、
  `rpc-extension-ui.md` 与 v1.5.0 **逐字节一致**；C1–C10 复核全部保持。
- **新能力（本仓消费）**：C11–C16（发现/嵌套/队列停机/原生 exposure/lockfile）。
  另有 OpenAI-compat 空 `tools:[]` 省略（#11843，本仓 provider 走 openai-completions
  且常驻有工具，不受影响）、MCP 大消息取消/边界修复（本仓未启 MCP）、
  `AgentSession.reload()` 流中抛错（本仓无 reload RPC 消费）。
- **破坏性变化（评估不触及本仓消费面）**：grep `readFile` 钩子签名变更（本仓
  无 grep ops 覆写）；Anthropic message threads 默认开（本仓不用 anthropic 官方
  端点；models.json `compat` 已显式声明所需开关）。
- **v1.5.2 评估**（发布于快照后 ~5h、任务启动前 ~1.5h）：coding-agent 的
  RPC/扩展/发现/嵌套/停机面与 v1.5.1 **零 diff**（仅 anthropic 版本串、
  missing-cwd fork UX、失败导入清理、docs/lockfile）。无本任务所需能力且无
  浸泡 → 不升；后续升 v1.5.2 = pin-only 变更（重新跑本节清单即可）。
- **回退**：gitlink 回 v1.5.0 + 重跑 build-vendor（幂等）**并**回退
  `pi_compat.PI_MIN_VENDOR_TAG` 至 v1.5.0（floor 是协议下限声明，回退 pin 不
  回退 floor 会让 v1.5.0 被 doctor 判 FAIL）；G12 新行为用 env kill-switch
  关闭（`PI_NATIVE_DISCOVERY=0`、`PI_SURFACE_SCOPE_ENFORCE=0`、
  `PI_GRACEFUL_STOP_TIMEOUT_S` 按需），即可恢复 v1.5.0 行为。config 代际换代
  无独立开关（fail-open 无锚不触发），rebuild 域无 mapspec 即 skip。

## 5. 版本历史

| pin | 引入 | 备注 |
|---|---|---|
| v0.81.1+1 | PR #1973 前 | 旧基线 |
| v1.5.0 | PR #1973（ADR-0282） | 两段加载/prompt disposition/pi-schema 校验等契约面以此为准 |
| **v1.5.1**（当前） | G12（ADR-0288） | 原生工具发现/嵌套调用/有序停机契约面（C11–C16）以此为准 |
