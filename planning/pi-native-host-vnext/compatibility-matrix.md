# Pi 升级兼容性矩阵：dd6bea41 (v0.81.1+1) → v1.5.0

基准：origin/master `b6ac970eb`；上游 tag `v1.5.0` = `f9a26365568127461c47a07a37dcb42c140a3ac5`（2026-11-02 发布，2304 commits ahead）。
证据来源：`git show/diff` 两 ref 源码对比（上游分析）＋ 本地 v1.5.0 干净 checkout 构建 + 真实 rpc-entry 活体测试（stub LLM / stub bridge 回调，见 `tests/integration/test_pi_vendor_contract.py` 与本文「活体验证」列）。
状态词：unchanged=两 ref 一致；additive=新增且向后兼容；breaking=需要适配；renamed/removed=更名/移除。

## 1. 构建与运行时

| 项 | 状态 | 事实 | 活体验证 |
|---|---|---|---|
| Node engines | unchanged | 两 ref 均 `>=22.19.0`（root/coding-agent）；本地 24.21、镜像 node:26 均满足 | ✅ 本机构建+运行 |
| 干净 checkout 构建 | changed | v1.5.0 `npm install && npm run build`（根）成功；新链 telemetry→schema→chord→tui→codemode→mcp→ai→durable→env→agent→coding-agent；`packages/server`/`storage` 已移除 | ✅ exit 0，`dist/rpc-entry.js` 与 `dist/bundle/rpc-entry.js` 均产出 |
| 旧 pin 干净构建 | **失败（升级论据）** | dd6bea41 在当前工具链下 `packages/ai` tsgo 类型错误（`"google-generative-ai"` 不在 api 联合类型）；根无 lockfile → 传递依赖漂移 | ❌ 复现于本机 |
| RPC 入口产物 | additive | 新增自包含 `dist/bundle/rpc-entry.js`（82 文件/9.1MiB，无 node_modules 依赖）；旧 `dist/rpc-entry.js` 仍由 tsgo 产出但依赖整仓 dist+node_modules | ✅ 两者均可启动 |
| spawn CLI 旗标 | unchanged | `--mode rpc` `--no-session` `--no-builtin-tools` `--extension` 全部存在（args.ts:96/132/149/186） | ✅ 真实 spawn |
| 内建工具排除 | unchanged+additive | `--no-builtin-tools` 保留；新增 settings `defaultTools:[]` 等价路径 | ✅ |

## 2. RPC 协议

| 项 | 状态 | 事实 | 活体验证 |
|---|---|---|---|
| 分帧 | unchanged | 严格 LF-only JSONL；U+2028/U+2029 不拆帧；容 CRLF（jsonl.ts 显式实现） | ✅ |
| 请求信封 | unchanged | `{id, type:<command>, …扁平参数}`；响应 `{id, type:"response", success, data|error}`；事件=有 type 无 id | ✅ get_state/set_model/prompt/abort |
| 命令集 | additive | 旧 32 命令全保留 + `clear_queue`；我方仅用 4 命令（prompt/abort/get_state/set_model）全部在位 | ✅ |
| `prompt` 响应 | additive+风险 | 响应 `data:{disposition:"started"\|"queued"\|"handled"}`；`handled`=被 input handler 消费、**无 agent run**。我方不注册 input handler，但桥若在 handled 时等 agent_settled 会挂满 PI_TURN_TOTAL_TIMEOUT(300s) | ⚠️ 需防御（M5） |
| `abort` | unchanged+additive | 成功响应同形；现在也取消 compaction；tool AbortSignal 立即触发 | ✅ abort→signal→isError→`agent_settled{aborted:true}` 全链 |
| 新事件 | additive | `agent_start`/`turn_start`/`turn_end`/`message_start`/`message_end`/`bash_execution_update`/`summarization_retry_*`/`extension_error` 等 — 我方 mapper 未知事件返回 None（无崩溃） | ✅ 全序列捕获 |
| `message_update` | breaking→**我方已免疫** | 上游 0.84.0 起仅 `{usage, assistantMessageEvent(delta-only)}`，无累计 `message`/`partial`。我方 mapper 早已按 delta 语义实现（audit #816）；`_extract_text_from_event(message_update)` 返回空串但权威文本仍取 `agent_end.messages`（两路径均有该兜底） | ✅ token 流 + agent_end 提取正常 |
| `agent_settled` | additive | 携带 `aborted:boolean`（我方终判事件不变） | ✅ |
| `tool_execution_*` | additive | start 增加 `args`；end 增加 `durationMs`；校验失败事件顶层仍有 `isError:true` | ✅ |
| `toolcall_start` | additive | 增加 `id`/`toolName` | ✅ |

## 3. 扩展 ABI（app/extensions/webgis-tools/index.mjs）

| 项 | 状态 | 事实 | 活体验证 |
|---|---|---|---|
| `registerTool` | unchanged+additive | 签名不变；`parameters` 必填（0.86.1 起无 schema 拒绝注册——我方全工具带 schema）；新增 outputSchema/exposure 等可选字段 | ✅ 注册+执行 |
| JSON Schema 直传 | unchanged | `parameters` 原样进入 toolsAdded（additionalProperties:false 等保留） | ✅ 事件回显逐字一致 |
| `promptSnippet`/`promptGuidelines` | unchanged | 流入系统提示 tools/rules 段 | ✅ |
| `pi.setActiveTools` 加载期调用 | **breaking** | 0.99+ 两阶段加载：加载期 action 不可用（"Extension runtime not initialized"）；registerTool 除外 | ❌ 复现；修复=延迟到 `session_start`/首轮 `before_agent_start`（M1） |
| 每轮 setActiveTools | unchanged | before_agent_start 内调用正常 | ✅ `active surface (turn): 8 tools` |
| `before_agent_start` | breaking(语义)+我方前缀路径可用 | v1.5.0 结构化系统提示；返回 `{systemPrompt}` 作为请求头投影（transcript 保留 sections）；旧字符串精确匹配 `CODING_ASSISTANT_OPENING` 已失效（上游改写为 "You are a coding assistant running inside pi…"）→ 我方前缀路径兜底生效 | ✅ stub LLM 收到 GeoAgent 开头；改进=同时匹配新串（M1） |
| `event.prompt` | unchanged | user 类型 trigger 携带原始 prompt（marker 扫描可用） | ✅ |
| `ctx.sessionManager.getEntries()` | unchanged | entries 含 `{type:"message",message:{role:"user",content:[…]}}`；turn-token marker 可扫描 | ✅ 回调带 token+fingerprint |
| 工具结果 JSON 限制 | unchanged(更严) | details 必须 JSON 兼容（我方本就 JSON） | ✅ |
| 扩展加载来源 | unchanged | `--extension <path>` 显式加载 .mjs 可用（自动发现仍只认 .ts/.js） | ✅ |

## 4. pi-schema 参数校验/强制转换（A3 核心边界）— 12 例实证

对 `additionalProperties:false` + typed schema 的工具（我方投影即此形态）：

| 模型实参 | 到达 execute() | 结论 |
|---|---|---|
| `count:"5"` (int 字段) | `count:5` | 数字字符串→数字（归一化） |
| `name:123` (string 字段) | `name:"123"` | 数字→字符串（双向归一化） |
| `tags:"single"` (array 字段) | `tags:["single"]` | 标量→单元素数组 |
| `flag:"true"` / `ratio:"3.5"` | `true` / `3.5` | 布尔/浮点字符串归一化 |
| `tags:[1,2]` (string[] 字段) | `["1","2"]` | 数组元素类型归一化 |
| `extra:"rogue"`（未知字段） | **拒绝**（execute 不执行） | additionalProperties 边界保留 ✅ |
| `count:0` / `count:99` | **拒绝** `expected >= 1` / `<= 10` | 数值边界保留 ✅ |
| 缺 required / enum 越界 | **拒绝** `missing` / `expected one of […]` | 必填/枚举保留 ✅ |

**边界政策（ADR 评审后写入）**：pi-schema 的类型归一化（数字↔字符串、标量→数组、布尔/浮点字符串）被接受为「单一事实归一化层」——服务端 `pi_input_gate` 的严格 Pydantic 校验保持不变，作为归一化后最终门（纵深防御不变）；additionalProperties/边界/枚举/必填在 Pi 侧先拒（错误文本新格式 `- path: reason`，以 tool_execution_end isError 回给模型）。union「取第一个可转换分支」的投机转换列入 adversarial corpus 监控。

## 5. Session / 取消 / 其它

| 项 | 状态 | 事实 |
|---|---|---|
| Session JSONL | 不适用+additive | 我方 `--no-session` + Python 侧持久真相；上游新增 usage/context_edit 条目不影响 |
| 断线/续起 | unchanged | 我方事件缓冲 resume 与 Pi session 无关 |
| models.json provider | unchanged | `api:"openai-completions"` + `$ENV` 替换 + compat 旗标全部生效（stub provider 解析 contextWindow=128000） |
| 安全 | additive | `.pi/mcp.json` 纳入信任；我方不用 MCP；扩展信任模型不变（in-process 全权限 → 我方扩展最小化 + 密钥隔离不变） |
| TypeBox | 不适用 | 我方扩展零 TypeBox 依赖（纯 JSON Schema） |

## Top 风险与对策

1. ~~setActiveTools 加载期失效~~ → M1 延迟到 session_start（唯一真实 breaking，已定位修复方案）
2. `prompt disposition:"handled"` 潜在 300s 挂 → M5 桥侧 disposition 检查，handled 即真实结算为 failed（不伪装成功）
3. 生产镜像从未构建 vendor dist（静默回退旧引擎）→ M2 Docker 构建阶段 + bundle 入口 + doctor
4. pi-schema 强制转换改变接受面（前拒后纳）→ M5 golden corpus 锁定矩阵 + ADR 边界政策
5. 上游 `message_update` 破坏性变更 → 已免疫（delta 语义），以契约测试锁死
6. 旧 pin 无法干净构建（类型错误）→ 升级论据；如需回退，回退=回退 submodule 指针+适配 revert（M7 回退开关说明）
