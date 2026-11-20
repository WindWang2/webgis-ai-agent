# G19-TYPED-FIELD-COMPUTE — BASELINE

记录时间：2026-11-10 21:2x GMT+8（ZCode G19 首轮，全部结论以当前源码复核）

## 仓库与基线

| 项 | 事实 | 证据 |
|---|---|---|
| 目标仓库 | `WindWang2/webgis-ai-agent` | `git remote -v`（origin = https://github.com/WindWang2/webgis-ai-agent.git）+ `gh repo view --json nameWithOwner` |
| 默认分支 | `master` | `gh repo view --json defaultBranchRef --jq .defaultBranchRef.name` |
| `BASE_SHA` | `816705218a5aa6202bf15d745cb31cddb3437f44` | `git fetch origin --prune` 后 `git rev-parse origin/master`；与研究快照 SHA 一致（主干未前进） |
| 主工作区 | `master @8167052`，有大量**未跟踪**文件，未触碰 | `git status --short`：`.claude/`、`dev-batch-g/`、`research/`、`.tmp_*`、`docs/reviews/SWARM_DEEP_REVIEW_2026-10-19.md` |
| 既有 worktree | 主工作区 + `.worktrees/fix-deep-review-2026-10-28`（`fix/deep-review-2026-10-28 @970fc75`） | `git worktree list --porcelain` |
| 本任务 worktree | `C:/Users/wangj.KEVIN/projects/webgis-worktrees/goal-g19-typed-field-compute-20261110T131120Z` | `git worktree add -b ... $BASE_SHA`，干净 `status --short`（空） |
| 本任务分支 | `zcode/goal-g19-typed-field-compute-20261110T131120Z` | 同上 |
| 同机并行任务 | G17 / G18 / G20 worktree 已存在（21:12 / 21:11 / 21:17 创建） | `ls webgis-worktrees/`；G12–G16 视为已分配，不接管 |
| gh | v2.89.0，账号 `WindWang2`，token scopes 含 `repo`/`workflow` | `gh auth status` |

## 环境与纪律

- 主工作区 `.venv/Scripts/python.exe`：pydantic 2.13.5 / pytest 9.1.1 / fastapi 0.141.1 / pyproj 3.8.0。worktree 无独立 venv；测试统一用主 venv 解释器 + **worktree 作为 cwd**（`import app...` 已验证解析到 worktree 路径）。
- 测试固定 `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1`，单进程 `-p no:cacheprovider --no-cov`，不启用 xdist。
- `CLAUDE.md:41`：仓库明令 **Never wait for CI results**；本任务不轮询线上 CI、不改门禁。
- `frontend/AGENTS.md`：Next.js 本地文档要求；本任务不改 Next.js 框架面，只增组件/页面局部挂载。
- ADR 目录已有 `0286`/`0287` **重号**（`0286-geodata...`/`0286-pi-native...`、`0287-capability...`/`0287-situated...`）。本任务**不占用任何 ADR 数字编号**，契约写入本目录 `INTERFACES.md` + 域模块 docstring，避免与并行 PR 碰撞。

## PR / issue 全景（复核后）

- **开放 PR = 12**：`#2032` UI 重设计、`#2022` G10 数据刷新、`#2021` G11 地图简报，另 9 个 dependabot（#2024–#2031, #1979）。**未见 G12–G18 / G20–G21 的在途 PR**，但有历史 `zcode/*vnext*` 远程分支（旧波次，非本批）。
- **排重清单状态**（`gh api pulls/<n>`）：全部 closed/merged —— `#1873`（要素编辑，2026-10-31）、`#1875`（联动视图/标注，2026-10-31）、`#1973`（Pi v1.5.0，2026-11-06）、`#1975`（出版制图）、`#2011`–`#2017`（执行编译/Pi runtime/数据资格/情境工作台/制图组合器/能力生态/可靠性实验室，2026-11-08）、`#2033`–`#2036`（命令清单/Studio 单源/Agent 撤销/Pi-only，2026-11-10）。开放：`#2021`/`#2022`/`#2032`。
- **评审记录**：对 `#2021/#2022/#2032/#2013/#2014/#2018` 分别查 `pulls/<n>/reviews`、`pulls/<n>/comments`、`issues/<n>/comments` —— **无正式 review submission，无 inline comments**；仅 `#2021` 有一条作者贴的独立 review 记录会话评论。按任务书「没有正式 review 不代表已通过评审」处理。
- **开放 issues = 45**：R1 审计族 `#2037`–`#2072`（2026-11-10）+ 旧族 `#1999`–`#2007`。与 WP06「全量可读/一致快照」直接相关：`#2066`（V6 物理执行器按 fetch window 静默截断）、`#2067`（FlatGeobuf 统计只读前 max_rows）、`#2056`（有界读取/分页）、`#2065`（联邦聚合截断）。本任务不修这些，但**不重复其错误**：ref-backed 载荷一次性冻结快照，不采分页样本冒充全表。
- 此前 G09 参数化批量分析无在途 PR；不纳入本任务。

## 现有 production 调用链（源码复核）

`app/api/routes/data_preparation.py`（7 端点，`app/main.py:1131` `include_router(prefix="/api/v1")`）→ `app/services/data_preparation/service.py::inspect_source`（`session_data_manager.get` + `profile_features` + `run_quality_checks` + `propose_field_roles` + `build_spatial_receipt`）→ `plan.py::build_plan`（closed-union 步集、`compute_plan_hash`）→ `executor.py::dry_run` / `execute_plan`（`apply_steps` 深拷贝、双锚 STALE_INPUT、`session_data_manager.store(prefix="prep")`、descriptor 铸造、`record_transformation`、`register_artifact`、JSONL receipt ring）。
`app/tools/data_preparation_tools.py::prepare_dataset` 是 `@tool(registry, ...)` 五态判别工具，注册在 `app/tools/__init__.py:84`。
前端 `frontend/components/sidebar/data-preparation/prepare-flow.tsx` 挂在 `data-sources-tab.tsx:601`（`activeSubTab === 'prepare'`）。

## 真实基线小旅程（WP01 要求，非复写旧报告）

命令（worktree cwd）：
```
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  .venv/Scripts/python.exe -m pytest tests/data/test_preparation_plan_v1.py -q --no-cov -p no:cacheprovider
```
结果（78.48s）：**24 passed / 9 failed**。全部 9 个失败集中在执行链（`TestExecutePlan::*` 7 项 + `TestReviewR1Regressions` 2 项），根因一致：

```
app.services.dataset_semantics.store:store.py:252 [dataset-semantics] payload write failed:
[WinError 123] 文件名、目录名或卷标语法不正确。
'.../dataset_semantics/<hash>/tmpXXXX.tmp' -> '.../dsd-v1:b2623a3f...json'
```

即 descriptor 文件名带冒号 `dsd-v1:<sha>.json`，Windows 非法 → `PutResult(ok=False, DESCRIPTOR_STORE_WRITE_FAILED)` → 测试 fixture 的 `assert put.ok` 失败。**在同 SHA 干净基线可复现，是仓库既有环境缺陷，与本任务无关**；不删测试、不放宽容差、不改 golden。纯函数面（closed-union、`plan_hash` 确定性/敏感性、`apply_steps` 原件不可变、`dry_run` 截断、去重保首、过滤硬闸）24 项全过。

**对本任务的设计约束**：G19 成功路径不能以 `dataset_semantics.store.put()` 成功为必要条件；descriptor/provenance/artifact 登记一律 additive try/except + 收据如实披露 `descriptor_minted: false` 与原因码；输出 ref 可读性与结果正确性用 `session_data_manager.get()` 回读独立验证。

## 下一步

见 `PLAN.md`；账本见 `LOOP_LEDGER.md`。
