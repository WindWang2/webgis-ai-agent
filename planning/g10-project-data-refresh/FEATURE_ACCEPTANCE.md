# G10-PROJECT-DATA-REFRESH · FEATURE_ACCEPTANCE（MUST 旅程验收）

> 契约 A6：所有 MUST 用户旅程必须**同时**具备 ①可发现入口 ②真实服务调用 ③持久化/恢复语义 ④失败/取消处理 ⑤权限检查 ⑥必要用户反馈 ⑦正反测试 ⑧操作说明。
> 纯组件 UI/mock API、仅有 helper/schema/registry 声明、单纯增加 catalog 数量均**不算完成**。

| 旅程 | 入口 | 真实服务 | 持久化/恢复 | 失败/取消 | 权限 | 用户反馈 | 正反测试 | 操作说明 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| J1 检查项目来源变化 | 项目资产面板「检查更新」按钮 + Pi 工具 `check_project_source_updates` | `GET/POST /projects/{pid}/data-refresh/check` → `inventory` | 检查结果落 proposal 账本，刷新后可查 | 来源失权/预算不足 → `blocked` 且拒绝泄露差异 | `_auth_project` + `_verify_session_access` | 五态标记 + 证据级别 + 截断状态 | T-O01a/b | `docs/features/project-data-refresh.md` | ✅ |
| J2 解释某图为何过期 | 面板「为何过期」+ Pi 工具 `explain_stale_map_product` | `.../impact` 只读分析 → `artifact_registry` + `fingerprints` | 无写入（只读） | 证据不足 → `unverifiable`，不编造原因 | 同上 | 失效原因 + 依赖链 + 完整/截断 | T-O03a/b | 同上 | ✅ |
| J3 预览更新 | 「预览影响」按钮 + Pi `preview_project_data_refresh` | `.../preview` → `compute_affected_subgraph` + `impact_analysis` | proposal 持久化 + `proposal_fingerprint` | 漂移 → 409 强制重预览 | 同上 + 快照版本核验 | 增/删/改数量、最小重算子图、预算、可复用结果、保留的人工编辑、冲突 | T-O04a/T-O05a | 同上 | ✅ |
| J4 执行授权候选 | 「生成候选」+ Pi `execute_project_data_refresh` | `.../execute` → `workflow_runtime` + 隔离候选命名空间 | job/attempt 状态持久，崩溃可续 | 失败/取消 → 当前成果不变、候选可检查 | owner/session/project + 审批令牌 + 三元组绑定 | 进度/部分完成/失败可辨 | T-O07a | 同上 | ✅ |
| J5 候选对比 | 双栏对比视图 | `.../candidate` | 候选与基线引用 pin | 候选损坏 → 显式错误 | 同上 | 差异 + 冲突 + 保留项 | T-O06a/b | 同上 | ✅ |
| J6 采纳候选 | 「采纳」+ Pi `adopt_project_data_refresh` | `.../adopt` → `apply_mutation`(CAS) + `map_product_service` | 新 revision，旧快照不变 | revision 漂移 → 409；渲染未观察 → 不宣称地图成功 | 同上 + expected revision + proposal fp | 采纳回执三态分离 | T-O08a/T-O11a | 同上 | ✅ |
| J7 放弃候选 | 「放弃」+ Pi `discard_project_data_refresh` | `.../discard` → 释放 GC pin | 候选引用可回收 | 已采纳 → 409 | 同上 | 明确回执 | T-O09a | 同上 | ✅ |
| J8 查询/回退版本 | 版本时间线 + Pi `rollback_project_data_refresh` | `.../rollback` → 既有 `project_runtime.resume` / `rollback_branch` | revert 式新 revision | 不可变快照不可改 | 同上 | 回退后需重新观察 | T-O08a | 同上 | ✅ |

## 分层完成披露（禁止互相替代）

| 层级 | 判定依据 | 未达成时的措辞 |
|---|---|---|
| 工具执行成功 | 真实 dispatch 回执 `status=ok` | — |
| 任务执行成功 | 候选产物写入且检查通过 | 不得说「地图已更新」 |
| 地图已显示 | 真实浏览器观察 + 渲染回执 | 缺浏览器观察 → `map_observation=unverified`，不阻断离线结果保存 |
| 文件已导出 | 导出端点产物校验 | 不得用「工具成功」替代 |
| 内容已发布 | 发布端点回执 | 本任务**不做公开发布** |

## Pi 语义要求（A5）

- 沿既有链：`Pi → 动态工具发现/资格 → ToolRegistry → ToolDispatchService → 领域服务 → 类型化回执 → SSE/前端投影 → 真实观察`。
- 至少 1 个真实 Pi RPC + stub LLM + 真实工具 dispatch/领域计算端到端场景；不把 stub 工具返回值冒充真实 GIS 执行。
- 服务端复核：参数、数组长度、有限数、单位、owner/session/project、审批令牌、快照版本；客户端 `verified/approved/owner_id` 非权威。
- 摘要保留关键数值、单位、完整/截断状态与警告。

## 当前总体状态

🟢 **后端闭环 + 前端 UI + Pi 工具已实现并本地验证**（S0–S9）。

| 证据 | 结果 |
|---|---|
| 后端 PU/DI/PI 测试 | 55 passed（exit 0，含 R1/R2/R3 审查回归 15 项 + 8 端点 404 探针 + 内部故障可观测） |
| 前端组件单测 + i18n 闸 + E2E 注册表闸 | 30 passed（exit 0） |
| TypeScript `pnpm run typecheck` | exit 0 |
| ruff（本任务全部新增 Python 文件） | All checks passed |
| OpenAPI / API docs 生成物闸 | 15 passed（官方生成器刷新后） |
| 增量/全量结果对拍 | 容差 0 逐字段等价；3 vs 6 节点（见 PERFORMANCE.md） |
| 真实浏览器 E2E | ⏳ **未验证**（j13 real-only，需真实后端 + 预置账号） |
| 独立审查 | R1 冷读 3×P1 + 18×P2 → 全修；R2 二审发现 1×新 P1 + 4×P2/P3 → 全修；R15 终审 **PR-ready**（0×P0/P1） |

⏳ 表中所有 MUST 旅程的「浏览器观察」项均为 `unverified`，不把工具成功/任务成功/版本采纳互相替代，不伪造 renderer ACK。操作说明见 `docs/features/project-data-refresh.md`。
