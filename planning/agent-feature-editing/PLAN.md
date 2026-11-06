# PLAN — 架构决策与里程碑执行

## 核心决策（D 系列，自动裁决记录）

### D1 写路径 = session ref 原位 overwrite + PatchSet journal（不建 new-ref 链）
- 编辑对象 = 会话数据 ref（`ref:` 可解析、geojson 信封、≤20000 要素）。fabric 外部源 / raster / inline-only MapSpec 层 → typed 拒绝（`dataset_not_editable`，不泄存在性差异）。
- 提交 = 会话锁内 read→CAS(expected content_revision)→patch→`session_data_manager.overwrite`（digest 变自动 bump revision）→journal append→锁释放→`ref_lifecycle.invalidate_ref_caches(OVERWRITE)`→mapspec revision 戳刷新→SSE 广播。
- **原始上传文件绝不触碰**（original.geojson 只读）；"可逆"由 journal before/after 逆 patch 承载（有界），非全量快照。
- 否决 new-ref-per-edit：与"地图渲染与业务数据版本同步更新"验收冲突（层引用漂移/churn），且修复管线的 new-ref 语义是数据集级修复，不是交互编辑。

### D2 稳定要素身份 = 首次编辑时确定性物化 `_wfid`
- `_wfid = "wf1-" + sha256(f"{dataset_fingerprint}:{index}")[:16]`，确定性（同 base 重物化同 id），写进 feature.properties（诚实可见字段），journal 记 materialize 事件（entry #0）。
- 物化即一次 overwrite（revision bump），只发生在用户/Agent 真正发起编辑时（opt-in），非摄入全局。
- 物化后一切寻址走 `_wfid`；物化前目标解析走前后端同源 `FEATURE_ID_KEYS` 链（`id/OBJECTID/fid/osm_id/@id/featureId/feature_id`，backend `layer.py:244-259` ≡ frontend `layer-data.ts:17`）。
- 规模门：>20000 要素 → `dataset_too_large_to_edit` fail-closed。

### D3 PatchSet journal = map_state 有界键（复用 session 生命周期）
- `map_state["_feature_edit_journals"]`：{ref_id → {entries[], head_seq, dropped}}；每会话 ≤4 个数据集 journal、每 journal ≤128 条 FIFO、≤64KB；溢出丢最旧+`dropped` 计数披露（interaction ledger 同款纪律）。
- entry：{seq, kind(apply/undo/redo/materialize), idempotency_key, actor, origin(user|agent), feature deltas[{_wfid, before, after}], op summary, dataset_fingerprint_before/after, content_revision_before/after, created_at}。
- undo = 用 head entry 的逆 delta 走同 CAS 写路径（新 entry kind=undo 引用被撤销 seq）；redo 仅当 head 是对应 undo。链完整性 = after_fingerprint 链校验。
- 活跃 plan 暂存 `map_state["_feature_edit_plans"]`（≤8，TTL 10min）——多 worker 共享，决策 REST 可能落在另一 worker。

### D4 信任边界 = 预览/提交分离 + 三种执行通道
- **Preview**（纯读，不锁）：解析目标（selection 模式读 IR 投影；explicit 模式显式 ids+ref）→ 计算有界 EditPlan（per-feature before/after 摘要、校验 findings、指纹、expected_revision、plan_id TTL 10min 绑 session+ref+revision）。
- **Commit 通道 A（用户卡）**：EditProposalCard（Chat 面板，ClarificationCard 同款状态机）→ `POST /feature-edits/plans/{plan_id}/decision`（require_owned_session，owner-token 鉴权）→ 服务端执行 → receipt。**破坏性/坐标变更类永远走此通道**。
- **Commit 通道 B（Agent 工具）**：`webgis_feature_edit_commit(plan_id)` 仅当 plan.safe && policy 允许；否则 typed `confirmation_required`（模型转告用户）。policy 由 owner 经 REST 设置（map_state `_feature_edit_policy`，默认 `confirm_all`；`allow_safe_attribute` 允许纯属性安全编辑；**坐标 move/snap 永不自动**）。
- **Commit 通道 C（用户 undo/redo）**：REST owner 鉴权 + 同 CAS 路径。
- safe 分类：仅 set_fields/rename_field、校验零 findings、≤50 要素、全部命中且指纹匹配。几何操作/删除/越界 → not safe（恒确认）。

### D5 编辑词表（最小、幂等、fail-closed）
- 属性：`set_fields`（类型校验按 dataset_semantics descriptor schema；枚举校验按 profile distinct 值 ≤24 视为 categorial，词表外拒绝 `enum_violation`）；`rename_field`（全数据集改名，journal 记录）。
- 几何：`repair_geometry`（per-feature 复用 data_quality 算子：make_valid/闭环/去重顶点）；`move_by`（显式 dx/dy + unit(m 必填) + 可选 bearing 语义：仅东西/南北位移或向量，缺单位/缺 CRS 假设 → `unit_required` 拒绝）；`snap_to_ref`（目标 ref + tolerance(m) + unit，容差内无候选 → `no_snap_candidate` fail-closed）。**无任何隐式"智能移位"**。
- 米→度换算：pyproj Geod/Transformer（绕要素质心的局部精确换算），数据 CRS 恒 4326（摄入已归一）。
- 每要素操作数 ≤8，plan 级 features ≤200（预览）/≤50（安全提交）。

### D6 MapSpec 联动 = 新窄 intent `RefreshDataRevisionIntent`
- 语义：对 mapspec 内 ref==目标 ref 的全部 source 仅更新 `content_revision` 戳（不动显隐/样式/user-wins 面）。
- 注册：intents.py + mutation_registry.py（effect_class=SEMANTIC、touches_layers=True、origin 仅 system/agent、锁目标=引用层）+ mutation_handlers.py handler + taxonomy.py 一行（system_repair）。四处行级最小 patch。
- 时序：数据锁释放后调用（engine 自取同把会话锁——**严禁嵌套持锁**，否则死锁）；失败重试 ≤2，最终失败 → receipt 披露 `mapspec_stamp_pending`（数据已正确，戳滞后）。
- SSE：前端已消费 mapspec 镜像（revision 门）→ 源 revision 变 → data-plane 缓存键失效自动重拉（map-state-restore/layer-data 既有 content_revision 管线），**无需新 SSE 事件类型**；另发 `dataset_updated` WS 广播（复用 fire_broadcast 先例）供非 mapspec 消费者。

### D7 Agent 观察面 = journal → situation 投影（EditActionReceipt）
- `gis_situation/compiler.py` 增投影：最近 ≤5 条 edit receipt（dataset_ref、feature 计数、字段/几何摘要、revision、actor）进 compile_interaction 相邻的 situation 块。**不改 Interaction IR 13-kind 词表**（数据面事件非交互事件，契约双闸不动——去重纪律）。
- 工具结果经 ToolDispatch → resultAbi envelope 自动有界；step_result SSE 既有管线。

### D8 前端 = 提案卡 + 预览覆盖层 + featureCommand undo
- `frontend/lib/feature-edit/`（新）：plan 类型、diff 模型（仿 workbench/delta.ts diff/invert 配对）、API client、pending 预览 store。
- EditProposalCard（新组件，chat 面板）：字段级 before/after 双栏（VersionWorkbench 模式）+ 几何摘要 + findings + approve/reject；approve → REST decision → receipt → toast + ops log。
- 预览覆盖层（新组件）：自管 GeoJSON source（`wb-edit-preview`，sketch 自管模式复刻）渲染"改后几何/高亮受影响要素"；确认前不写任何持久层。**不改 sketch-editor.tsx**。
- undo：`undo.ts` 新增 featureCommand 构造器（undo/redo → REST undo/redo CAS）；ops log actor 可见。
- 增量回显：mapspec revision 戳刷新 → SSE 镜像 → data-plane 重拉受影响 ref（单飞/ETag 既有）→ MapSpecRuntime 仅 setData 不 restyle（无闪烁、user-wins 保持）。MVT 层走 ref_lifecycle epoch 失效。

### D9 鉴权/租户
- 全部 REST 挂 `require_owned_session`（跨租户 404 不泄存在）；工具 session_id 服务端注入（Pi 验签 token，模型参数不可信）。
- ref 天然 session 命名空间隔离（session_data 按 session 分桶，跨会话 ref 不可解析 → `dataset_not_found`）。
- plan/journal/policy 读写在会话锁内，actor 从鉴权面取（user）或 turn context 取（agent），**绝不信请求体**。

### D10 生成物与中心文件纪律
- 新端点 → openapi snapshot 再生（API_SNAPSHOT_UPDATE=1）、endpoint-scope-matrix、api-docs、staleness 指纹。
- 中心文件 patch 清单（全部行级最小）：`app/tools/__init__.py`(+1)、`app/services/mapspec/intents.py`(+1 class)、`mutation_registry.py`(+1 register)、`mutation_handlers.py`(+1 handler)、`representation_binding/taxonomy.py`(+1 row)、`gis_situation/compiler.py`(+1 投影调用)、`app/api/main.py`? (路由注册既有模式)、前端 `use-sse-stream.ts`（dataset_updated 处理，若 mapspec 镜像路径不足）、`frontend/lib/types.ts`（payload 联合，如需）。
- 不触碰：registry.py 分发闸、tool_dispatch_service 核心、agent_pi_bridge、pi_* 模块、undo.ts 核心、selection-store、sketch-store、Interaction IR 契约。

## 里程碑映射（任务书 M1-M9 → 本执行）

- M1 ✅（BASELINE.md 数据所有权表 + 本文件）
- M2 = D1-D3 落码：`app/services/feature_edit/{__init__,journal.py,identity.py,plan.py,executor.py,geometry_ops.py,attr_ops.py,policy.py}` + 纯函数单测
- M3 = `app/api/routes/feature_edits.py`（preview/decision/undo/redo/history/policy）+ 鉴权/越权/冲突负例
- M4/M5 = 词表算子 + 校验 + 指纹不变性测试
- M6 = `app/tools/feature_edit_tools.py`（preview/commit/history）+ situation 投影 + dispatch 真链测试
- M7 = 前端（D8）
- M8 = durability 恢复/并发竞争/多标签/SSE 乱序测试
- M9 = E2E journey + vitest 全量 + 独立 review ×2 + PR

## 资源纪律

- pytest 不用 xdist（或 -n 2 上限）；前端 build/test 串行；无原生编译需求。
- 每里程碑原子 commit；PR 前 `git diff --check` + 敏感扫描 + 生成物一致性。
