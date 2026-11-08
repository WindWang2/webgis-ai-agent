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
| R11 | M8 独立 Review → Fix → Re-Review | 待执行 | 待填写 | 待填写 | 待填写 | 冷读 + 针对性复现 | — |
| R12 | M9 同步 master + PR | 待执行 | 待填写 | 待填写 | 待填写 | 最终验收 | 不自动 merge |

## 关键确定性验收（在最终代码上重复两遍）

- R6/R10：`pytest tests/data/test_project_data_refresh_*.py` 两轮均 27→（含 R10 全套 40）绿。
- R10：OpenAPI 兼容闸在生成器刷新前后各跑一遍（前红→后绿 15 passed）。
- R10：ruff 静态检查 `--fix` 前后各跑一遍（前 5 错→后 All checks passed）。
