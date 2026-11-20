# RECON — G22-PI-STATE-CONTINUITY

> 状态：持续更新。所有 SHA/路径在执行时重新核实。

## 0. 基线事实（2026-11-11T15:14Z 核实）

- BASE_SHA = `b8f2a6ecc777d0ae633202034b609a48a8d140d9`（origin/master）
  - 快照时开放的 #2084/#2085/#2086 已全部合入 BASE，不再阻塞本地验证。
- 分支/worktree：`zcode/g22-pi-state-continuity-20261111T151408Z`
  @ `/home/kevin/projects/webgis/webgis-wt-g22-pi-state-continuity-20261111T151408Z`
  - 独立 venv（uv, Python 3.13.x, requirements.lock + requirements-dev.txt）。
  - vendor/pi submodule 已 init @ `dd30101cf` = tag **v1.5.1**。
- 无开放 PR（`gh pr list --state open` 空）。
- 主工作区有未提交 `M vendor/pi`（gitlink dd6bea41e，v1.5.1+1）——他人/前会话状态，
  只读保留，不纳入本方向。

## 1. 上游 Pi 版本调查（P0 输入）

- 上游仓库 `earendil-works/pi`（remote 已核实）。最新 release/tag：**v1.5.2**
  （2026-11-10T12:25:43Z 发布；无更新 tag）。tag SHA = `b4afdddf44e059c67ccf5097e5317d913521b470`。
- v1.5.1 (`dd30101cf`) → v1.5.2 (`b4afdddf4`) diff（本机逐文件核实，非转述）：
  - `CHANGELOG.md` +15：仅「Fork sessions whose folder is gone」feature、
    interactive missing-cwd 三选（Continue/Fork/Cancel）、/import 失败副本清理、
    Anthropic OAuth 版本串。
  - `src/main.ts` ±32：missing-cwd 分支重构；**非 interactive（含 `--mode rpc`）
    分支行为不变**（`MissingSessionCwdError` + exit(1) 原样）。
  - `src/core/session-cwd.ts` +16：`getMissingSessionCwdOptions`（interactive 提示用）。
  - `src/core/agent-session-runtime.ts` +10：/import 失败时 `rmSync` 清理残留副本
    （仅 fork/import 路径）。
  - `src/modes/interactive/interactive-mode.ts` ±93 + 2 个测试文件：interactive UX。
  - `docs/sessions.md`、`docs/llama-cpp.md`、若干 examples 的 package.json/lock。
  - **RPC wire 协议文档（rpc-commands.md/json.md/rpc-extension-ui.md）零改动**；
    sdk.ts、rpc 扩展加载、tool_search、codemode、abort/clear_queue、shutdown 面零改动。
- 结论（待 pi_doctor + 契约测试 + 真实子进程复验后定案）：v1.5.2 对 C1–C16
  消费面零漂移，与 G12 矩阵 §4 评估一致。升级 = pin-only 变更 + 全清单复跑。

## 2. G12 残留（REVIEW.md 核实，G22 直接输入）

- **P2-1**：同会话后继 turn 的 clear_queue/abort 迟到窗口（跨会话已挡；
  `_active_turn_sid` 不随后继 turn 改变）→ G22-P1 核心目标。
- **P2-2**：J6b 选择器 turn-2 激活不稳定（master@v1.5.0 干净基线 10 跑 4 败，
  `nearest_ok:False` + step_error 同签名）→ G22-P5 需查真实根因。
- **N4**：J7 llm_calls 计数 vacuous（`json.dumps` 带空格 vs node 紧凑输出 → 恒 0）
  → G22 测试可观测性必须修复，reviewer 特别点名「检查 LLM stub 请求计数是否恒为0」。
- N3 教训：字段契约快照（API_CONTRACT_FIELDS_UPDATE=1）需刷新流程。

## 3. Issues 现况（按最新代码裁决，不接管 R1）

- #2061 [R1-agent P2]「/config/llm 更新静默无效」：PR #2081 标题声称修复
  （fixes #2061），issue 仍开放。G12 P1-2（turn 准入 `recycle_if_config_stale`）
  + P1-3（spawn 解析 key 覆盖 env）为修复主体 → P3 工作包需按最新代码复验
  A10/A11 后在 PR body 记录证据；不重复实现，不擅自关 issue。
- #2060 SessionBudgetLedger 无界增长：R1-agent owner。G22-A24 只证明**本方向**
  queue/receipt/context 结构有界，不接管 budget ledger。
- #2062/#2063 等 R1 合集：仅登记依赖，不接管。

## 4. 关键文档结论

- `docs/PI_UPSTREAM_COMPAT_MATRIX.md`（80 行）：C1–C16 契约表 + §3 升级清单 +
  §4 v1.5.0→v1.5.1 裁决 + v1.5.2 初评 + 回退方案（gitlink + PI_MIN_VENDOR_TAG +
  env kill-switch）。
- G12 已合入能力面（native discovery/codemode 嵌套/clear_queue/配置代际/有序停机）
  = G22 的起点，不重建。

## 5. 代码面调查

（见子代理 A 报告，回填至 INTERFACES.md / DUPLICATION_MATRIX.md）

## 6. 环境与资源

- 16 核 / 62GB RAM（~38GB 可用）/ 938GB 盘（40% 用）。编译默认 -j2。
- 宿主级重任务锁：`/home/kevin/projects/webgis/.heavy-task.lock`（flock，真实 OS 锁）。
- pytest：单 worker、不开 xdist；Playwright workers=1。
- Python 窗口 >=3.12,<3.14（worktree venv = 3.13.x 合规）。
- 前端测试如需 jiti：G12 记录需 `frontend/node_modules/.bin/jiti` 符号链接（mapspec
  编译测试）；按需安装，不提前做重型 pnpm install。
