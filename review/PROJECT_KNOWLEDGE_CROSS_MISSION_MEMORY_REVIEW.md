# PROJECT_KNOWLEDGE_CROSS_MISSION_MEMORY_REVIEW — 独立对抗性 Review 记录

- 分支：`harness/project-knowledge-memory-v1`
- baseline：`origin/master = faa453a8935101378c23eb6694a42c3616d9c670`（2026-09-17 fetch）
- Review 执行者：subagent B（独立实例，未参与实现）；修复：主 agent
- Review 范围：`git diff faa453a8..HEAD`（模型/迁移/store/liveness/indexer/retrieval/card/invalidation/API/wiring/openapi/6 个测试文件）

## Verdict

**SHIP-WITH-FIXES** → 全部 P0/P1/P2 与低成本 P3 已在本地修复并回归（本 memo 记录 red-test→fix 链）。最终无未处理 P0/P1。

## Findings 与处置

### P1（1 项，已修复）

**P1-1 空上游 ref-token 产生 `exact`（severity 反转，违反「正向证明缺失永不 exact」红线）**
- 位置：`app/services/project_knowledge/retrieval.py` 上游指纹比对。
- 复现：`ArtifactLineage.source_dataset_fingerprint = NULL`（lineage_service 声明 Optional）→ 索引 ref-tag token 为空 → 请求给同一 ds 指纹 → 原实现跳过比对并给出「指纹一致」reason → verdict=exact。
- Red test：`tests/test_project_knowledge_verdicts.py::test_empty_upstream_token_never_exact`（先跑失败确认红）。
- 修复：空 token → `upstream_unverified:{ds}`（soft cause），绝不产生正向 reason。回归：verdicts 11/11 绿。

### P2（3 项，全部已修复）

**P2-2 card 静默丢弃失败警告却报告 truncated=False / omitted=0（观测不诚实）**
- Red test：`tests/test_project_knowledge_context_card.py::test_dropped_warnings_counted_as_omitted_review_p2_2` + `test_omitted_counts_variable_length_lines_review_p3_7`。
- 修复：装不下的 section/警告全部计入 `omitted`；变长行下不再早退 `break`（逐条如实计数）。card 10/10 绿。

**P2-3 `rebuild_project_knowledge` 首个源异常中断其余 6 源（rebuild 充分性破坏，API 仍 200）**
- Red test：`tests/test_project_knowledge_invalidation.py::test_rebuild_per_source_isolation`（monkeypatch `index_datasets` 抛错 → 其余源照常入索引，`report.failed_sources == ["datasets"]`）。
- 修复：per-source try/except 循环；`RebuildReport.failed_sources` 归因；`RebuildResponse` API 契约同步补 `failed_sources` 字段。invalidation 8/8 绿。

**P2-4 ref_lifecycle 观察者是死代码（`register_project_knowledge_hook()` 无任何调用点）**
- 修复：`app/api/routes/project_knowledge.py::_gate` 在 flag-on 首次使用时懒注册（幂等、无 import 副作用、失败不阻断主路径）；`_ref_lifecycle_hook` 本体加 flag 双重门（flag 关 = 零行为）。失效正确性仍以检索期 lazy 复核为主，观察者仅为增值推送（与 ref_lifecycle R6「正确性从不依赖观察者」同构）。
- Tests：`test_hook_lazy_registration_idempotent`、`test_hook_noop_when_flag_off`。API 7/7 绿。

### P3（已修复 5 项 / 记录 3 项）

已修复：
- **P3-5 model/migration 索引漂移**：migration 0093 补 `ix_project_knowledge_entries_org_id` / `ix_project_knowledge_entries_status`（downgrade 对称 drop；0090 同类先例）。
- **P3-6 `validate_bbox` 接受 NaN/±inf**：`math.isfinite` 全量校验；回归 `test_validate_bbox_rejects_nan_inf`。
- **P3-7 card 早退少计 omitted**：随 P2-2 一并修复（逐条计数）。
- **P3-8 `request_input_gone` 分类为 soft**：改为 hard（输入已消失 → not_reusable，「重算」无从谈起）；`request_input_stale` 保持 soft。
- **P3-9 测试恒真断言**：`test_empty_entries_render_empty_string` 的 `hasattr(card, "empty")` 三元恒真式改为三段实断言。

记录不修（评估后接受）：
- `retire_entry` 校验 org 不校验 project：与仓库项目 auth 粒度（owner-or-org-member）一致，且无 API 暴露；审计面调用方已过 org 门。
- 检索扫描上限 200 active vs 行预算 400：超出部分需 kind 过滤查询；已在 DECISIONS 记录（D5 备注）。典型项目投影 <200 行，预算是硬上界而非常态。
- card 自定义极小 char_budget（<header 长度）可产出超预算文本：默认 1600 不可达；预算参数面向测试，生产恒默认值。

## 误报排除（review 提出并复核为非问题）

- 并行红线：diff 纯增量（4425 insertions / 0 deletions），零触碰 `gis_memory/**`、`mission_runtime/**`、`evidence_claim/**`、`project_artifact_promotion.py`、`map_product_service.py`。
- Tenancy：store/retrieval/indexer 全查询路径带 org+project 恒等值过滤；`liveness.py` 的 `db.get(GISSpatialMemory)` 无 org 谓词但只返回被 org 过滤后的投影行已引用的指纹标量，无跨租户读路径。
- supersede SAVEPOINT 重试：READ COMMITTED 下重试 re-SELECT 可见胜者提交行，单次重试充分；partial unique `uq_pkx_active_key` 保证同 key 单 active。
- migration 0093（down=0091）与 #1355 的 0092 并行：本分支基线 head=0091，链干净；两分支都合入时按仓库惯例加 merge 节点。
- openapi 快照：`grep '^-'` 仅 diff 头，纯增量 +379/-0。
- 名字相似红线：`_evaluate_entry` 全程不读 subject；`/search` 文本过滤仅展示面（in-code 注释 + 测试锁定）。

## 与最新 master / open PR 的交叉

- open PR（review 时点）：#1335（claim/tenant/mission fail-closed——消费方，无文件交叉）、#1336（GeoAI——无交叉）、#1351–#1356（#1355 事件驱动 spatial ops 互补：其 invalidation bridge 面向 session 事件账本，本投影失效为 lazy-fingerprint 模型，可后续订阅其 ledger；其占用 migration 0092，本分支 0093）。
- 本分支不改动 #1335 修复面与 #1355 触及的 `project_artifact_promotion.py` / `map_product_service.py`。若 #1355 先合入，本 PR 需 rebase + alembic merge 节点（语义冲突预期为零）。

## 是否需要 integration PR

不需要独立 integration PR。两处集成点均为 additive（router 注册、openapi 快照），rebase 时按语义核对即可。

## 最终验证证据

- 本方向全套：`pytest tests/test_project_knowledge_*.py`（store/verdicts/invalidation/card/scenario/api）= **56 passed**
- Oracle 两遍连跑：store+verdicts+invalidation+card+scenario = 49 passed ×2（连跑，结果一致）；api = 7 passed ×2
- 邻居回归：`tests/unit/test_project_api.py + tests/test_gis_memory_store.py + tests/quality/test_api_compatibility.py` = 44 passed；`tests/unit/mission_runtime + test_project_domain.py` = 29 passed
- ruff（全部新增/触碰文件）：All checks passed
- `git diff --check origin/master...HEAD`：干净
