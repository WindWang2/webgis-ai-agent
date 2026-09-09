# Harness V5 Architecture & Plan

## 目标
把 V4 harness 升级为 Autonomous Spatial Reasoning & Execution Runtime V5：
Understand→Plan→Execute→Observe→Diagnose→Repair/Replan→Re-observe→Verify→Finalize→Resume
全链路可持久化、可审计、可重放、可恢复。

## 决策（全部 additive，无第二事实源）

### D1 Durable Trace V5（W1）
- `trace_store.py` 重写持久层：**每 session 一个 JSONL + 跨进程 flock**（`fcntl.flock` on `<file>.lock`，非 POSIX 降级为进程锁 + 文档化）；单行 `write()` under lock + O_APPEND；每记录单调 `seq`（lock 下读 last seq +1）；`persist_turn_chain` 幂等（`(turn_id, revision)` 去重窗口）；trim 仍在锁内 read→rewrite。
- 修复 LRU 驱逐丢链：turn settle 时 chain 直接从 settle 上下文持久化（不经 registry 存活），`GisTraceRegistry` 仅作查询缓存。
- 压缩/关键事件：FINAL_VERDICT/TOOL_FAILURE 类记录永不 trim 丢弃（trim 只裁剪普通 stage 记录，保留 verdict/tail N）。
- 不引入 DB 表（session-plane 与 project-plane 分离维持 V4 边界；W8 的 resume anchor 走 DB WorkflowRun）。

### D2 统一失败分类 + Remediation（W2）
- 新 `app/services/gis_harness/failure_taxonomy.py`：`HarnessFailureClass`（tool_error/data_error/crs_error/empty_result/stale_ref/renderer_failure/timeout/partial_completion/cancelled/budget_exhausted/unknown）+ `classify_harness_failure(...)` **适配**（不替换）planning/geocompute 两套枚举 + dispatch TOOL_ERROR 细分线索（correction_hint、cancellation、timeout marker、stale ref detail）。
- `RemediationAction` Literal（retry/ backoff/fallback_tool/replan/substitute/requery_profile/abort_disclose）+ `RemediationPolicy`（per-class bounded：max_attempts ≤3、预算字段）。
- 接入点：`tool_dispatch_service` 错误 seam 生成 typed failure 记录 → TurnEvidence + chain TOOL_FAILURE/事件；FINAL_VERDICT.failure_code 用统一词汇（trace contract 已要求）。
- 防循环：replan 预算计数在 workflow_instance repair memory / evidence 中累计，超预算 → abort_with_disclosure（已有 MAX_FINALIZATION_PASSES/MAX_RUNTIME_REPAIR_PASSES 保持）。

### D3 CRS/经度约定硬化（W3）
- 新 `app/lib/gis/longitude.py`：convention 检测（bbox/坐标采样）、0-360↔±180 归一、antimeridian 穿越检测与 GeoJSON polygon/line 拆分（纯函数、确定性）。
- `DatasetProfileV3` 增加 `longitude_convention` deep fact；planner fact bundle 透传。
- 修 KNOWN-GAP #1：`geo_processor.to_utm_gdf_with_note` 捕获 `pyproj.CRSError` → typed `InvalidCRSError`（含 correction_hint）→ std error；xfail 转 pass（strict=False 自动通过，更新 KNOWN-GAP 文档）。

### D4 Progressive DatasetProfile（W4）
- `app/services/data_profile/profiler.py` 增 `depth: cheap|deep` 两段式 + session 级缓存（map_state key `_profile_cache`，keyed by source_fingerprint）；fingerprint 失配 → cheap 重算、deep 失效（stale invalidation 已有 source_fingerprint 契约，扩展到缓存层）。
- planner 在 precondition fact unknown 时请求 deepen（requery_profile remediation 闭环，接 D2）。

### D5 Rendered-state Observation + Finalization V5（W5）
- observation payload 契约 additive 扩展（`chat.py` CartographicObservationPayload model）：`layers[].source_status/feature_count/style_applied`、`render_complete`、`charts[].rendered/data_points`（全部 optional，旧客户端兼容）。
- `validate_render_observation`：**requested intent ↔ actual rendered** 逐层核对（missing/extra/invisible/source-error/style-missing）→ findings；`chart_required` facet → chart rendered 且 data_points>0 校验；telemetry 缺失 → disclosed warning（不假通过）。
- finalization verdict：intent/rendered 不一致 → NEEDS_REPAIR（现有 verdict 词汇复用）。

### D6 Subagent Accounting（W6）
- `SubagentBudget` 增加 `llm_usage`（prompt/completion/total tokens、cost 当 provider 提供）；从 sub-engine TurnEvidence/evidence registry join；不可得时诚实缺席（usage=None + disclosed）。
- 结果与 EVENT_SUBAGENT_COMPLETED 携带 lineage（parent_turn_id/depth/role）+ usage roll-up 至 parent TurnEvidence。

### D7 Retrieval V5 评测（W7）
- 新 `app/evaluation/retrieval_eval_corpus.py`：~240 curated open-loop cases（zh/en；直接/近重复对/hard-negative/歧义四类），期望集人工金标（不与 lexical 索引同源，避免答案写死对齐）。
- 指标：precision@1、recall@5/10、invalid_selection_rate、fallback_rate；阈值按实测保守 pin；deterministic。

### D8 Project-level Resume（W8）
- WorkflowRun 增 `resume_anchor` JSON（alembic `0032_harness_v5_resume_anchor`，本任务独立 revision）：session 摘要 + gis_chapter 关键块（session_plan 摘要/workflow_instance/map_product verdict）+ chain 文件游标 + source_fingerprints。
- `POST /sessions/{sid}/workflow-resume-anchor`（写，require_owned_session）+ `POST /sessions/resume-from-anchor`（读，require project membership + user 一致）：新 session 种子化 gis_chapter + plan 摘要，标记 `resumed_from`。
- 重启测试：内存 store 重建后 resume 成功。

### D9 Live-failure Corpus + Recovery Scenario（W9）
- `runtime_corpus.py` 增 fault-injection 类目（provider_timeout/db_transient/empty_partial/map_source_error/restart/stale_workspace/cancellation/retry_exhaustion）→ deterministic 预期（typed class + remediation + 终态）。
- 至少 1 条端到端恢复 scenario：中途故障 → typed diagnose → bounded repair/replan → 恢复 → finalization intent↔rendered 验证通过（进程内 fake harness 驱动，无 LLM）。

### D10 Compat + Docs（W10）
- V4 trace/session 行为兼容测试（旧 JSONL 无 seq → 迁移读取容忍）；ADR-0118；CHANGELOG；PR 总结。

## Test oracle
- 每个 D：单元（新模块纯函数）+ 接入点行为测试（真实 seam，不 mock 事实源）+ 兼容/负路径。
- 多 worker trace：多进程并发 append → 零丢行 + seq 单调无重复。
- 端到端：W9 恢复 scenario；restart resume。

## 与并行 Epic 边界
- 不改科学算法本体（Science）、不改渲染器实现（只消费前端 telemetry 契约）、不改集群调度、不改 workflow compiler；FailureClass 为适配器非重构。
- 高冲突共享文件最小化：CHANGELOG 追加一段；无 workflow/CI 修改；migration 单独 revision 0032。
