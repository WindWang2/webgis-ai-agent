# G19-TYPED-FIELD-COMPUTE — PR_HANDOFF

> PR 已创建；复审结论回填后刷新 PR 正文与 head SHA。

| 项 | 内容 |
|---|---|
| PR URL | https://github.com/WindWang2/webgis-ai-agent/pull/2074 （`gh pr create` 真实返回值） |
| branch | `zcode/goal-g19-typed-field-compute-20261110T131120Z` |
| worktree | `C:/Users/wangj.KEVIN/projects/webgis-worktrees/goal-g19-typed-field-compute-20261110T131120Z` |
| base SHA | `816705218a5aa6202bf15d745cb31cddb3437f44`（`origin/master`，PR 前再次 fetch 确认未前进） |
| head SHA | 本轮复审修复 commit（`fix(g19): 复审 P1/P2 清偿 ...`，见 PR 页面最新 SHA）；前序 `30eb8fcfb`，base `8167052`，0 behind |
| BASE_BRANCH | `master`（`gh repo view --json defaultBranchRef` 实测） |
| 状态 | 未自动 merge / 未发布 / 未删除分支或 worktree |

## 函数集与版本

- `IR_VERSION = g19-ir-1`、`FUNCTION_SET_VERSION = g19-fc-1.0`、`RECEIPT_VERSION = 1`（全部参与 `compute_fingerprint`）。
- 首批 **28 个函数**（数值 7 / 条件 3 / 文本 6 / 代码与转换 4 / 日期 3 / 单位 1 / 几何 4）+ 13 个闭合算子。
- 函数表由 `app/services/data_preparation/field_compute/functions.py::FUNCTIONS` 单一驱动；面向用户文档 `docs/field-compute-contract.md` 由域内 `gen_help.py` 从真实契约生成（`python app/services/data_preparation/field_compute/gen_help.py`）。
- 新增函数必须同步升 `FUNCTION_SET_VERSION`、补边界测试并重生成文档。

## 支持数据形态与规模

| 项 | 事实 |
|---|---|
| 数据形态 | ref-backed GeoJSON `FeatureCollection`（含 `crs`/`crs_source`）与真正的 `rows` 非空间表格；UI 按 `data_shape` 如实披露，不宣称未支持形态 |
| 行数上限 | 50,000（超限启动前 `ROW_LIMIT_EXCEEDED`，不静默截断） |
| 列 / AST / 每行 | ≤8 列；AST 节点 ≤64、深度 ≤12；每行操作数 ≤256 |
| 字符串 | 输入 ≤2048、结果 ≤512 字符 |
| 批次 | 2,000 行/批；CPU 计算 `asyncio.to_thread` offload；预估操作数闸 5,000,000 |
| 一致性 | 一次性冻结快照；发布前二次核对内容 + schema 双锚 |
| 有界基准 | 真实执行：Windows AMD64 / Python 3.13.9，5,000 行 × 3 列、batch 2,000 → 1.008s、`tracemalloc` 峰值 1.81 MiB（仅执行期新增分配）、0 错误；**万行级基准未跑**，不承诺 10 万/千万行能力 |

## 共享文件接线位置

纯新增行，无格式化/重命名/删除（详见 `INTERFACES.md §2`）：
`app/main.py`（+2）、`app/tools/__init__.py`（+1）、`frontend/lib/i18n/messages.ts`（+5）、`frontend/components/sidebar/data-sources-tab.tsx`（+2）、`scripts/generate_endpoint_scope_matrix.py`（+1 条 `DOMAIN_RULES`）。
生成物按本分支当前版本独立完成并随 PR 提交：`tests/quality/snapshots/openapi.json`、`docs/api-docs.md`、`docs/dev/endpoint-scope-matrix.csv`（10 个新端点，含 `cancel`）。
独立评审修复后验证：既有 OpenAPI 短名 `PlanOut`/`PlanResponse`/`PlanListResponse` 与既有端点 `$ref` **零改写**，`git diff 8167052..HEAD --stat` = 43 文件 +10217/-3。

## 对 G12–G21 兼容

见 `INTERFACES.md §5`。要点：只读已有 refs（不依赖 G17 协议 / G18 下载器）；产出新 `ref:fc-*` 不写原件或 G14 undo journal；工具经既有 `@tool`/dispatch 注册（不改 G12 runtime / G13 资格政策）；不改 MapSpec schema 的 G15/G16/G20/G21 子模型；只加功能专属子组件与一处局部挂载（让出 #2032 全局 UI 重设计）；不做 #2022 自动刷新。PR 前 `git fetch origin --prune` 确认 `origin/master` 未前进，开放 PR 仍为 12 个（#2032/#2022/#2021 + 9 dependabot），无 G12–G21 在途 PR。

## 已执行验收

| 类 | 结果 |
|---|---|
| 后端定向 | **81 passed（38.81s）= 核心 44 + 评审修复 23 + API 8 + Pi 工具 6**，单进程 `--no-cov --basetemp` |
| 契约门禁 | **23 passed**（OpenAPI 快照 / api-docs drift / 端点矩阵） |
| 前端定向 | **21 passed**（组件 8 + 错误信封解析 3 + i18n 键完整性 10），threads pool 单 worker |
| 静态 | `ruff check` All checks passed；`tsc --noEmit` 新增文件 0 错误（8 个既有 `scene3d/*`）；ESLint 新增文件 0 警告；i18n 143/143；`git diff --check` 通过 |
| 已知限制 | 发布失败回滚覆盖稳定键/session ref/项目清单；descriptor/provenance/artifact 三类 additive 登记无删除 API，不随回滚移除（指向已删除 ref，不构成可读成果） |
| 21 场景 | 见 `TEST_MATRIX.md`；UI 单列、真实 Pi/渲染回执、全量前端构建等边界如实声明 |
| 线上 CI | **不等待、不轮询**（仓库 CLAUDE.md:41 + 任务书 A）；门禁未关闭/未降级 |

**既有失败隔离**：`test_preparation_plan_v1.py` 24/9（Windows `dsd-v1:<sha>.json` 冒号 WinError 123）、`tsc` 8 个 `scene3d/*` 错误，均在干净 `8167052` 基线复现且位于未改动文件；本 PR 的 descriptor/provenance/artifact 登记一律 additive + `degraded` 如实披露。

## 独立 review / fix / rereview

- 第一轮：独立 reviewer（未参与实现）冷读判定不可进 PR，4 个 P1 + 9 个影响验收 P2；详见 `REVIEW.md`。
- 修复：最小集合 1–9 全部完成 + owner/错误信封/夹具等附加项，commit `031da7e5c`，12 项针对性回归全通过。
- 第二轮复审（两位独立 reviewer 冷读 `031da7e5c`）：13 项原阻塞 10 彻底修复 / 2 部分修复 / 1 以如实声明达成；新发现 1 个 P1（最终收据 upsert 失败仍有可见半成品 + 未发布成果可被幂等命中）与 3 个影响验收 P2（cancel 端点 500、升级残留 `PROJECT_SAVE_FAILED`、`_CANCEL_REQUESTS` 无界/陈旧键）。
- 第三轮修复：`probe_publish()` 发布面探测、稳定键先写+ring 失败回滚、最终 upsert 失败撤销 ref 与项目清单、升级清理 degraded、`_ACTIVE_EXEC` + TTL 取消语义、cancel 端点 typed 映射与真实 ASGI 覆盖、`cancelled` 可恢复、前端空集合/取消三态文案。新增 7 项回归（17 review + 2 API）。
- 第三轮复审（全新 reviewer，冷读 `9461505c9`）：4 个遗留项 3 项判定已修，另发现 3 个功能性 P2（排队取消被先完成者清掉、升级失败丢降级标记、最终失败未删稳定键且删键失败无披露）+ 2 项证据映射偏差。
- 第四轮修复：条件化取消键清理 + 两并发任务回归；恢复 `PROJECT_SAVE_FAILED`；`drop_published()` 删除稳定键并在失败时写 `STABLE_KEY_ROLLBACK_FAILED`；TTL 剪枝跳过活跃执行；receipt 层 ring 失败测试与组件空集合/取消三态测试。
- 第四轮复审：待独立 reviewer 冷读 `9461505c9..HEAD`；**不得由作者自行标记通过**。

## 迁移 / 回退

纯新增域 + additive 接线，无数据迁移、无 DB schema 变更、无破坏性 API 变更。回退 = revert 本 PR 的全部 commit；已生成的 `ref:fc-*` 是会话内数据，不依赖迁移。

## 未测限制（如实）

1. 真实 Pi 进程 + stub LLM 全链路 → MapSpec mutation → 浏览器渲染回执：无 Node extension/runtime 与渲染服务；已用真实 `ToolRegistry.dispatch` 覆盖数据面闭环，MapSpec/渲染保持 `not_evaluated`，不伪造。
2. 前端全量构建 / 全量 Vitest / Playwright 浏览器旅程：同机 4 个 worktree 并行 + 资源纪律下未跑；只跑定向测试与静态检查。
3. 万行级峰值内存基准：未跑。
4. 线上 CI 结果：按授权不等待，PR 中如实标注「未等待」而非「通过」。
