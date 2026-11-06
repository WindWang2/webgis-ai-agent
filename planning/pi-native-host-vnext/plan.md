# Pi v1.5.0 原生升级与 WebGIS Agent Host 兼容性 — 实施计划

任务：`01_PI_RUNTIME` ｜ 分支：`zcode/goal-pi-native-host-vnext-20261105` ｜ worktree：`../webgis-wt-goal-pi-native-host`
基准：origin/master `b6ac970eb`（2026-11-05）｜ 目标：vendor/pi dd6bea41 (v0.81.1+1, 2026-07-21) → **v1.5.0** (`f9a263655`, 2026-11-02 stable tag)

## 范围与避让

- 所有权（Track A）：`vendor/pi`、`.gitmodules`、`app/agent_pi_bridge.py`、`app/services/chat/pi_*`、`app/extensions/webgis-tools/index.mjs`、Pi 契约测试、启动文档。
- 避开 open PR #1965（scene3d/terrain/前端 observation —— 与 Track A 零文件交集，已核实）。
- Review-R2 issues（#1900–#1967）为维护线：只作风险输入（#1916 late-callback no-progress、#1931 post_dispatch never-raise 均在我改动面附近 —— 不修，不回归）。
- 不改：ToolRegistry/ToolDispatchService 执行真相、MapSpec 状态、GIS Harness 编排、frontend。

## 需求清点（implemented / partial / missing）

| 需求 | 现状 | 本任务动作 |
|---|---|---|
| A0 上游基线/差分评估 | missing → **完成**（compatibility-matrix.md，活体实证） | 落档 |
| A1 pin 升级 | partial（pin 未动） | submodule → v1.5.0 |
| A1 可复现构建 | **missing**（dist 从不在仓内构建；Docker 只 COPY vendor/；生产 Pi 静默回退） | 构建脚本 + Docker 阶段 + bundle 入口 |
| A1 诊断 CLI | **missing**（无 doctor/compat 工具） | `scripts/pi_doctor.py` + 运行时身份门 `pi_compat.py` |
| A2 RPC/事件/取消/背压 | partial（mock 测试全；真实进程契约测试仅 real-LLM 车道） | 真实 rpc-entry + stub LLM 契约车道（无需真实凭据） |
| A2 disposition=handled 防挂 | **missing** | 桥侧检查 |
| A3 双版本 golden / adversarial | partial（ABI adversarial 有；pi-schema 强制转换矩阵无） | golden corpus + union 投机转换 adversarial |
| A3 危险内建工具 fail-close | partial（--no-builtin-tools 单点） | 契约测试断言 spawn 面无内建工具 + doctor 校验 |
| A4 版本/能力 observability | **missing**（无 Pi 版本标识；PI_SKIP_VERSION_CHECK=1） | pi_compat 身份+能力报告（日志/health/doctor） |
| A5 场景 fixture 验证 | partial（real-LLM 车道需凭据） | stub-LLM 全链旅程（含取消/跨会话/旧 observation 边界断言） |
| 文档/ADR/回退 | **missing**（无升级政策） | ADR + specs 增补 + 回退开关（USE_NEW_AGENT=false + submodule revert 指引） |

## 里程碑（薄切片，每片含正/负/边界测试）

- **M1 pin + 扩展适配**：submodule v1.5.0；index.mjs ①默认 surface 投影延迟到 `session_start`（保 before_agent_start 每轮路径）②`CODING_ASSISTANT_OPENING` 双串匹配（v0.81 原串 + v1.5 新串，命中即替换，未命中前缀兜底不变）。测试：加载期无 "not initialized" 错误、默认/每轮投影均生效、persona 双形态。
- **M2 构建可复现**：`scripts/build-vendor-pi.sh`（npm install+build，幂等，缓存友好）；`PI_RPC_ENTRY` 解析顺序 bundle→dist；Dockerfile/Dockerfile.prod 增 pi-builder 阶段产出 bundle 并 COPY（修复「生产从未带 dist」缺陷）；.dockerignore 核查。
- **M3 身份门 + doctor**：`app/services/chat/pi_compat.py`（submodule HEAD/期望 pin/入口存在性/能力旗标 → 启动日志 + /health 投影 + 不匹配时 WARN 而非静默）；`scripts/pi_doctor.py`（pin/构建/扩展/dump/握手/事件词表/强制转换抽检/env 契约；分域 exit code；安全相关 fail-closed、观测相关 fail-open）。
- **M4 契约车道**：`tests/integration/test_pi_vendor_contract.py`（真实 rpc-entry + 仓内 stub LLM 模式）：握手、完整事件序列（message_update delta、toolcall_end、tool_execution_*、agent_settled 唯一终判、aborted）、abort 中断、未知事件容忍、信封/分帧（超长行、CRLF、U+2028 载荷）。未构建 vendor 自 skip 并给修复指引（非 silent-green）。
- **M5 强制转换 golden + 防挂**：`tests/fixtures/pi_schema_coercion_golden.json`（12 例实证矩阵 + union/嵌套/含 ref 的 adversarial 扩充）；`tests/unit/test_pi_schema_coercion_golden.py`（vendor 构建时活体比对；未构建时校验 golden 结构+文档一致性）；桥侧 prompt disposition≠started/queued 时真实结算（handled→failed 明示理由，不挂 300s）。
- **M6 场景验证**：fixture 旅程（成都小学分布→intent→POI→MapProduct→ACK/verdict；换色不重算数据；北京会话不污染成都 SessionPlan；取消不在下轮复活；旧 observation 不误判成功）跑在 stub-LLM 契约车道之上（复用 M4 harness），断言桥/SSE/回执真实行为。
- **M7 文档与回退**：ADR（升级决策+兼容契约+强制转换边界政策+回退）；`docs/agent-runtime/`、`specs/pi-as-agent-host.md` 版本政策；DEPLOYMENT 构建步骤；README/doctor 用法。
- **R8/R9**：独立审查（第三 subagent，只读）→ 逐项修复复核 → P0/P1=0、P2 处置。
- **R10**：全量本地验收（单测车道 + 契约车道 + lint/typecheck/契约门）→ 语义聚合 commit 链 → push → PR。

## 本地资源纪律

构建串行（-j2 语义：npm/tsc 单任务；pytest 不与前端构建并发）；重型验收在主 agent 串行跑；不等待远端 CI。

## 每轮 ledger 见 milestone-ledger.md；重叠矩阵见 pr-overlap-matrix.md。
