# BASELINE — GOAL-20261126-01 Pi 原生决策、工具契约与交互运行治理

## 快照核对（2026-11-27 UTC，执行时实查）

- `origin/master` = `59eb3f52747e865b29243af305abf6bb09fcc83b`
  （first-parent 顶端 = Merge PR #2282 `codex/ui-workbench-polish`；其下 #2281
  整合 #2267–#2280）。与任务书快照一致。
- `vendor/pi` gitlink @ master = `ef4a1cfcca62b77ad3558337ff3e1c7f6beeadfe`
  （mode 160000，gitlink 正常形态，非 symlink）= 上游 tag **v1.6.1**。
- 上游 `earendil-works/pi`（原 badlogic/pi-mono）最新 stable release =
  **v1.6.1**（2026-11-25T13:41:35Z，非 prerelease/draft）；
  `git ls-remote --tags` 末尾：v1.5.0/v1.5.1/v1.5.2/v1.6.0/v1.6.1，无更新 tag。
  → **本线保留 pin v1.6.1，无新 stable 可升**。
- PR 去重（`gh pr list --state all --limit 40` + `--state open`）：
  - 开放 PR = **0**。
  - 已合并相关线：#2276（goal-pi-native-gis-host-20261125）、#2280
    （feat/pi-next-gis-host-20261124）、#2272–#2279（五条 20261124/25 线）、
    #2281（整合）、#2282（UI polish）。#2265/#2266 及更早已合并。
  - 结论：本方向前序宿主融合工作已在 master；本线**不重建宿主**，
    只消费已具备能力（决策/schema/阻塞语义/上下文治理/可观察性）。

## 本线 worktree

- 分支：`zcode/goal-pi-native-decision-governance-20261126-172144`
- worktree：`/home/kevin/projects/webgis/webgis-pi-native-decision-governance-20261126T172144Z`
- 基于 `59eb3f527`；`vendor/pi` 子模组已 `--init --recursive` @ ef4a1cfc，干净。
- 主工作树 `webgis-ai-agent` 在 `zcode/full-code-review-fixes`（有未提交改动）——
  本线不触碰该树，不合并该分支。

## 基线契约事实（来自 docs/，须以代码复核为准）

- `docs/PI_UPSTREAM_COMPAT_MATRIX.md`：pin v1.6.1 / floor v1.5.1（`PI_MIN_VENDOR_TAG`）；
  契约面 C1–C24；能力快照 `pi_compat.PiCapabilitySnapshot`（14 项封闭词表，
  v1.6.1 组 = provider_failure_events / decision_model_classify / rpc_line_scan_fix）。
- C20 现状：扩展 `registerTool` 未设 `constrainedSampling` → 线上 `strict:false`；
  #12049 增量降级谓词未触发；native 前门 xfail(strict) 钉 `query_local_poi` +
  `webgis_component_update`；长尾 opt-in fallout 252/529（待本地复现）。
- C22：`models.classify()` + `responseId`（codemode 能力，默认不启用，能力门控）。
- C23：手动 compact wire 契约（`compaction_start/end`，keepRecentTokens 20k）。
- C24：provider 429/retry 可观测（auto_retry wire 事件 + `after_provider_response`
  → 有界 stderr 诊断 → `PiStderrDiagnostics` 环）。
- `docs/PI_RUNTIME_CONTRACT.md`：agent_settled 唯一终判；三层工具面（native 7 冻结 →
  registry 超集 dump → per-turn 激活面 32KB + SurfaceReceipt 指纹）；discovery grant
  ≤16/turn；热回收 kill switch；compaction 扩展只记账，权威重建走
  `GIS_STATE_REBUILD` typed assembly 块；无 session 持久化（--no-session，诚实中断）。

## 本机环境

- node v24.21.0（≥22.19 满足 vendor engines）。
- 项目 python = 3.12（主仓 venv 3.12.15）；系统 python 3.14 不可用（超 pyproject 上界）。
  本线独立 venv：worktree 内 `uv venv --python 3.12 .venv` + `uv sync --frozen`。
- `ruff`：无全局可执行；以 venv 内 `python -m ruff` 为准（uv sync 后确认）。
- 主机 load ≈10/16 核（其他线并发）→ 本线重型任务限 `-j4`、pytest 单进程起步。

## R0 干净基线（待本地复现后回填）

- [ ] `tests/unit/test_pi_vendor_capability_snapshot.py`
- [ ] `tests/unit/test_pi_native_surface.py`（schema 卫生分桶：xfail(strict) 2 项、长尾 fallout 252/529）
- [ ] `tests/unit/test_pi_v16x_diagnostics.py`
- [ ] `tests/integration/test_pi_vendor_contract.py`（需 build vendor）
- [ ] `tests/integration/test_pi_schema_coercion_golden.py`
- [ ] Ruff（范围与计数按实际输出记录）
- [ ] #2281 披露的 29 项 Ruff / #2282 披露的 5 项既有诊断：仅本地复现才归类
