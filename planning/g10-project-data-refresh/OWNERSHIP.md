# G10-PROJECT-DATA-REFRESH · OWNERSHIP（所有权与保护区）

## 1. 本任务主修改域（只在此新增/改动）

| 域 | 路径 | 允许动作 | 三层标签（层 / 职责 / 依赖） |
|---|---|---|---|
| 刷新契约 | `app/services/project_runtime/refresh/contracts.py` | 新增 | domain / 类型化 proposal·diff·receipt / pydantic + `app/lib/data/fingerprints` |
| 来源清单 | `app/services/project_runtime/refresh/inventory.py` | 新增 | domain / UPD-01 版本证据汇总 / `ProjectService` + `project_runtime.freeze` + `ProjectDataset` |
| 差异判定 | `app/services/project_runtime/refresh/diff.py` | 新增 | domain / UPD-02 四态差异 / `fingerprints` + `dataset_descriptor` + lakehouse |
| 影响预览 | `app/services/project_runtime/refresh/impact.py` | 新增 | domain / UPD-03 只读计划 + proposal fingerprint / `artifact_registry` + `workflow_v4.recompute` + `data_lifecycle.service` |
| 三方合并 | `app/services/project_runtime/refresh/merge.py` | 新增 | domain / UPD-04 user-wins / `mapspec` + `workbench_locks` |
| 执行采纳 | `app/services/project_runtime/refresh/execution.py` | 新增 | domain / UPD-05 隔离候选·CAS·回退 / `workflow_runtime` + `map_operations` + `project_runtime.store` |
| 刷新账本 | `app/services/project_runtime/refresh/store.py` | 新增 | infra / proposal 持久化·lease·幂等 / 复用 `project_runtime.store._atomic_write_json_sync` 与 `runtime_snapshots_dir` 目录约定 |
| 编排门面 | `app/services/project_runtime/refresh/service.py` | 新增 | application / 对外单一入口 / 以上全部 |
| REST | `app/api/routes/project_data_refresh.py` + `app/schemas/project_data_refresh_schema.py` | 新增 | api / 权限门 + 有界请求/响应 / `project_runtime._auth_project` 同款门 |
| Pi 工具 | `app/tools/project_data_refresh_tools.py` | 新增 | tools / 自然语言语义 + 类型化回执 / `ToolRegistry` + 上述 service |
| 前端 API | `frontend/lib/api/project-data-refresh.ts` | 新增 | client / typed fetch / `apiFetch` + `fastGet` |
| 前端面板 | `frontend/components/sidebar/project/data-refresh-panel.tsx` | 新增 | ui / 检查→预览→执行→比较→采纳 / 既有 shared 组件 |
| i18n | `frontend/messages/{zh-CN,en-US}/project.json` | **追加键**（不新建 namespace） | i18n / 双语同键 / 避开 `lib/i18n/messages.ts` 共享登记 |
| 测试 | `tests/data/test_project_data_refresh_*.py`、`frontend/**/*.test.tsx`、`frontend/e2e/journeys/j13-*.journey.ts` | 新增 | test / 正反 + 三场景 + 对拍 | 既有 conftest / bootstrap |
| 文档/账本 | `docs/features/project-data-refresh.md`、`planning/g10-project-data-refresh/*` | 新增 | docs / 证据与操作说明 | — |

## 2. 共享接缝（必须集中小提交、additive 最小化）

| 接缝 | 文件 | 最小改动 | 回归义务 |
|---|---|---|---|
| 路由注册 | `app/main.py` | `import` + 一条 `app.include_router(...)` | 现有端点 200/权限语义不变 |
| 工具导入入口 | `app/tools/__init__.py` `_TOOL_MODULES` | 追加 1 行模块名 | `init_tools` 既有工具数量/签名不减 |
| OpenAPI 生成物 | `tests/quality/snapshots/openapi.json` / `docs/api-docs.md` | 仅用权威生成器更新 | 不抬阈值、不改 baseline、不删用例 |
| 前端 tab 挂载 | `frontend/components/sidebar/project/project-assets-section.tsx` | `ASSET_TABS` 追加 1 项 + 一个 tabpanel 分支 | 既有 5 个 tab 行为不变 |
| i18n catalog | `frontend/messages/*/project.json` | 追加键（两目录同键） | `key-completeness` / `no-raw-cjk` 闸全绿 |
| E2E 注册表 | `frontend/e2e/helpers/registry.ts` | 追加 1 条 journey | `changed-lanes` 双向闸 |

> 不写全局 `.goal-loop-ledger.md`；正式证据只归本任务目录。

## 3. 保护区（禁止改动 / 禁止触碰）

| 对象 | 原因 | 规则 |
|---|---|---|
| 主工作树 `/home/kevin/projects/webgis/webgis-ai-agent` | 他人正在 `zcode/review-g3dv2-fixes` 上工作，有 `m vendor/pi` 与 30 个未跟踪 skills | **只读**；不 stash / clean / reset / checkout / commit |
| `master` 本地分支 | 落后 `origin/master` 且属共享 | **只读**，本任务基点固定 `origin/master` |
| 其它 34 个 worktree 与其分支 | 他人并行开发 | 不切换、不删除、不合并 |
| 在途 PR #1979（dependabot tailwindcss） | 唯一 OPEN PR | 不动 `frontend/package.json`、`pnpm-lock.yaml`、tailwind 配置；不改它的分支 |
| #2011–#2020 已合并代码 | 不是「在途」，但属既有权威实现 | 复用不重写；尤其不重新实现 #2009/#2019 的锁借用 |
| Data Fabric 下载/摄入、资格/CRS 校验、MapSpec mutation、workflow 重算服务 | 依赖域 | 只做薄接线，不重构、不改既有签名/默认值/错误语义 |
| 不可变快照 / 已交付固定版本 | 契约 M0/B | 不倒写 revision、不删快照、不让交付成果随源数据悄然变内容 |
| G09 参数表单/批次调度、G11 冻结产物消费 | 三线边界 | 不修改；只提供「哪些结果过期/更新到哪个版本」的事实 |
| Open Issues #1999–#2007 | 风险线索 | 不关闭、不顺手清空 |

## 4. 三线边界（B1）

| 线 | 职责 | 本线接口 | 禁止 |
|---|---|---|---|
| **G10（本线）** | 数据版本变化 → 影响预览 → 只重算受影响产物 → 保留人工调整 → 原子采纳/回退 | 输出 `RefreshProposal` / `AdoptionReceipt` / stale 事实 | 不自动改写报告、不公开发布、不做后台自动抓取（除用户显式启用项目策略） |
| G09 | 保存方法并批跑新任务 | 消费本线的事实 | 本线不改其参数表单/批次调度 |
| G11 | 消费已冻结产物版本 | 消费本线的版本事实 | 本线不改其报告/发布链 |

三线**无待合并代码硬依赖**：均基于当前 `origin/master` 已存在的服务公开接口协作；每份 Prompt 可独立执行。

## 5. 工程纪律

- 每功能切片一个可回滚 commit；不把无关重构塞进同一 commit。
- 修改前先写可失败的测试；正例必须有明确预期数值/状态（不是「返回非空」）。
- 所有测试按 `pure unit / real domain integration / real Pi+stub LLM / browser+real backend / live external service` 标记。
- 工具成功 ≠ 任务执行成功 ≠ 地图已显示 ≠ 文件已导出 ≠ 内容已发布；分别披露。
- 浏览器证据必须来自真实认证入口/合法 owner；不可用 fake opener / 伪 ACK / 截图占位。
- 服务端严格复核参数、数组长度、有限数、单位、owner/session/project、审批令牌、快照版本；客户端传来的 `verified/approved/owner_id` 一律不作权威。
- 大 FeatureCollection / 大表 / 全量日志不进模型上下文：用有权限 ref + 分页 + 有界摘要，摘要带关键数值、单位、完整/截断状态与警告。
