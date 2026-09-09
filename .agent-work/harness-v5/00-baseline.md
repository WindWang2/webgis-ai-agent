# Harness V5 Baseline (read-only audit @ origin/master 445ad30)

Branch: `feat/harness-v5-autonomous-runtime` · Worktree: `../harness-v5` · Baseline commit: `445ad30`

## V4 已落地（PR #1156, ADR-0104）
- WorkflowInstance runtime projection（`app/services/gis_harness/workflow_instance.py`，gis_chapter["workflow_instance"]，StateRevision/StateFingerprint，rows_fingerprint V2）。
- Tool surface V3 + V4 deterministic rerank（`tool_surface_v3.py`），retrieval corpus 2000+（capability→surface projection，r@5≈0.44 pinned）。
- Context policy 六操作执行器（`context_policy.py`，RELOAD_REF→RefSpillStore 24h TTL）。
- SubagentDispatcher：12 roles、depth≤2、parallel≤6/semaphore 2、预算=tool_calls/heavy/wall_time（**无 token/cost**）。
- Map observation loop：结构投影 gate（mounted/visible/source_converged）+ finalizer ≤2 修复 + verdict；像素启发式为 agent-tool-only。
- 18-stage chain：5 个真实 emitter ≈28% 完整度（V4 后已加 completion pipeline emitters）；durable sink 仅 session JSONL。
- Corpora：conformance ≥20,088 plan-tier + runtime ≥3,000 + e2e ≥100（deterministic，无 runtime-failure-injection 层）。

## V5 缺口（file:line 证据）
1. **Durable trace 多 worker 不安全**：`app/services/gis_harness/trace_store.py:27` 进程内 `threading.Lock`；docstring L56-59 明示"记录面允许极端并发下的行交错损失"；`GisTraceRegistry` LRU 128 驱逐后 `persist_turn_chain` 返回 False；`app/lib/runtime/evidence.py:20-21` 单进程假设。无 seq/幂等。
2. **Rendered-state 证据缺失**：`render_observation.py:139` gate 仅 mounted/visible/converged；无 per-layer source_status/feature_count/style_applied/render_complete；`chart_required` 无任何完成校验（audit §3）。
3. **失败分类双头**：`planning/models.py:66` 与 `geocompute/errors.py:20` 两套 disjoint FailureClass；dispatch 层 `registry.py` 只有 VALIDATION_ERROR/NOT_FOUND/UNKNOWN_TOOL/TOOL_ERROR。
4. **Project-level resume 不存在**：SessionPlan/workflow_instance/map_product/observation 全部 session-TTL 绑定（`session_data.py`）；RefSpill TTL 24h；DB 侧 WorkflowRun/Artifact（`models/project.py:150,203`）与 chat turn 断连。
5. **Subagent 无 token/cost roll-up**：`subagent_roles.py:358-395` 仅 calls/heavy/wall；lineage 仅 trace 事件。
6. **Retrieval 评测非 query→tool**：`retrieval_corpus.py` 由 golden+paraphrase 生成、capability→surface 解析期望；无 curated hard-negative/歧义集；无 precision@1/invalid-selection 指标。
7. **DatasetProfile 一次性**：`app/lib/data/profile.py` quality=complete/sampled/partial/failed 静态；无 progressive deepen；经度约定（0-360）字段缺失。
8. **CRS 0-360/AM 仅 MVT**：`mvt.py:259-470`；分析/数据面无通用处理；KNOWN-GAP #1（`tests/quality/test_scientific_regression.py:124` xfail：坏 CRS 逃逸为 pyproj 裸错→generic TOOL_ERROR）。
9. **Runtime corpus 无真实故障注入**：provider timeout/DB transient/重启/stale workspace/cancel/retry-exhaustion 类目缺失。

## 资源/约束
- 部署为单 uvicorn worker（`Dockerfile:89`），但 V5 不得假设单进程；Redis lock 基础设施已存在（`distributed_lock.py`）。
- Alembic head = `0031_revision_indexes`（32 files）。新 migration 必须用本任务独立 revision。
- Ruff: E4/E7/E9/F；pytest asyncio_mode=auto, timeout=60(thread)；快速 lane = contract tier / `-m cartography` / `-m perf`。
- 最多 2 个 subagents（ZCode 执行层）；产品侧 subagent 并行上限维持 V4 语义（≤6 children、depth≤2），本 Epic 不改并行上限，仅补 accounting。
