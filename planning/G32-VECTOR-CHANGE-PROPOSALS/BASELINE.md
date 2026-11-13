# G32 BASELINE — 持久要素变更提案与字段、几何冲突审阅

- 执行时间: 2026-11-12/13 (UTC)
- 目标仓库: WindWang2/webgis-ai-agent
- base SHA: `ca7b3ec273f2b43336852951e2292a416b2238e6` (origin/master, PR #2087 merge;与任务快照一致,fetch 后无新提交)
- worktree: `/home/kevin/projects/webgis/webgis-worktrees/g32-vector-change-proposals-20261112T174659Z`
- branch: `zcode/g32-vector-change-proposals-20261112T174659Z`
- vendor/pi pin: `b4afdddf44e059c67ccf5097e5317d913521b470` (v1.5.2, submodule init 完成;不改 vendor)

## 执行时 PR 状态复核 (2026-11-12T17:46Z)

- open PR: 仅 #2094 (G30 可执行 GIS 技能作业) — 与 G32 无文件所有权冲突(G30 改 `app/services/gis_skills*`、jobs 入口;本任务新增 `app/services/feature_proposals/` + feature_edit 最小适配)。
- 最近 merged: #2087–#2093 (G22–G28) 已入 base;planning/G22..G28 已在 master,REVIEW 记录已读。
- 并行 worktree: g29/g31/g35 有未提交进行中工作(g31 会改 `app/services/data_quality/`+task_queue;g29 改 chat/pi_rpc;g35 改 temporal_media)。本任务不依赖、不等待它们;共享薄接缝仅 `app/main.py`(router 注册)、`app/tools/__init__.py`(工具注册)、i18n messages 索引 — 均为局部加性接线。
- G09/G21: 未检索到 PR,worktree 存在(goal-g21、g09)。与本任务域不同,不占用。

## 关键既有能力(file:line 均按 ca7b3ec27)

| 能力 | 位置 | G32 增量 |
|---|---|---|
| G14 编辑操作词表(set_fields/rename/repair/move/snap/create/delete/replace/split/merge) | app/services/feature_edit/contracts.py:54-61 | 复用;proposal 候选=有界 op 集 |
| plan 暂存(TTL 600s,map_state ≤8) | feature_edit/executor.py:602-616 | proposal 持久层独立(per-session JSON 文件,跨重启) |
| executor revision+fingerprint CAS + journal + receipt | feature_edit/executor.py:200-422 | 提交桥复用 commit_plan;不弱化 CAS |
| `_wfid` 物化 + wfid_for_created(seed,op_idx,child_idx) | feature_edit/identity.py:28-72 | proposal 持久 op_seed → child 身份跨重载稳定 |
| journal FIFO(≤128×64KB,LRU≤4) | feature_edit/journal.py:62-83 | 长期审计不依赖 journal;merged 重放走持久 receipt |
| Map Review 提案状态机+JSON 文件存储+merge lease | app/services/review/store.py:1-301, policy.py:31-62 | 模式复用(同款原子写+租约);本域是要素数据 proposal,不是 MapSpec union |
| BlobStore CAS(put-if-absent, sha256 key) | app/services/durable_blob_store.py:128-221 | base 快照证据落 CAS;不复制整个 dataset 进 proposal 行 |
| editable-copy(只读源→可编辑副本) | feature_edit/datasets.py:339-389 | 只读源建 proposal 时走同路径,不改源 |
| G24 selection handles(ids≤200, 三重绑定) | app/services/selection_handles.py:55-59 | 提案目标冻结存快照;句柄过期→拒绝而非前50回填 |
| require_owned_session(actor 服务端裁决) | app/services/auth_history_bridge.py:57-74 | 全部新端点沿用;agent 不可自批 |
| 表现层三方 merge(MapSpec) | project_runtime/refresh/merge.py | 不复用不重包装;本域做 feature/field/geometry 粒度 |
| 前端 layer-edit-bridge 回执消费 | frontend/lib/edit/layer-edit-bridge.ts:123-184 | merge 成功复用 refreshDatasetLayer+lineage+undo 注册 |

## 已确认的 G14 空缺(需最小适配,任务 §二明确授权)

1. `build_plan` 的 plan_id 固定 `fep-{uuid}`(plan.py:452) — proposal 需要**持久 op_seed** 驱动 create/split/merge 出生身份,否则重载/重预览换 child wfid。适配: `build_plan(..., plan_seed: Optional[str] = None)`。
2. `set_fields`/geometry ops/delete 是 **plan 级 targets**(同一组值应用到全部 target,plan.py:220-232, attr_ops.py:211-231) — 一个 proposal 里"要素A改X,要素B改Y"无法表达。适配: op 可选 `wfids` 键做 per-op 目标限定(`effective = targets ∩ op.wfids`),向后兼容;一个 plan(≤8 ops)即可受控单 ref 提交多要素异构修改。
3. EditOpModel(schemas/feature_edit_schema.py:7-31) 加可选 `wfids` 字段。

## 存储与所有权决策

- **proposal 元数据/决策真相**: per-session JSON 文件 `<BASE_STORAGE_DIR>/<sid>/feature_proposals/proposals.json`(原子写+分布式锁+租约,同 review/store.py 模式)。跨重启、跨 worker、有 owner(session 归属由 require_owned_session 保护)。单一可写真相;不写 DB、不进 map_state。
- **base 快照证据**: 创建 proposal 时把当前 payload canonical JSON 写入 BlobStore(CAS, key=sha256),proposal 记录 {blob_key, sha256, content_revision, fingerprint}。diff/冲突比较从快照读,不从当前 session ref 读(当前 ref 随时被改)。快照不可变(CAS),withdraw 后标记 released;物理删除留给既有 GC(blob 内容寻址天然去重,多 proposal 共享同一 base 不重复存储)。
- **候选数据**: 不复制 dataset;候选= ops(带 op targets)+ resolutions,重算经 G14 derive_changes(seed=op_seed)。几何大载荷(候选要素级)按需分页/摘要。

## 状态机(词表按实现收敛,见 contracts.py)

draft → submitted →(changes_requested↔submitted)→ approved → merging → merged
draft/submitted/approved → conflicted(检查发现冲突时标记,可 rebase 回 submitted)
任意活跃态 → withdrawn(终态);merged/withdrawn 终态
recovery_required: merging 崩溃后无法证明结果时的诚实状态(阻断盲重试,提供只读证据视图)
