# G10-PROJECT-DATA-REFRESH · LOOP_LEDGER（循环账本）

> 每轮：目标 / 证据（命令 + 退出码） / 变更文件 / 原始失败 / 修复 commit / 验证模式 / 剩余风险。
> 只追加，不覆盖。证据以命令输出与 commit hash 为准。

| 轮 | 目标 | 证据（命令 / 退出码） | 变更文件 | 原始失败 | 修复 commit | 验证模式 | 剩余风险 |
|---|---|---|---|---|---|---|---|
| R0 | A1/A2 启动取证：技能、git、远端 PR/issue | `git status --short`、`git worktree list --porcelain`、`git fetch origin --prune`(0)、`gh pr list/open/merged/issue`(0)、`gh api pulls/<n>`(0) | 无（只读） | fish shell `for` 语法错误 ×2（exit 123）→ 改用 `bash -c` | — | 只读核查 | 本地 `master` 落后；已改用 `origin/master` |
| R1 | A3 独立 worktree | `git worktree add -b zcode/goal-g10-project-data-refresh-20261108T153716Z ... origin/master`(0)；`HEAD=8eb8aa188` | 无 | 无 | — | 只读核查 | 其它 34 个 worktree 未触碰 |
| R2 | M0 源码现状双线调研 | 2 个只读 Explore 子代理（后端 / 前端），0 文件改动 | 无 | 无 | — | 只读调研 | 浏览器/Pi vendor 版本未实测 |
| R3 | M0 账本落地 | 6 个账本文件写入(0) | `planning/g10-project-data-refresh/{BASELINE,OWNERSHIP,PLAN,LOOP_LEDGER,TEST_MATRIX,FEATURE_ACCEPTANCE}.md` | 无 | `b80c7a65d` | 文档 | — |
| R4 | M1 失败测试先行 | `pytest tests/data/test_project_data_refresh_{diff,merge}.py`(**exit 2**)：`ModuleNotFoundError: app.services.project_runtime.refresh` | 2 个测试文件 | 模块不存在（预期红） | — | PU（红） | — |
| R5 | S1–S3 契约/差异/影响/合并 | 首跑 `1 failed, 14 passed`(exit 1)：组件文本未保留；复跑 `15 passed`(0) | `refresh/{contracts,diff,impact,merge,__init__}.py` | `merge.py` 原地修改落在 `candidate` 深拷贝之外（`n_comp`/`n_layers` 取自 candidate 而非 merged） | `cffc4353f` | PU | — |
| R6 | S4–S5 隔离候选/采纳/回退 | `pytest ...test_project_data_refresh_service.py` 首跑 `10 failed, 2 passed`(exit 1)：`dag = 'nodes'`（`used_dag, dag_source = dag or await ...` 把 dict 当二元组解包）；修后 `27 passed`(0) | `refresh/{records,store,sources,execution,service}.py` + 12 个 DI 测试 | 元组解包错误；显式分析图被误判 fallback 强制保守；`analysis_roles` 从 `logical_id` 推导出图中不存在的节点 | `9adda2eec` | DI | — |
| R7 | S6 REST + 权限门 | `app.openapi()` 生成 12 条 G10 路由(0)；路由测试首跑 `1 failed, 4 passed`：统一错误信封把结构化 detail 放在 `data`（ADR-0138）；修后 `5 passed`(0) | `app/main.py`(2 行)、`routes/project_data_refresh.py`、`schemas/project_data_refresh_schema.py`、5 个 HTTP 集成测试 | 断言用了 `body["detail"]` 而非 `body["data"]` | `1d1fb3a54` | DI（真实 HTTP + 真实鉴权） | — |
| R8 | S7 Pi 工具链 | 首跑 `3 failed, 1 passed`：① `list_tools()` 返回字符串列表 ② 既有「同参重复拦截」吞掉第二次 check ③ 工具错误被 Pi 桥包装成 LLM 文本；改用真实 `ToolDispatchService.dispatch` + 新 `executed_tools` 集合取 `raw_result` 后 `5 passed`(0) | `app/tools/__init__.py`(1 行)、`app/tools/project_data_refresh_tools.py`、Pi 集成测试 | `side_effect` 词表非法（`read_only`）→ 改 `pure`/`artifact_creation`；fish heredoc 反引号被命令替换 → 改用编辑工具 | `8145754c1` | PI（真实 registry + dispatch + 领域计算） | 完整 Pi RPC + stub LLM 子进程车道未跑 |
| R9 | S8 前端 | `pnpm install --frozen-lockfile`(**EPERM futime，沙箱拦截**) → 只读 symlink 复用同仓库依赖缓存(0)；`vitest` 组件测试首跑 `2 failed, 3 passed`：动态 import 使 mock 未生效触发真实 fetch、预览按钮误放已有提案分支；修后 `5 passed`(0)；`typecheck`(0)；i18n+e2e-lanes 闸 `25 passed`(0) | 前端 8 个文件（panel / api client / 双语 JSON / journey / registry / 挂载） | 见上 | `183a0a632` | UI 单测 + typecheck + i18n 闸 | **真实浏览器 E2E 未跑**（需 real 后端 + 预置账号），标未验证 |
| R10 | S9 对拍/性能/文档 | `pytest ...test_project_data_refresh_parity.py`(0) `3 passed`，输出 `G10_PERFORMANCE_JSON`；全量后端 `40 passed`(0)；`ruff check` 首跑 5×F401 → `--fix` 后 `All checks passed`(0)；生成物闸首跑 `2 failed`(#2007 既有漂移 + 12 新端点) → 官方生成器 `API_SNAPSHOT_UPDATE=1 pytest ...`(0) + `python scripts/gen_api_docs.py`(0) → 复跑 `15 passed`(0) | parity 测试、PERFORMANCE.md、docs/features、生成物 ×2、ruff 清理 | 见上 | 待提交（S9） | DI + PU + 生成物闸 | 快照 diff 含 master 既有漂移，PR 中披露 |
| R11 | M8 独立 Review（C 冷读，固定 SHA `1a6a2eff0`） | `git diff 8eb8aa188..1a6a2eff0 --stat`(0)：39 files +9073/−30；后端 40 passed(0)、生成物闸 15 passed(0)、ruff(0)、4 组只读 `python -c` 复现(0)；审查者全程未改文件 | 无（只读审查） | **3×P1**：① `_bounded_map` 静默截断 256 → 假 EXACT + silently unchanged；② 客户端 `style_only` 跳过真实差异；③ `hard_semantic_errors` 用 `and` 短路。另有 18×P2 / 4 组 P3 | 待提交（R12 修复轮） | 冷读 + 针对性复现 | 结论：需修复后二审，0×P0 |
| R12 | M8 Fix（R1）：清偿 3×P1 + 高价值 P2 | 回归 10 passed(0)；全套后端 50 passed(0)；前端 30 passed + typecheck(0)；生成物闸 15 passed(0)；ruff(0) | contracts/diff/impact/merge/execution/service/store/routes/tools/前端 panel + 回归测试 | 见 R11 | `1945e7316` | PU+DI+PI+真实生产 CAS | 残余 P2/P3 在 PR 披露 |
| R13 | M8 Re-Review（C 冷读 `1945e7316`） | 40 files +9873/−274；后端 50 passed(0)、生成物闸 15 passed(0)、ruff(0)、8 端点 HTTP 探针 + 3×P1 只读复现(0)；审查者未改文件 | 无（只读审查） | **R2 新 P1**：`_guard_proposal` 抛 `RefreshError` 未被端点 `try/except` 覆盖 → 8 个端点在 proposal 不存在/越权时返回 500；另 4 项 P2/P3（cap glob 把 `.doc.json` 计配额、实例重验证、短尾绑定碰撞、TEST_MATRIX O05 措辞） | 待提交（R14） | 冷读 + 针对性复现 | 结论：仍需修复，0×P0 |
| R14 | M8 Fix（R2）：修新 P1 + 4 项 P2/P3 | 回归 19 passed(0)；全套后端 54 passed(0)；生成物闸 15 passed(0)；ruff(0) | routes.py / contracts.py / store.py / service.py / TEST_MATRIX + 回归测试 | 见 R13 | 待提交 | PU+DI+PI+HTTP 探针 | 待 R15 终审 |
| R15 | M8 终审（C 冷读 `5f6ff5263`） | 增量 9 files +143/−26；全套 54 passed(0)、生成物闸 15 passed(0)、ruff(0)、8 端点独立 HTTP 探针 404、3 组只读复现(0)；审查者未改文件 | 无（只读审查） | R1/R2 全部 P1 已修，4 项 P2/P3 已修；**未发现新 P0/P1**；结论 **PR-ready** | — | 冷读 + 针对性复现 | 残余 P2/P3 随 PR 披露 |
| R16 | M8 终审后收口 | R15 建议：内部故障不得伪装 404 → `routes.py` 改为 log + re-raise；新增 `test_r3_internal_session_check_failure_is_observable_not_masked_as_404` → 7 passed(0) | routes.py + 路由测试 | 见 R15 残余 | 待提交 | DI | — |
| R17 | M9 同步 master + 最终验收 + PR | `git fetch origin --prune`(0)：`origin/master` 仍 `8eb8aa188`，`merge-base=8eb8aa188`，`ahead/behind=0/9`，**无需 merge**；`gh pr list` open=2（#1979 dependabot、#2021 G11）；**最终验收两遍**：R1 后端 55 passed(0)+生成物闸 15 passed(0)+ruff(0)；R2 权限/完整性 32 passed(0)+对拍/兼容 15 passed(0)+前端 typecheck(0)+vitest 30 passed(0) | routes.py（内部故障 log+re-raise）、路由回归测试、PR_DESCRIPTION、账本 | 见 R15；G11 #2021 共享 `app/main.py`/`tools/__init__.py`，已在 PR 正文披露潜在冲突 | `2c62dd38d` + **PR #2022**（OPEN，非 draft，未合并） | 两遍最终验收 | 真实浏览器 E2E / 完整 Pi RPC 车道标未验证 |

## 关键确定性验收（在最终代码上重复两遍）

- R6/R10：`pytest tests/data/test_project_data_refresh_*.py` 两轮均 27→（含 R10 全套 40）绿。
- R10：OpenAPI 兼容闸在生成器刷新前后各跑一遍（前红→后绿 15 passed）。
- R10：ruff 静态检查 `--fix` 前后各跑一遍（前 5 错→后 All checks passed）。
- R17：**最终代码上重复两遍**（契约 A7）：第一遍后端 55 + 生成物 15 + ruff；第二遍权限/完整性 32 + 对拍/兼容 15 + typecheck + vitest 30，全部 exit 0。

## 发布结论（M9）

- 分支 `zcode/goal-g10-project-data-refresh-20261108T153716Z` 已 push，PR **#2022**：https://github.com/WindWang2/webgis-ai-agent/pull/2022
- PR：OPEN / 非 draft / base=master / 10 commits / 41 files / head `2c62dd38d44c8dc74e7aa1df509c316747c0c9c5`。
- **未自动合并**；未关闭他人 Issues；未删除任何未合并 worktree；未修改 G11 #2021 或 dependabot #1979。
- 线上 CI 未轮询、未宣称通过。
