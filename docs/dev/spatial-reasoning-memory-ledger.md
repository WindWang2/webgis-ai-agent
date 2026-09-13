# Ledger — GIS Spatial Reasoning Memory v1（方向 9）

基线：`origin/master @ 580b33e9`。每个 milestone 追加一节；未解决项留在最末。

## M0 — Phase 0 勘察与契约落点（本次提交）

- 目标：完成全库记忆/事实栈对账，锁定不重复施工边界与生产接线缝。
- 改动文件：`docs/dev/spatial-reasoning-memory-{recon,decisions,ledger}.md`。
- 新/改契约：无代码契约；决策 D1–D11 预注册。
- 测试：无（纯文档）。
- 证据：recon §2 重叠矩阵覆盖 7 个 open PR + 全部 merged 近线；§5 kind→存储对账 10/10。
- 兼容：不触碰任何既有文件行为。
- 回滚面：删三份文档即回滚。
- 未解决项：evaluation corpus 场景数（32 为初值，M5 可调）。

## M1 — 契约 + 写策略 + 存储（R1+R2，commit 0b7050f5）

- 改动：`app/services/gis_memory/{__init__,contract,sanitizer,policy,store}.py`、
  `app/models/spatial_memory.py` + `__init__` 注册、
  `migrations/versions/0080_gis_spatial_memories.py`（初领 0072，M5 复核协议后改段 0080，见 D10 修订）、
  `migrations/.alloc.json`（领号声明）、`docs/integration/ownership.json`
  （adr_watermark 183 / migration_watermark 72）、`tests/test_gis_memory_store.py`。
- 契约：10 kind closed vocab、8 证据源 × kind 允许矩阵、语义指纹
  （volatile 字段剥离）、`MemoryWriteRequest.validate`、失效规则自动推导。
- 测试：27 项；迁移 upgrade→downgrade→re-upgrade 于 scratch sqlite 验证（20 列）。
- 未解决项：无。

## M2+M3 — 检索/投影/收割/生产接线（R4–R6，commit 4d3cd47c）

- 改动：`retrieval.py`、`projection.py`、`pending.py`、`harvest.py`、
  `queries.py`、`app/api/routes/chat.py`（Pi 双路由 `[GIS_MEMORY]` 注入 +
  turn 端收割 + `_resolve_memory_org`）、`app/services/gis_harness/tools.py`
  （map_intent 兜底 + 候选入缓冲）、`app/services/tool_dispatch_service.py`
  （provider_failure 候选）、检索测试 8 + 接线测试 10。
- 契约：`MemoryQueryContext`/`RetrievedMemory`（理由面）、
  `[GIS_MEMORY]` 块（≤1100B、sensitive 双防线）、
  `MemoryProjectionInput` narrow interface、
  `_gis_memory_org`/`_gis_memory_user` 租户桥键。
- 测试：45 项全绿。
- 发现并修正既有 latent bug：`memory_harvest.py` `from plan_orchestrator
  import get_plan` 实为不存在的方法名（靠 try/except 掩盖）——本包用真实
  单例访问路径 `plan_orchestrator.get_plan`；未改他人文件（该修正属
  #1273/#1275 触碰面，避免冲突，记录在此供后续修复）。
- 未解决项：boundary_ref 目前为确定性身份 ref（`local:admin:{level}:{name}`），
  几何级 ref 待 `get_local_admin_boundary` 工具缝接入（见 ADR 接口点）。

## M4 — 安全/租户加固（R8，commit de227ab4）

- 改动：`tests/test_gis_memory_security.py`（8 项）、
  `projection.py` sensitive 渲染层纵深拦截（本里程碑唯一产线改动）。
- 测试：跨租户三层隔离、session 等值边界、访问撤销、sensitive 双防线、
  credential 双防线、路径遮蔽、user scope 双谓词、DB 故障 fail-open。

## M5 — 评估语料 + ADR + 契约导出（R9+R7 文档面，本次提交）

- 改动：`eval.py`（32 场景回放框架）、`tests/test_gis_memory_eval_corpus.py`（7 项）、
  `docs/adr/0183-gis-spatial-reasoning-memory.md`、
  `docs/dev/spatial-memory-contracts/gis-spatial-memory.schema.json` +
  漂移测试（4 项）、`docs/gis-harness.md` 新节、store.py 偏好路由
  supersede 修正（显式用户来源，ADR-0069 自身语义）。
- 指标：useful=36 / wrong=0 / stale=0 / precision=1.0（36/36）/
  context_bytes_saved=1670 / tool_calls_saved=36 / score=108。
- 全套件：59 项（27+8+10+8+4+7−重合计，实际 pytest 计 64 用例含模块级）
  全绿；ruff 干净。
- 未解决项：Situation（#1275）合并后的 projection 挂接；`sweep_expired`
  的后台周期化（当前 harvest 位点同步执行）。

## 全局未解决项

1. 与 open PR 的 chat.py 冲突面：#1275 改 env_block 流、#1277 改 bridge——
   rebase 时以「单注入通道追加块」语义最小适配。
2. boundary_ref 几何 ref 生产者（接口点已留）。
3. user-scope 偏好的 UI 撤销入口（`retire_memory` API 已备）。
