# G10 · 项目数据版本刷新 — Pull Request 正文

## 新增用户功能

让用户在更新学校点 / 行政统计表 / 栅格数据后：

1. **检查更新**（手动触发）→ 项目资产面板新增「数据更新」页签，展示来源版本清单
   （稳定逻辑身份 `dataset:<id>`、内容版本 `content_hash`、证据级别、
   `pinned/refreshable/ephemeral/missing/version_unknown` 五态）、变化数与截断状态。
2. **预览影响**（只读）→ 精确新增/删除/属性/几何变化计数、字段/单位/CRS/覆盖范围/分母
   差异、将重算的最小可证明子图、可复用结果、预计预算、保留的人工调整、冲突列表，
   绑定输入版本集 + MapSpec revision + 用户锁 + owner 的 `proposal_fingerprint`。
3. **生成隔离候选** → 在 `data-refresh/` 命名空间产出候选，当前可见地图零触碰；
   刷新页面后从服务端 proposal/status 恢复候选与进度。
4. **user-wins 三方合并** → 按稳定 entity ID 保留颜色、透明度、可见性、层序、
   组件位置/族锁、文本；被删要素标注 / 字段移除 / 隐藏层显式冲突。
5. **原子采纳 / 放弃 / 回退** → 采纳走既有 session 锁 + `MapSpecStore.save_mapspec`
   单事务 CAS（revision 单调）；放弃释放候选 GC pin；回退创建新 revision 或走既有
   `resume_runtime_snapshot`，**不倒写 revision、不改不可变快照**。
6. **Pi 自然语言工具** 8 个（检查 / 解释过期 / 预览 / 执行 / 采纳 / 放弃 / 回退 / 状态），
   沿 `ToolRegistry → ToolDispatchService → 领域服务 → 类型化回执 → SSE` 既有链。

按钮明确区分「只计算」与「改变项目当前版本」；破坏性动作经 `ConfirmDialog`。

## 复用基础（不重新实现）

- `app/lib/data/fingerprints.py`：`classify_change` / `staleness_verdict` /
  `compute_reuse_fingerprint` / `canonical_fingerprint`
- `artifact_registry` / `data_lifecycle.service.impact_analysis`：依赖图与只读裁决
- `workflow_v4.recompute.compute_affected_subgraph`：受影响子图（不建第二 DAG）
- `workflow_runtime/*`：重算计划 / attempt / 取消 / 锁（不建第二执行器）
- `workbench_locks` / `session_lock_registry` / `MapSpecStore.save_mapspec`：user-wins + 原子 CAS
- `project_runtime/*`：runtime snapshot、resume、目录与原子写原语
- 未新建第二 registry / MapSpec / session truth store；刷新状态只是项目运行时体系内的领域账本

## 真实证据（本地验证）

| 项 | 结果 |
|---|---|
| 后端 PU/DI/PI 测试 | **55 passed**，exit 0（含 R1/R2/R3 审查回归 15 项） |
| 前端组件单测 + i18n 闸 + E2E 注册表闸 | **30 passed**，exit 0 |
| `pnpm run typecheck` | exit 0 |
| `ruff check`（本任务全部新增 Python） | All checks passed |
| OpenAPI / API docs 生成物闸 | **15 passed**，exit 0 |
| 增量 vs 全量对拍 | 容差 0 逐字段等价；3 vs 6 节点、IO 估算减半（见 `PERFORMANCE.md`） |
| 独立审查 | R1 冷读 → Fix → R2 冷读 → Fix → R3 终审 **PR-ready（0×P0/P1）**；审查者全程未改文件 |
| 关键验收 | 在最终代码上重复两遍（后端全套 + 生成物闸 + ruff） |

验收矩阵 / 循环账本 / 功能验收：`planning/g10-project-data-refresh/`。
操作说明：`docs/features/project-data-refresh.md`。

## 破坏性变更 / 迁移

- **无数据库迁移、无既有 API 签名/默认值/错误语义变更。**
- `app/main.py` / `app/tools/__init__.py` 各 1 行 additive 注册；`project-assets-section.tsx`
  追加 1 个 tab；双语 `project.json` 追加 45 个键。
- REST 新增 12 个端点（OpenAPI `433 → 445` operations，**无端点删除**）。
- ⚠️ `tests/quality/snapshots/openapi.json` / `docs/api-docs.md` 按**官方生成器**
  （`API_SNAPSHOT_UPDATE=1 pytest ...`、`python scripts/gen_api_docs.py`）刷新。
  diff 中除本任务 12 个新端点外，还包含 **master 既有漂移**（#2007，例如
  `review_schema__ProposalListResponse` 命名与 `GET /export/diagnostics` 描述），
  这是刷新生成物的必然结果；本 PR **未抬阈值、未删用例、未加 skip、未吞异常**。
- 在途 PR #1979（dependabot tailwindcss）保护区：`frontend/package.json` /
  `pnpm-lock.yaml` / tailwind 配置**未改动**。
- ⚠️ **共享接缝潜在合并冲突**：在途 PR #2021（G11 证据化地图简报）也追加
  `app/main.py` 与 `app/tools/__init__.py`。本分支在这两个文件各只追加 **1 行**
  （语义区域：`project_runtime_routes` 之后 / `capability_ecosystem_tools` 之后），
  与 G11 的追加位置不同，通常可自动合并；若 G11 先合入，需 rebase 并重跑
  路由注册与工具注册相关测试。本 PR **未修改 G11 分支、未替其合并**。

## 未验证项（诚实披露）

- ⏳ **真实浏览器 E2E 未跑**：`frontend/e2e/journeys/j13-project-data-refresh.journey.ts`
  为 `E2E_MODE=real` + 真实认证入口（`loginViaApi`），需运行中的真实后端、
  `E2E_USER/E2E_PASS` 及 `E2E_G10_PROJECT_ID/SESSION_ID`。本机未跑通，
  **不把组件测试提升为浏览器证据**。
- ⏳ 完整「真实 Pi RPC + stub LLM」子进程车道（`PI_JOURNEY_E2E=1`，需 vendor/pi 构建）
  未跑；本轮用 `PiToolRequest → dispatch_tool` 真实入口 + 真实 `ToolDispatchService`
  验证调用链。线上 LLM provider 可用性未验证。
- `map_observation` 全程 `unverified`：**工具成功 ≠ 任务成功 ≠ 地图已显示 ≠ 版本已采纳 ≠ 已发布**，
  不伪造 renderer ACK；本 PR 不做公开发布。
- 性能结论来自固定合成 Oracle 的纯计算微基准，不含真实 GIS/Redis/渲染耗时，
  不外推线上；max RSS 是同进程峰值而非节省量。
- 线上 CI/CD 未轮询、未宣称通过；本地验证为交付依据。

## 残余风险（不阻断，建议 follow-up）

- `_match` 词边界匹配比子串更严格：`source` 中 tail 被字母数字包围时回退第一个 token，
  多来源版本证据绑定可能不精确；建议改为显式 `source → logical_id` 映射。
- `preview` 吞 `list_snapshots` 异常 → `baseline_snapshot_id=""`（结构化日志可补）。
- `model_copy()` 不触发 Pydantic 重验证（框架既有行为；生产路径走 dict 构造）。
- OpenAPI 中 `ProposalListResponse` schema 被重命名（R1 起存在的 master 既有漂移）。

## 回滚方式

1. 已采纳：项目面板「回退」或 `POST .../rollback` → 新 revision 前向恢复 /
   既有 `resume_runtime_snapshot`（新 session）；不倒写、不删用户后续数据、
   不改不可变快照。回退后地图需重新观察。
2. 撤销功能：删除本分支/worktree 即可 —— additive 模块 + 两处注册 + 一个资产 tab，
   无破坏性迁移；候选/基线数据仅位于 `DATA_DIR/workspaces/<project>/data-refresh/`。
3. 「放弃候选」释放 GC pin，blob 保护仍归既有 artifact lifecycle 引用计数。

## 发布纪律

- 不自动 merge、不关闭他人 Issues、不删除未合并 worktree。
- 分支为独立 `zcode/goal-g10-project-data-refresh-20261108T153716Z`，
  基于 `origin/master`；提交前已重新 fetch 并同步最新主干。
