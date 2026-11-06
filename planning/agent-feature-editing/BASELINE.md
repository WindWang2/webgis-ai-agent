# BASELINE — Agent 驱动的要素编辑（/goal 03）

- 审计基线锚点（任务书）：`4b3629c90ee977e287f7b1fb4fd827b0fad7428c`（PR #1867 合并后，2026-10-29 观察）
- 开工实核：`git fetch origin master` 因本机网络 TLS 故障失败（github.com 与 api.github.com 全端点 `SSL routines::unexpected_eof`，多次重试含退避仍失败）；本地 `origin/master` 引用 = `4b3629c90`（与锚点一致，且锚点即 2026-10-29 当天观察）。**风险**：无法读 open PR/issue 快照 → 已在 PR_OVERLAP_MATRIX.md 记录基于本地 log 的合并 PR 审计 + 网络/PR 二次去重承诺（PR 创建前重试）。worktree 从该 `origin/master` 新建。
- 工作分支：`zcode/gis-agent-feature-editing-vnext`（worktree `../webgis-worktrees/gis-agent-edit`）
- 主仓工作树在 `master`（本地分支 7b4e41667，落后 origin/master）；**master 只读，未做任何写操作**。

## master 现状归档（production / partial / scaffold-only / absent）

依据三路只读 subagent 勘察（后端数据层 / 工具面 / 前端地图面，2026-10-29，HEAD=4b3629c90）+ 主 Agent 复核关键文件：

| 能力 | 判定 | 证据 |
|---|---|---|
| 数据集摄入→session ref（Redis/内存，`{"geojson":FC}` 信封） | production | `app/services/data_ingest/pipeline.py:119-305`；`session_data_redis.py:359 store/468 overwrite` |
| 要素级读取（整包/分页/单要素/MVT） | production | `app/api/routes/layer.py:80-313`；`feature_pages.py`；`mvt.py` |
| **要素稳定 ID（摄入铸造）** | **absent** | 摄入不铸造 fid；单要素查找靠源 id 键碰运气 `layer.py:244-259`；分页锚=数组下标（仅 revision 内稳定） |
| **要素级写入（属性/几何 patch）** | **absent** | 全仓 grep `update_feature/patch_feature/edit_feature` 无生产路径；layer 路由无 POST/PATCH |
| 整 ref 原位覆写（digest 变→content_revision bump） | production（无要素级调用方） | `session_data_redis.py:468-535` |
| new-ref 修复/变换（autofix/repair，绝不覆写源） | production | `data_quality/autofix.py:247`；`repair_execution.py:101-197` |
| 版本账本（artifact_revisions append-only + lakehouse 版本链） | production（PatchSet 无实现，`delta` 仅词表占位） | `artifact_revisions.py`；`lakehouse/dataset_registry.py:760-861` |
| CAS/幂等/锁（mutation_id dedup 环、expected_revision、SessionLockRegistry、IdempotencyMiddleware） | production | `mapspec/lifecycle_engine.py:265-427`；`distributed_lock.py:490`；`core/idempotency.py:167` |
| ref 失效权威（epoch bump + 派生缓存清除 + 观察者钩子） | production | `ref_lifecycle.py:101 invalidate_ref_caches` |
| MapSpec 源 ref 形态 + `content_revision` 盖章/消费 | production | `mapspec/pipeline.py:120-133` 盖章；`frontend/lib/session/map-state-restore.ts:352-354` 消费 |
| data_quality 几何算子（make_valid/闭环/自交/重复顶点吸附） | production（整集变换，非要素级） | `data_quality/rule_functions.py:132-842`；`spatial_repair_pipeline.py:64-99` |
| 工具注册/分发（tier/side_effect/tier3 闸/Pi 面投影/receipt） | production | `app/tools/registry.py`；`pi_native_surface.py`；`pi_surface_receipt.py` |
| Interaction IR V1（13 kind，feature_selected 含 dataset_ref+feature_id，无属性值） | production（#goal02 已合并） | `interaction_ir/contracts.py:100-166` |
| 选中体系（transient，selection-store，≤50 ids，有界属性投影） | production | `frontend/lib/selection/selection-store.ts:57-86` |
| workbench undo（CAS 反向重放命令栈） | production（图层级命令，无要素级） | `frontend/lib/workbench/undo.ts:31-184` |
| 用户确认 UI 先例（ClarificationCard/PlanProposalCard/ConfirmDialog） | production | `frontend/components/chat/clarification-card.tsx` 等 |
| 前端差异/预览先例（ComparisonView/VersionWorkbench diff） | production | `components/map/comparison/comparison-view.tsx`；`version-workbench.tsx` |
| sketch（transient 草图，无服务端持久化） | production | `frontend/lib/edit/sketch-store.ts`；`sketch-editor.tsx`（622 行） |
| 会话冲刷/多标签防护（epoch/客户端实例/CAS LWW 收敛） | production | `use-workspace-session.ts:166-357`；`workbench/collab.ts` |
| **dataset 级读写权限分离（session ref 面）** | **absent**（仅 legacy layers 表有 read/write/admin） | `db_model.py:187-206`；session ref 只有 owner 全权 |
| 组织审计账本（audit.py）/per-session provenance | production | `audit.py`；`gis_world_state/provenance.py:33-45` |

## 数据所有权表（M1 交付，草图 vs 数据要素 vs MapSpec 视图）

| 层 | 真相宿主 | 生命周期 | 写通道 | 本任务是否触碰 |
|---|---|---|---|---|
| transient 草图（sketch） | `frontend/lib/edit/sketch-store.ts` 模块 store | 会话/刷新即弃（保存=文件下载） | recordSketchCommand（本地 undo） | 否（保持原样） |
| 地图选中（selection） | `selection-store.ts` + HUD selectedFeature 快照 + IR ledger | transient，切换冲刷 | publishSelection | 只读消费（preview 目标解析） |
| MapSpec 视图（显隐/样式/组件） | 服务端权威 mapspec doc + 前端 committed/pending 游标 | 会话持久（checkpoint/durability） | mutation engine（CAS/user-wins） | 仅新增 revision 戳刷新 intent（最小 patch） |
| **持久数据要素（dataset feature）** | session store ref 载荷（`{"geojson":FC}`）+ content_revision | 会话持久（durability write-back） | **本任务新增**：feature_edit journal + overwrite CAS | **核心新增** |
| 原始上传工件 | `data/uploads/{id}/original.geojson` | 永久 | 只读（编辑绝不触碰） | 否 |

## 与任务书"主要编辑边界"核对

- `frontend/lib/edit/**`：已存在（仅 sketch）；本任务新增 `frontend/lib/feature-edit/**`（不改 sketch 语义）
- `frontend/components/map/sketch-editor.tsx`：最小改动目标改为**零改动**（预览覆盖层独立组件，复用其自管 source 模式）
- `frontend/lib/selection/**`：只读消费（selection → preview 目标），不改 store
- `app/services/gis_world_state/**`：经既有门面 apply_gis_mutation 调用（provenance 记录），不改核心
- `app/services/mapspec/**`：契约钩子 = 新增 RefreshDataRevisionIntent（intents/registry/handlers/taxonomy 四处最小行级 patch）
- 新增 feature-edit 服务：`app/services/feature_edit/` + `app/api/routes/feature_edits.py` + `app/tools/feature_edit_tools.py`
