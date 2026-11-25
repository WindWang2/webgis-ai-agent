# Pi 上游兼容矩阵（PI_UPSTREAM_COMPAT_MATRIX）

> G01-PI-NATIVE-RUNTIME / PI-10；G12 更新（ADR-0288）；G22 升级 v1.5.2；
> goal-pi-native-gis-host-20261125 升级 v1.6.1（ADR-0301）。
> 当前 pin：**v1.6.1**（gitlink `ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe`，
> 上游 `earendil-works/pi`，原 `badlogic/pi-mono`；lightweight tag，
> published 2026-11-25T13:41:35Z）。协议 floor 仍 **v1.5.1**
> （`PI_MIN_VENDOR_TAG`——floor 是「已消费协议的最低下限」声明，pin/floor
> 解耦：floor 不盲随 pin 上移，旧 checkout 仍可经 PI_VENDOR_DIR 开发
> override 使用且身份可追溯）。
> 能力判定：**按 feature 而非版本号**——`pi_compat.PiCapabilitySnapshot`
> （typed、封闭词表、唯一 feature→min-version 映射点）；本矩阵 §4.5 记录
> v1.5.2→v1.6.1 差异裁决。gitlink 形态守卫：树条目 mode 必须 160000
> （#2169 symlink 回归类 → doctor FAIL，§4.5 C17）。
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
- **v1.5.2 正式裁决（G22，2026-11-11 升级）**：按 §3 清单全量实跑后升级。
  四栏：
  - 已消费协议（C1–C16）：**零漂移**——`rpc-commands.md`/`json.md`/
    `rpc-extension-ui.md` 与 v1.5.1 逐字节一致（本机 diff 核实）；变更
    全部落在 interactive-mode（missing-cwd 的 Continue/Fork/Cancel）、
    `/import` 失败副本清理（`agent-session-runtime.ts`）、Anthropic OAuth
    版本串、docs/examples/lockfile。`--mode rpc` 消费面（含 missing-cwd
    非 interactive 分支）行为原样。
  - 新增可用能力：无本仓消费（fork-into-cwd 是 interactive UX）。
  - 破坏性变化：无触及本仓消费面。
  - 项目采纳：**升级 pin v1.5.1 → v1.5.2**（tag `b4afdddf`），pin-only
    变更。证据：pi_doctor 六域 PASS（428 tools/native 7/7/事件词表全量）、
    `test_pi_vendor_contract.py` + `test_pi_schema_coercion_golden.py`
    13 passed、`PI_JOURNEY_E2E=1` 旅程（见 TEST_EVIDENCE.md）；构建产物
    经 `build-vendor-pi.sh --reinstall`（root lockfile `npm ci`）来自目标
    SHA（doctor vendor 域 pin 核验）。
- **回退**：gitlink 回 v1.5.0 + 重跑 build-vendor（幂等）**并**回退
  `pi_compat.PI_MIN_VENDOR_TAG` 至 v1.5.0（floor 是协议下限声明，回退 pin 不
  回退 floor 会让 v1.5.0 被 doctor 判 FAIL）；G12 新行为用 env kill-switch
  关闭（`PI_NATIVE_DISCOVERY=0`、`PI_SURFACE_SCOPE_ENFORCE=0`、
  `PI_GRACEFUL_STOP_TIMEOUT_S` 按需），即可恢复 v1.5.0 行为。config 代际换代
  无独立开关（fail-open 无锚不触发），rebuild 域无 mapspec 即 skip。
  v1.5.2 → v1.5.1 回退 = gitlink 回 `dd30101c` + `build-vendor-pi.sh
  --reinstall` + doctor/契约复跑（floor 不动，v1.5.1 仍满足下限；G22-P7
  已演练，见 TEST_EVIDENCE.md）。


## 4.5 v1.5.2 → v1.6.1 差异裁决（ADR-0301，2026-11-26）

先修供应链再升 pin：master 的 `vendor/pi` 在 merge `1b3f9e415`（PR #2169，
2026-11-16）被误改成本机悬空 symlink（mode 120000 blob `dd645f502`），
本波次恢复 gitlink 形态并直升 v1.6.1。四栏口径：

- **已消费协议（C1–C16）：零漂移**——`json.md`/`rpc.md`/`rpc-extension-ui.md`
  在 v1.5.1→v1.6.1 间**逐字节一致**（本机 `git diff` 核实）；
  `rpc-commands.md` 仅两处文档级变更，且本仓零消费：
  ① `bash` 响应加性 `fullOutputError`（本仓不经 RPC bash 命令）；
  ② `get_last_assistant_text` 语义澄清——跳过 aborted/failed turn 或仅工具
  调用的无文本消息（本仓无该命令消费点，`rg` 零命中）。
- **新能力（本仓消费，C17–C24）**：见下表。
- **破坏性变化（评估不触及本仓消费面）**：① v1.6.0 扩展组件 `render()`
  返回 `readonly string[]`——`app/extensions/webgis-tools/index.mjs` 不实现
  自定义组件渲染，不受影响；② v1.6.1 `notices.*` 设置替换
  `showCacheMissNotices`——本仓 settings.json 投影不写该键，无迁移面。
- **项目采纳**：**升级 pin v1.5.2 → v1.6.1**（tag `ef4a1cfc`）。证据：
  pi_doctor 六域 PASS（529 tools / native 7/7 / deferred 522 / 事件词表全量 /
  真实 rpc-entry handshake-turn-settled live 探测）；`npm ci`（root lockfile
  `f05391f4…`）；build manifest 对账 consistent；能力快照 14/14
  （`entry_probe@1.6.1`）。非字节级可复现已知限制：上游 esbuild 产物含
  内容寻址 chunk hash，重建换 hash（manifest entry_drift 观测性 WARN，
  pin 对账不受影响）。

### 新增契约面（C17–C24）

| # | 上游契约（版本） | 本仓库消费点 | 契约测试 |
|---|---|---|---|
| C17 | vendor/pi git 树形态必须是 gitlink（mode 160000）；120000 symlink = 供应链回归（#2169 实证），pin 判定不得静默降级 | `pi_compat._expected_pin`（type 必须 `commit`，symlink blob 40-hex 不再误判）、`pi_doctor` vendor 域 FAIL、identity `vendor_tree_not_gitlink` | `tests/unit/test_pi_vendor_capability_snapshot.py`（临时 git 仓 fixture） |
| C18 | 构建清单 `.build-manifest.json`（pin/node/lock/entry sha16；位于子模块工作树内、超项目索引不可见）；状态机 absent/consistent/manifest_only/pin_mismatch/entry_drift/unparsed | `build-vendor-pi.sh` 写入；Dockerfile(.prod) pi-builder 经 `PI_VENDOR_PIN` build-arg 写入；`pi_compat._manifest_facts` 对账；doctor build/vendor 域消费 | 同上（manifest 状态机用例） |
| C19 | 入口自证版本：`node <rpc-entry> --version`（mode 分发前处理，v1.5.1 即支持）；identity `observed_version`（`entry_probe` 证据） | `pi_rpc_client.probe_pi_entry_version`（唯一 spawn 点，10s 上限）；`pi_compat.pi_vendor_identity` 汇聚 | 同上（live 本 checkout 断言 1.6.1） |
| C20 | typed 能力快照：feature→min-version 唯一映射（14 项封闭词表）；未知版本一律 unsupported 不谎报；消费方查 `caps.has()` 禁止散落版本比较 | `pi_compat.PiCapabilitySnapshot`/`capabilities_for_version`；v1.6.0 组=extension_error/abort_batch_terminal/session_lifecycle_fix/unreported_diagnostics，v1.6.1 组=provider_failure_events/decision_model_classify/rpc_line_scan_fix | 同上（边界用例 v1.5.1/v1.6.0/v1.6.1/unknown/prerelease） |
| C21 | v1.6.0 RPC 会话生命周期修复：`new_session`/`switch_session`/`fork` 的扩展 `session_start` 单跑 + `entry_appended` 事件转发 RPC 客户端；Esc 中止批量工具未启动项获 "Operation aborted" 终态结果 | 事件映射按封闭词表处理（桥无需行为变化）；能力位 `RPC_SESSION_LIFECYCLE_FIX`/`ABORT_BATCH_TERMINAL` 记录 | vendor contract 车道（真 rpc-entry）+ 能力快照测试 |
| C22 | v1.6.1 provider 可观测性：`after_provider_response` 覆盖 HTTP 4xx/5xx 与每次重试尝试（WP5 挂点）；codemode `models.classify()` + `responseId`（默认不启用，能力门控） | 能力位 `PROVIDER_FAILURE_EVENTS`/`DECISION_MODEL_CLASSIFY`；WP5 把失败/重试事件接入 typed usage/diagnostic ledger（不记录请求全文/密钥） | 能力快照测试 + WP5 治理测试 |
| C23 | 手动 `compact` wire 契约（v1.6.1 实测）：`compaction_start(reason="manual")` → `compaction_end`（result 含 summary/tokensBefore/estimatedTokensAfter；失败/中止时 result 缺席 + errorMessage/aborted）→ response(success, data=result)；**keepRecentTokens(20k) 从尾部保留——单条超限消息整条进保留区会诚实拒绝 "session too small"** | `tests/integration/test_pi_vendor_contract.py::test_manual_compaction_contract_and_post_compaction_usability`（真实 rpc-entry：N 个中等 turn 累积历史 → compact → 压缩后回话仍 settle → get_state 一致） | 同左（C23 专测） |
| C24 | provider 失败/重试可观测（v1.6.1）：`auto_retry_start/end` wire 事件 + `after_provider_response` 扩展事件覆盖 HTTP 4xx/5xx 与每次重试（含 `retry-after`）→ 扩展有界 stderr 行 `provider_response status=<n> retry_after=<n|->`（仅整数化 status/retry-after，不透传其他 header/请求全文）→ 宿主 `PiStderrDiagnostics` 有界环；mapper 侧 auto_retry 进有界 typed 诊断 | `test_provider_429_retry_observability`（429×2→成功，attempts 1,2，settled 恰一次）+ `test_provider_429_exhaustion_fails_honestly`（持续 429 → 重试耗尽 → auto_retry_end success=false + 非空 finalError + 诚实 settled；≥3× status=429 观测）+ `test_pi_v16x_diagnostics.py::test_auto_retry_events_enter_typed_diagnostics` | 同左 |

## 5. 版本历史

| pin | 引入 | 备注 |
|---|---|---|
| v0.81.1+1 | PR #1973 前 | 旧基线 |
| v1.5.0 | PR #1973（ADR-0282） | 两段加载/prompt disposition/pi-schema 校验等契约面以此为准 |
| v1.5.1 | G12（ADR-0288） | 原生工具发现/嵌套调用/有序停机契约面（C11–C16）以此为准；floor 下限 |
| v1.5.2 | G22（2026-11-11） | pin-only 升级；消费面零漂移（§4 正式裁决）；interactive missing-cwd UX/导入清理不触及 rpc 面 |
| **v1.6.1**（当前） | goal-pi-native-gis-host-20261125（ADR-0301，2026-11-26） | 先修 #2169 symlink 回归再升 pin；消费面零漂移（§4.5 裁决，json/rpc/rpc-extension-ui 文档逐字节一致）；新契约面 C17–C24（供应链守卫/manifest/版本探针/能力快照/v1.6.x 生命周期与 provider 事件）|
