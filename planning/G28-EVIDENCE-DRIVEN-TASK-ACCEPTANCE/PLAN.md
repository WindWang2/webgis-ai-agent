# G28 PLAN — 实施计划（全部加性，不动既有裁决）

设计原则：单一 evaluator（A02）不动摇；证据面加性扩展；finalizer 最小接线；
前端消费同一持久化裁决；修复走既有 runtime_repair/loop_budget 词表。

## W1 证据面加性扩展（P1/P2/P3 核心，纯函数层）

新文件 `app/services/gis_harness/goal_satisfaction/receipts.py`:
- `ReceiptsBundle`（有界 dict 投影）: `edit[] / compute[] / import_[] / delivery[] / raster[] / refresh[] / frames`（每族 ≤4 条，字段白名单）。
- `collect_capability_receipts(session_id, chapter)`（async，唯一 IO 点，finalizer 锁外预取；env kill switch `GIS_GOAL_RECEIPT_ADAPTERS` 默认开）。
- `receipt_evidence_rows(bundle, chapter)` → List[GoalEvidence]（EvidenceKind.CAPABILITY_RECEIPT / EXPORT_RECEIPT(delivery 复用) / DATA_REFRESH）。
- 陈旧/身份规则: 回执 revision 字段 ≠ checked_revision → STALE；同名工件 checksum 不一致 → FAILED(A18)；owner/scope 不匹配 → 丢弃该证据（A17）。

新文件 `app/services/gis_harness/goal_satisfaction/semantics.py`（P2 闸，纯函数）:
- `semantic_caps(requirement, evidence, bundle) -> List[cap_code]`：
  `population_partial`（skipped/truncated/sample>0 → 不冒充全集 A07）、
  `unit_undeclared`、`density_without_denominator`、`rate_without_timebase`（A08/A09）、
  `crs_unverified`（crs_source=unknown/assumed）、`artifact_checksum_mismatch`、
  `observation_snapshot_not_live`（A14）、`scope_subset`（A17/A18 配套）。
- 未知即 unknown：无证据码 → 不造码。

`evidence.py` 加 `_capability_receipt_evidence/_delivery_receipt_evidence`；`build_evidence_registry` 加可选 `receipts` 参数（默认 None = 旧调用零漂移）。

## W2 evaluator 加性规则 + frame 闸

`evaluator.py`:
- 新失败规则 id（机器可读）：`R_RECEIPT_PARTIAL`/`R_RECEIPT_UNIT_UNDECLARED`/`R_RECEIPT_DENSITY_DENOM`/`R_RECEIPT_RATE_TIMEBASE`/`R_RECEIPT_CRS_UNVERIFIED`/`R_RECEIPT_STALE`/`R_RECEIPT_ARTIFACT_MISMATCH`/`R_RECEIPT_FAILED`/`R_FRAME_INCOMPLETE`/`R_FRAME_SNAPSHOT_ONLY`。
- `_eval_map_requirement` 加 frame 闸：块上 frame_product.summary.required>0 且 verified<required 或存在 unresolvable/not_observed → PARTIAL（A13）；全部仅 snapshot → PARTIAL + R_FRAME_SNAPSHOT_ONLY（A14）；旧 revision 观察 → STALE（A05 复用既有 STALE 语义）。
- `_eval_row_requirement` 加 receipt 语义闸：capability 绑定的回执带 cap 码 → PARTIAL + 对应 rule（有证据在证后才降级——闸只降级不升级）。
- `_eval_export_requirement` 加 delivery 面：delivery 回执（scope.missing_ids 非空 → PARTIAL; status≠completed → FAILED; content_sha256 缺失 → 诚实 unknown 不 pass？——改为 missing code `artifact_unverified`）。
- 信号面：`R_RECEIPT_*` 家族 → CONTINUE（可重试动作），不新开信号词。

`requirements.py` 加性派生：export_intents 中数据交付格式（shp/geojson/gdb/csv/xlsx）→ EXPORT 需求 `delivery_expected` 标记（bounded dict 新字段，默认 False 向后兼容）。

## W3 finalizer 最小接线（共享文件小型补丁）

`completion/pipeline.py`:
1. 锁外预取（与 awf_runs 同点）: `collect_capability_receipts(session_id, awf_eval_chapter基)` + frame 验证预取 `frame_product_verification(session_id, mapspec, observation)`（新薄函数在 goal_satisfaction/facets.py，调 frame_products.verify_frame_product 只读）。
2. `map_product_block(...)` 加两个可选参数 `receipts=None, frame_verification=None` → 持久化 `block["capability_receipts"]`（bounded）与 `block["frame_product"]`（G15 缺口闭合）。
3. `evaluate_goal_satisfaction(..., receipts=receipts)` 传参。
4. task_complete SSE 面已透传 goal_satisfaction —— 追加透传 `capability_receipts` 摘要（有界 ≤8 行）供 UI。

## W4 前端完成状态卡（P4）

- `frontend/lib/chat/` 解析 task_complete.goal_satisfaction（additive，缺席零漂移）。
- 新组件 `frontend/components/chat/goal-satisfaction-card.tsx`: verdict 徽标 + 逐需求行（id/kind/verdict/missing/next_action）+ summary_line；明确区分「计算完成、地图等待显示」与「任务失败」。
- message-reducer / use-sse-stream 接线（现有 task_complete 消费点旁加性键）。

## W5 有限修复（P5）

新文件 `goal_satisfaction/repair_actions.py`:
- `repair_plan_for(report) -> BoundedRepairPlan`：失败规则 → 注册动作（`reobserve` / `reexport` / `stale_reread` / `retry_step` 只读 vs 写入分类）。
- 写入动作必须带既有 mutation/CAS 路径名；user lock/权限/数据缺失/unsupported → blocker 停止码（不放宽目标）。
- 接线：finalizer continuation 面已有 replan/repair 裁决 —— repair_plan 作为 additive 披露块 `block["goal_repair_plan"]`，供 Pi harness 下一轮消费（不绕过 loop_budget，admissible 检查由消费方执行）。

## W6 测试与语料（P6）

- `tests/unit/gis_harness/test_goal_satisfaction_receipts.py`（适配器 + 假成功注入负例 ≥5：假 status 字符串、同名不同 checksum、旧 revision 回执、partial 冒充全集、snapshot 冒充 live）。
- `tests/unit/gis_harness/test_goal_satisfaction_semantics.py`（A07–A11、合法无地图任务不阻断 ≥2）。
- `tests/unit/gis_harness/test_goal_satisfaction_finalizer_wiring.py`（pipeline 接线：frame_product 持久化、capability_receipts 持久化、SSE 透传、kill switch 关闭零漂移）。
- 语料新族（`app/evaluation/goal_satisfaction_corpus.py` 加性 case builder）：receipt 族反事实 + 合法无地图任务；oracle 独立手写（不从被测函数抄）。

## W7 Pi 闭环（P7）

- 复用 `app/lib/harness/lab` 既有脚本化 provider 机制跑 Pi 子进程 + 真实服务 fixture 的验收旅程（1 条：统计→热力/统计表达→验收→解释）。
- 浏览器闭环：现有 Playwright e2e 基建（journeys 已登记）；workers=1，宿主重任务锁。受阻则诚实 blocked 记录。

## 顺序与提交切分

1. W1+W2（纯函数 + 单测）→ commit
2. W3（pipeline 补丁 + wiring 测试）→ commit
3. W4（前端）→ commit
4. W5（修复映射 + 测试）→ commit
5. W6 语料扩展 → commit
6. W7 闭环证据 → commit
7. 独立 review → fix → 复审 → PR

## 状态

- W1: in_progress
- W2–W7: pending
