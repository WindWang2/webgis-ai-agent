# G10-PROJECT-DATA-REFRESH · BASELINE（M0 取证）

- 取证时间：2026-11-08T15:37Z（UTC）
- 执行者：ZCode 主开发 Agent（自管理目标循环；`/goal-loop` 未安装，见下方说明）
- 工作树：`/home/kevin/projects/webgis/webgis-wt-g10-project-data-refresh-20261108T153716Z`
- 分支：`zcode/goal-g10-project-data-refresh-20261108T153716Z`（基点是 `origin/master`）
- 只读主工作树：`/home/kevin/projects/webgis/webgis-ai-agent`（分支 `zcode/review-g3dv2-fixes`，存在他人未提交修改 `m vendor/pi` 与 30 个未跟踪 `.claude/skills/*`，**已保留，未 stash/clean/reset/切换**）

## 0. 技能与执行模式（契约 A1 实测）

| 项 | 实测结果 |
|---|---|
| `/goal-loop`（来源候选 `bravesd/goal-loop`） | ❌ 未安装：`~/.claude/skills`、`~/.workbuddy/skills`、项目 `.claude/skills` 全盘搜索 `*goal*loop*` 无命中 |
| 项目内 `loop-me` 技能 | ⚠️ 存在但是「工作流规范对谈」技能（`.claude/skills/loop-me/SKILL.md`），与目标执行循环无关，未使用 |
| 已安装可用技能 | ✅ `ask-matt` / `tdd` / `code-review` / `review` / `gstack` / `planning-with-files`（等价物）；`gh 2.102.1` 可用 |
| 回退决策 | ✅ 按 A1 回退为**自管理目标循环**：本目录 `PLAN.md` + `LOOP_LEDGER.md` + `TEST_MATRIX.md` + `FEATURE_ACCEPTANCE.md` 为正式证据账本；不下载、不执行任何未知脚本；不改写全局技能环境 |
| 未杜撰内容 | ✅ 未声称 ZCode flags、未声称可突破宿主上下文/执行时长上限 |

机器资源：16 CPU / 62 GiB 内存（可用 39 GiB）。本任务并行度保守取 **-j2**（编译上限 -j4 未触碰），测试 1–2 workers，`OMP_NUM_THREADS=1` 等已设置。

## 1. Git 实时状态（A2 强制项）

| 项 | 值 |
|---|---|
| 远端 URL | `https://github.com/WindWang2/webgis-ai-agent.git` |
| 默认分支 | `origin/master`（`refs/remotes/origin/HEAD`） |
| **`origin/master`（权威基线）** | **`8eb8aa188c1464ecb71bee79f40192e8045285b5`** = Merge PR #2020 |
| 本地 `master`（落后，只读，不用作基点） | `7b4e41667`（Merge PR #1836） |
| 用户提供的参考快照 | `8eb8aa188...` —— ✅ 与本次 fetch 的 `origin/master` **完全一致** |
| `git fetch origin --prune` | ✅ 已执行，退出码 0 |
| worktree 数量 | 35 个（含大量他人并行 worktree，全部未触碰） |

⚠️ 契约中「生成此文件时 master 已不同于 `585812ee`」已验证：当前 `origin/master` 是 #2020 合并点，**不是**旧的「七个 PR 尚未合入」状态。

## 2. GitHub 实时状态（A2 强制项）

### 2.1 PR（不截断声明）

- `gh pr list --state open --limit 200` → **1 条，未达上限**（无截断）。
- `gh pr list --state merged --limit 60` → 60 条**触及上限**，已按契约改用 `--limit 100` 复核 → 100 条，仍触及上限；本任务只需判定 #2011–#2020 状态，这些均在前 100 条内，且逐条用 `gh api`/`gh pr list --json` 独立确认了 `state=MERGED` 与 merge commit。**未把 100 条描述为全仓 merged 总数**。
- `gh issue list --state open --limit 200` → **9 条，未达上限**（无截断）。

### 2.2 前轮 G01–G07 / G08 review / #2019 / #2020 实际状态

| PR | 内容 | state | head SHA | merge commit | 结论 |
|---|---|---|---|---|---|
| #2011 | G02 GIS Intent-to-Execution Compiler | ✅ MERGED | `223c71803ee8` | `d5b858313a92` | 已在 origin/master，直接复用 |
| #2012 | G05? Pi 原生运行时契约一体化 | ✅ MERGED | `e34a27e80807` | `2024dd4c729b` | 已在 origin/master |
| #2013 | G03 数据资格判定链 | ✅ MERGED | `185df8267378` | `9e553083388c` | 已在 origin/master |
| #2014 | G05 情境工作台 | ✅ MERGED | `21c546f2e5e2` | `a4cf0a98f5a4` | 已在 origin/master |
| #2015 | G04 智能制图组合器 | ✅ MERGED | `ef1cb367fe7d` | `161c60041da0` | 已在 origin/master |
| #2016 | G06 GIS 能力生态 | ✅ MERGED | `8f605ef57ec0` | `8c24d375d90e` | 已在 origin/master |
| #2017 | G07 可靠性证据链 | ✅ MERGED | `2c18dffe4b70` | `e3c4d7e0524c` | 已在 origin/master |
| #2018 | G08 独立 review-fix-rereview | ✅ MERGED | `1b7f2d10efbe` | `c6e1b50a6755` | 已在 origin/master |
| #2019 | J7 post-dispatch 锁重入修复 | ✅ MERGED | `06a9241b1579` | `caacf9421f28` | 已在 origin/master；**不得重实现其锁借用** |
| #2020 | pnpm lock overrides 修复 | ✅ MERGED | `33d00d2f6547` | `8eb8aa188c14` | 当前 master 尖端 |

> **结论：G01–G07 + G08 review + #2019/#2020 全部已合并**，「七个 PR 尚未合入」的旧判断已作废。本任务**不整包 cherry-pick、不修改别人 PR、不替别人合并**。

### 2.3 唯一在途 PR（保护区）

| PR | state | head | base | 内容 | 本任务处置 |
|---|---|---|---|---|---|
| #1979 | OPEN（非 draft） | `30e5ec14e00c` | `master` @ `af41347e1a` | Dependabot: `tailwindcss 3.4.19 → 4.3.4`（frontend 依赖） | ⚠️ **避让** `frontend/package.json`、`frontend/pnpm-lock.yaml`、tailwind 配置；本任务不改动依赖声明 |

### 2.4 Open Issues（风险线索，非本任务清空队列）

`#1999`–`#2007` 共 9 条，全为 `bug,P1,needs-triage`。与本功能直接相关的风险线索：
- `#2002` 出版约束应用没有尊重**组件族锁** → 与 UPD-O06 的 user-wins 冲突语义相关，合并实现必须显式尊重锁。
- `#2004` 框选 >8 要素静默截断 / `#2006` 带 summary 的工具结果丢分区数值 → 与「有界摘要必须披露截断状态」相关，新工具回执必须带 `truncated` 标志。
- `#2007` OpenAPI 快照已过期 → 新增路由会加剧漂移；本任务需按权威生成器更新 `tests/quality/snapshots/openapi.json` 并诚实披露。
- `#2001` 换城市旧图层残留 / `#2005` 网络距离单位错 → 提醒差异判定必须带单位与范围维度。

## 3. 现有同类功能逐项判定（B / M0 强制项）

判定词表：`已有完整实现` / `已有但缺用户入口` / `部分实现` / `待开发` / `与在途PR冲突`。

| # | 能力 | 判定 | 权威落点（worktree 相对路径） | 本轮处置 |
|---|---|---|---|---|
| 1 | Runtime Snapshot freeze/list/inspect/resume/delete | ✅ 已有完整实现 | `app/api/routes/project_runtime.py:75-292`；`app/services/project_runtime/{freeze,resume,validation,store,promotion,rebind}.py` | **保留作回归**，不再实现「项目快照」 |
| 2 | missing/stale/recompute 恢复前预览 | ✅ 已有完整实现 | `app/services/project_runtime/validation.py:110-376` | 复用 `validate_runtime_snapshot` / `build_recovery_plan`；注意其 `dataset_version_changed` **只披露不触发重算** |
| 3 | `DatasetDescriptor.version_fingerprint` 漂移判定 | ✅ 已有完整实现 | `contract.py:121-132`；`freeze.py:195-223`；`validation.py:260-309`；`app/models/project.py:42-57` | 复用为 UPD-01 的权威版本证据 |
| 4 | lifecycle policy / GC plan / approve / execute / rollback / purge | ✅ 已有完整实现 | `app/api/routes/data_lifecycle.py`；`app/services/data_lifecycle/{gc_plan,policy,quota,service}.py` | **不另起 GC**；刷新采纳的 pin 释放必须与之一致 |
| 5 | 统一指纹 / 变更类别 / staleness verdict / 复用键 | ✅ 已有完整实现 | `app/lib/data/fingerprints.py:43-244`（`FingerprintSet` / `classify_change` / `staleness_verdict` / `compute_reuse_fingerprint`） | 直接复用为 UPD-02/03 的精确判定与保守推断原语 |
| 6 | 内容版本 / `SourceRevision` / `compare_revisions` | ✅ 已有完整实现 | `app/lib/data/versioning.py:36-165`；`app/services/data_lifecycle/service.py:212-256` | 复用 |
| 7 | 只读影响分析 `impact_analysis` | ✅ 已有但缺用户入口 | `app/services/data_lifecycle/service.py:328-366`（明确「不写状态，只给裁决」） | **复用**，不新建影响图执行器 |
| 8 | artifact 依赖图 / dependents 闭包 | ✅ 已有完整实现 | `app/services/artifact_registry.py:129-201` | 复用 |
| 9 | 受影响子图精确计算 | ✅ 已有完整实现 | `app/services/gis_harness/workflow_v4/recompute.py:115-191` `compute_affected_subgraph` | 复用 O(nodes+edges) 纯函数；不新建第二 DAG |
| 10 | workflow runtime 重算计划 / apply / 取消 / attempt / 锁 | ✅ 已有完整实现 | `app/services/workflow_runtime/{service,contracts,recompute,reuse,fingerprints}.py`；`app/api/routes/workflow_runtime.py:254-298` | **不重新实现 #2009/#2019 的锁借用**；只做薄接线 |
| 11 | MapSpec `expected_revision` CAS / `client_mutation_id` / user-wins | ✅ 已有完整实现 | `app/services/mapspec/lifecycle_engine.py:268-715`；`app/api/routes/mapspec_mutations.py` | 复用权威 mutation 入口 |
| 12 | 用户编辑（颜色/层序/可见性/组件族锁/文本）表达 | ✅ 已有完整实现 | `app/schemas/mapspec_mutation_schema.py`；`app/contracts/workbench_locks.py:18-67` | 复用；三方合并按稳定 entity ID |
| 13 | map operation preview / commit 事务回执 | ✅ 已有完整实现 | `app/services/gis_harness/map_operations/service.py:604-659` | 复用为「原子采纳」的落地门面 |
| 14 | map product version / 五维 diff / `analysis_recomputation_expected` | ✅ 已有完整实现 | `app/services/map_product_service.py:108-700`；`app/models/project.py:455-503` | 复用为「采纳后新 revision」与 style-only 不重算判定 |
| 15 | Lakehouse 数据集版本 / commit / rollback_branch / lineage | ✅ 已有完整实现 | `app/services/lakehouse/dataset_registry.py:377-907` | 复用；回退走 revert 式新 commit，不倒写 revision |
| 16 | Pi → ToolRegistry → ToolDispatchService → 类型化回执 → SSE | ✅ 已有完整实现 | `app/main.py:257-285`；`app/tools/__init__.py:11-149`；`app/services/tool_dispatch_service.py:108-282`；`app/agent_pi_bridge.py:480` | 只新增工具模块 + `_TOOL_MODULES` 一行登记 |
| 17 | 真实 Pi RPC + stub LLM + 真实 dispatch E2E 车道 | ✅ 已有完整实现（opt-in） | `tests/integration/test_pi_stub_llm_journeys.py`（`PI_JOURNEY_E2E=1` + `heavy` + `requires_node`） | 复用同一车道加 G10 场景；不伪造 GIS 执行 |
| 18 | `refresh_data_source` 工具 | ⚠️ 部分实现 | `app/tools/data_fabric_tools.py:638-680` | 只做元数据 sync + 健康度 + catalog 索引，**不写版本指纹、不触发 impact/staleness**；本任务不改动其语义，另建项目级刷新入口 |
| 19 | 「来源版本变化 → 刷新提案 → 影响预览」项目级契约 | ❌ 待开发 | 全仓 grep `refresh|impact|proposal|candidate|adoption` 无项目级刷新端点/模块（`app/**/*g10*` 无文件） | **本轮主工作量** |
| 20 | `dataset_version_changed` → `PendingChange` → `recompute_plan` 编排 | ❌ 待开发 | 未找到桥接代码 | **本轮主工作量** |
| 21 | 隔离候选运行 + user-wins 三方合并 + 原子采纳/放弃/回退 | ❌ 待开发 | 未找到 refresh candidate 模型 | **本轮主工作量** |
| 22 | 项目/数据/成果面板的刷新 UI 与「检查更新」入口 | ❌ 待开发 | 前端 `runtime-snapshots` 零匹配；stale 徽标仅 `components/sidebar/layers-tab.tsx:431` | **本轮主工作量** |
| 23 | 前端 stale 徽标 / 五维 diff / 双栏对比 / ConfirmDialog / a11y | ✅ 已有完整实现 | `components/shared/{status-badge,confirm-dialog,inline-notice,loading-state,empty-state}.tsx`；`components/sidebar/map-product-versions.tsx`；`lakehouse/version-workbench.tsx` | 复用组件与范式，避免全站改版 |
| 24 | i18n 双语强制闸 | ✅ 已有完整实现 | `frontend/test/i18n/key-completeness.test.ts`；`no-raw-cjk.test.ts`；`messages/{zh-CN,en-US}/*.json` | 新增文案必须双语同键、JSX 不裸 CJK |
| 25 | E2E 真实认证车道 | ✅ 已有完整实现 | `frontend/e2e/helpers/bootstrap.ts:47-122` `loginViaApi`；`E2E_MODE=real` | 复用，禁止 fake opener |
| 26 | `frontend/package.json` / pnpm lock / tailwind | ⛔ 与在途PR冲突 | #1979 | **禁止改动** |

## 4. 已验证缺口（本轮要补的纵向功能）

1. ❌ 没有把「项目挂载数据集的逻辑身份 + 内容版本 + 证据级别」汇总成**可比较的来源清单**（UPD-01）。
2. ❌ 没有**字段/要素/几何/栅格/覆盖范围**的差异判定，且没有「精确 / 保守推断 / 未知 / 未验证」的四态披露（UPD-02）。
3. ❌ 没有把 dataset drift 翻译成 `WorkflowChange` 并调用既有 `compute_affected_subgraph` / `impact_analysis` 产出**只读更新计划 + proposal fingerprint**（UPD-03）。
4. ❌ 没有**隔离候选命名空间** + baseline/current-user-edits/candidate 三方 user-wins 合并（UPD-04）。
5. ❌ 没有绑定「source-version set + proposal + expected MapSpec revision」的**幂等执行 / 崩溃恢复 / 原子 CAS 采纳 / 回退 / GC pin 释放**（UPD-05）。
6. ❌ 没有任何 UI 或 Pi 自然语言工具能走「检查 → 预览 → 执行 → 比较 → 采纳/回退」（UPD-06）。
7. ❌ 没有增量 vs 固定版本全量重算的**结果对拍**与性能报告（UPD-07）。

## 5. 未验证项（诚实披露，禁止冒充成功）

| 项 | 状态 | 计划验证方式 |
|---|---|---|
| Pi vendor 版本是否为 `earendil-works/pi v1.5.0`、gitlink / 构建产物 / `pi_doctor` | ⏳ 未验证 | 后续读取 `vendor/pi` gitlink、`scripts/build-vendor-pi.sh` 与 pi doctor；差异记兼容矩阵 |
| 真实浏览器 E2E 能否在本机跑通（Next dev + 真实后端 + 合法 owner） | ⏳ 未验证 | `E2E_MODE=real` 车道；跑不通则明确标「未验证」，不把组件测试提升为浏览器证据 |
| 线上 provider / 付费模型可用性 | ⏳ 未验证（不依赖） | 用 stub LLM + 真实工具 dispatch 验证调用链 |
| 线上 CI/CD | ⏳ 不轮询、不宣称 | 本地验证为交付依据 |
| 全仓 merged PR 总数 | ⏳ 未穷尽（100 条页满） | 已满足本任务对 #2011–#2020 的判定需要；不描述为完整列表 |
| `tests/quality/snapshots/openapi.json` 当前是否已漂移 | ⏳ 未验证（#2007 提示已过期） | 新增路由后用 `scripts/gen_api_docs.py` / `app/lib/quality/api_compat.py` 权威生成器复核并披露 |

## 6. 结论

底层能力（版本指纹、lineage、影响图、增量重算、CAS、user-wins、GC pin、Pi 分发）**已基本齐备**；G10 的真实工作量是把它们**纵向编排**成一条可感知、可预览、可采纳、可回退的产品闭环，并补上 API / UI / Pi 工具 / fixtures / 正反测试。禁止重新包装已有接口冒充新功能，禁止新建第二执行器/registry/MapSpec/session truth store。
