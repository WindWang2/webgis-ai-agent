# G06 — /goal-loop 循环账本

每轮：输入事实 → 决定 → 代码差异 → 失败 → 修复 → 本地验证。
**不修饰失败、不虚构通过。**

## L0 · 实时审计（M0）

- 输入：`git fetch origin master --prune` →
  `origin/master = 585812ee4e2d75612f2b124e6fe6587af3deca5c`
  （2026-11-07T17:54:14+08:00，Merge PR #2010）。
- `gh pr list --state open` → 8 个，全部 dependabot，零文件冲突。
- `gh pr list --state merged --limit 45` + `gh pr view N --json files,reviews,comments`
  → #1972/#1973/#1976/#1989/#1993/#1997/#1998 的改动路径与 Review 已读。
- 并行 goal 分支 diff：G01 有 3 commits 只碰 `pi_*`/`health`/`evidence`；
  G02 0 commits；G03 在数据 fabric 侧。
- 决定：本线只写 `app/lib/gis/*`、`app/tools/capability_ecosystem_tools.py`、
  `app/services/chat/pi_availability_adapter.py`、docs/examples/scripts/planning。
- 输出：`BASELINE.md`、`INTERFACE_CONTRACTS.md`、`RECON.md`。
- 验证：`git worktree list` 确认新 worktree；`pytest test_execution_catalog.py` 16 passed；
  `execution_catalog_docs --check` up to date。

## L1 · 变更前失败测试（M1）

- 输入：稳定的 `INTERFACE_CONTRACTS.md`（主 Agent 定稿）。
- 决定：子代理 C 只写 2 个测试文件，import 集中在单个
  `try/except ModuleNotFoundError`；实现模块尚不存在 → 明确红灯。
- 真实输出（子代理原文）：
  `FFFFFFFFFFFFFFFFFFFFFFF [100%] ... 23 failed in 8.04s`，
  0 passed / 0 collection error / 0 skip，全部为
  `assert not MISSING, "实现模块尚未落地（M1 红灯证据）"`。
- 子代理附带报告 5 处契约歧义（`tool_id` 语义、量纲 space/params 方向、
  certify 顺序、封套 key 未细化、cert index 路径），未自行改契约。

## L2 · 核心实现（M2–M5）

- 输入：probe 得到的真实签名 —— `ToolRegistry.descriptors/registry_fingerprint`、
  `audit_registry_conformance`、`compile_execution_catalog`、
  `ParameterContractRegistry.iter_contract_packs`、`ExtensionHost.status_report`、
  `ExtensionState`、`resolve_pi_tool_call`、`slim_tool_result`、
  `manifest.py:466` 的 id 约束、`get_algorithm_registry().count == 253`。
- 决定：12 个新文件 + 2 处单行接线（详见 `git diff --stat`）。
- 首次 Oracle 运行（M2→M5 第一次）：**21 passed / 2 failed**
  1. `test_cap_o13_docs_and_examples_are_locally_executable` — 两份 dev 文档未写（计划内待办）。
  2. `test_cap_o10_extension_full_lifecycle_marks_namespace_unavailable` —
     `host.get_record(...) is None`（fixture manifest id 不合法）。
- 失败归因与修复：
  - O13 是交付物未完成 → 写 `docs/dev/GIS_CAPABILITY_ECOSYSTEM.md` +
    `GIS_TOOL_AUTHORING_CONTRACT.md`（含 pytest / CLI 命令）。
  - O10 是**测试脚手架数据错误**（`manifest.id` 必须 = `namespace.name`，
    `manifest.py:466`），修 fixture `name: "cap_o10" → "pack"`，**断言未动**；
    独立复验 1 passed / 10.46s。
- 第二次 Oracle 运行：**23 passed in 29.88s**（连续两次关键验收达成）。

## L3 · 真实注册面与生成物（M5–M7）

- 输入：`init_tools` 后 `len(tool_names()) = 399`（基线 397 + 2 新原语）。
- 8 面对账：两个新工具都是 `overall=warning`，理由
  `planner:binding_unbacked` + `science:no_parameter_contract`。
  **决定保留诚实降级**（见 DECISIONS D3），不刷成 verified。
- 快照实测：`native_verified=213 / available_unverified=186 /
  declared_only=18 / total=417`；fingerprint `cf1af53c…` / catalog `eee03b1e…`。
- 生成物漂移：`execution_catalog_docs --check` 报 3 个 DRIFT →
  用既有生成器重生成 → `--check` up to date。
- `get_algorithm_registry().count` 实测仍 **253**（未破坏硬断言）。
- 参数契约注册：`has(conditional_quantile_zonal_stats)=True`、
  `has(network_service_coverage_consistency)=True`。

## L4 · 质量门（M7）

- 首次 `ruff check`：**23 errors**（T201 print ×12、F401 未用 import ×11）。
- 首次 `mypy`：**3 errors**（`capability_availability.py` 的
  `getattr` 默认值 / `list`↔`tuple` 赋值）。
- 修复：CLI/demo/checker 改 `sys.stdout.write`；清理未用 import；
  把 `provider_deps` 提为局部变量并统一 `unverified: list[str]`；
  把 `extension_lifecycle_view` 的 import 变成**真实新增断言**
  `test_cap_o04_extension_lifecycle_view_fail_closed`（增加覆盖，不是加 noqa）。
- 复验：**ruff `All checks passed!`**、**mypy `Success: no issues found`**。

## L5 · 一键验收（M7）

- 首次 `scripts/verify_capability_ecosystem.py`：**5/6 passed**，
  失败项 `pack_manifest_checker`：
  `certification_stack_unavailable:ModuleNotFoundError` ×1 +
  `contract_check_failed` / `registry_check_failed`。
- 归因：`check_pack.py` 直接运行时 `sys.path` 不含仓库根（demo 已有引导，
  checker 漏了）。
- 修复：checker 顶部加 `sys.path.insert(0, repo_root)`。
- 复验：**6/6 passed**（CLI / checker / 2 demo / Oracle pytest / catalog 漂移门）。

## L6 · 回归（M7）

分批运行、不跑全量 CI、无 `-n`：

| 批次 | 命令 | 结果 |
|---|---|---|
| Oracle | 2 个新测试文件 | 23 passed |
| gis 全套（首次 -x） | `pytest tests/unit/gis/` | 1 failed / 532 passed — `test_catalog_doc_matches_registry_projection` |
| catalog/元数据/Pi 面 | 5 个文件 | 78 passed / 1 skipped |
| 失败项归因 | `gen_science_catalog.generate()` vs `ALGORITHM_CATALOG.md` | 「127 参数契约 → 129」，本方向 +2 契约引入 |
| 修复 | 既有 `scripts/gen_science_catalog.py` 重生成 | 该测试 1 passed |
| gis 全套（完整） | `pytest tests/unit/gis/` | **798 passed / 3 skipped**（109s） |
| 非必需生成物 | `gen_capability_catalog.py` 重生成带环境噪声 | 回退到 master（DECISIONS D8） |

## L7 · 基线债务归因（M7）

- `scripts/check_generated_staleness.py` 报 8 个 stale：
  `docs/quality/*` 与 `docs/science/BENCHMARK_MANIFEST.md`。
- 指纹归因：这些条目的 `content_fingerprint` 与账本**一致**，
  只有 `input_fingerprint` 变；输入目录含 `app/api/routes`、
  `app/lib/geo_analysis`、`app/lib/cancellation.py` 等本线**未改动**的路径。
  Issue #2007 已记录同类历史债务（#1993 合入后 CI 失败）。
- 决定：记录为基线/环境限制，不 `--update` 掩盖、不改 Actions、
  不等待线上 CI、不宣称 CI 通过（DECISIONS D9）。
- `git archive origin/master` 到 /tmp 的干净对照尝试被沙箱安全策略拦下
  （tar utime + 策略），已改用「输入目录枚举 + content_fingerprint 对拍」归因，
  不重试绕过。

## L8 · 独立冷审与二次 review（M8）

见 `REVIEW.md`（子代理 B 冷读 + 主 Agent 修复 + 二次 review）。

## L9 · 同步 / 提交 / PR（M9）

见 `PR_HANDOFF.md`。
