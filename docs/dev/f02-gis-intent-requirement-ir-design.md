# F02 — GIS Intent & Requirement IR — Design

- Date: 2026-09-26（实现） / 2026-09-27（本文为合并后补记的 design，G06 文档债补齐）
- ADR: `docs/adr/0215-gis-intent-requirement-ir.md`
- Recon: `docs/dev/f02-gis-intent-requirement-ir-recon.md`；Decisions: `docs/dev/f02-gis-intent-requirement-ir-decisions.md`；Review: `docs/dev/f02-gis-intent-requirement-ir-review.md`
- 实现基线: `origin/master @ 9e1ad229`；Merge: PR #1507（`0141f527`）
- 状态: 已合并（merged）。本文全部内容以合并后代码为事实源，可 rg 核对。

## 目标与边界

把「一次地图请求」从无状态的 `MapRequestIntent` 升级为**版本化、可增量修订、可回放
的需求文档**（`RequirementDocument`），并把澄清、digest、多轮 patch、user-wins
收敛在单一包内。红线：**不造第二理解器** —— 语义核心仍是
`app/services/gis_harness/intent.py::MapRequestIntent`（`resolve_map_request_intent`
+ `merge_intent_hints` 产出），IR 不写 MapSpec、不选工具、不算 grammar/completeness。

## Module map（新包 `app/services/gis_harness/requirement_ir/`，全部为新建）

| 模块 | 内容 |
|---|---|
| `contracts.py` | 版本化 typed 契约：`GISIntentSpec`（内嵌 `core: MapRequestIntent`）、`MapRequirementSpec`、`RequirementDocument`、`Provenance`、`Ambiguity`、`PatchOp`、`PatchRecord`、`REQUIREMENT_DOCUMENT_SCHEMA`；全部 `extra="forbid"`、有界（measures≤8 / ambiguities≤16 / locks≤32 / journal 保存 200 条 + folded 计数） |
| `classify.py` | 五类顶层意图归类（`query_only / analysis / map / edit / export`）：词面 cue 表 + 语气护栏 + `_FALLBACK_ANALYSIS` 显式排除 resolver 默认族；优先序 export → map（no-map 护栏压制）→ analysis → query_only 兜底；不产出 TaskType/Analysis 词汇、不替代权威 |
| `build.py` | core→sections 确定性派生 `derive_sections` / `build_document`；多轮 edit 话语 → patch 差分 `diff_to_patches`；超替携带 `carry_user_state`（user locks **连值带 provenance**）；`rederive_requirements` / `rebuild_with_patches` |
| `patch.py` | 白名单 patch 协议：typed 错误族（`PatchUnknownPath` / `PatchValueInvalid` / `PatchConflict` / `PatchStale` / `PatchBlocked`）；`expected_revision` CAS + `op_id` 幂等；`_sync_core` 显式 sync-map 同步 section 与 core；`fold_patches` 纯重放（回放不变式）与 `diff_documents`（带 actor 归因）；user-origin 字段被非 user actor 触碰 → `PatchConflict` 拒绝 |
| `clarify.py` | typed 澄清策略：`ClarificationNeed` + blocking/safe-default(+rationale) 策略表（只有影响正确性/不可逆才提问）；`context_key` 去重（code+相关字段规范值，context 未变不再问）；`plan_clarifications` / `pending_questions` / `record_ambiguities`；兼容出口 `to_legacy_request` → 旧 `ClarificationRequest` 形状 |
| `digest.py` | `requirement_digest`（对规范化语义核 canonical-json+sha256，同义请求同 digest；排除 raw phrase/provenance/revision/journal）与 `document_digest`（含 revision/parent/lifecycle 的 envelope 指纹）双轨 |
| `normalize.py` | 表驱动有界规范化：`normalize_admin_name` / `normalize_statistic` / `normalize_group_by` / `normalize_normalization` / `normalize_time` / `normalize_palette` / `normalize_format` 等，只服务 digest，不做开放式 NLP |
| `lifecycle.py` | 条目/文档级 lifecycle 状态机：`transition_document` / `transition_item` / `can_accept` / `blocking_ambiguities` / `supersede` / `resolve_document_lifecycle`；状态词表封闭 |
| `obligations.py` | 需求义务派生：`derive_requirements(spec) -> MapRequirementSpec`（复用 `goal_satisfaction.contracts.RequirementKind` 词表）+ `rederive`（patch 后最小重推导） |
| `projection.py` | 六路单向投影，只写权威模块的**输入面**：`intent_view`（MapRequestIntent 消费方）、`field_query_inputs`（field_resolver）、`grammar_request_face`（GrammarRequest 构造键）、`template_obligations`、`export_obligations`、`goal_requirements`（产出合法 `GoalRequirement`，构造期校验） |
| `corpus.py` | 8 个真实场景验收 corpus（多轮增量修订/超替/澄清/digest 稳定） |
| `service.py` | 会话级生命周期，**包内唯一 IO 面**（可注入 async store 协议，单测注入内存实现）：`ensure_document(session, query, hints, turn)` = 载入 → classify（edit vs new task）→ edit 走 patch / new task supersede；journal 折叠上移至此（`_fold_if_needed`：genesis 前滚为 `replay(genesis, folded_part)`） |

既有文件仅两处加法改动：`app/services/gis_harness/tools.py`（`webgis_map_intent`
result 追加 omittable `requirement` 键，:704-725，fail-open 静默省略，仿
`skill_guidance` 先例，不改 contract_version）。

## 规范语义（normative）

1. **单一语义核心**：`GISIntentSpec.core` 内嵌 `MapRequestIntent`；patch 经
   `_sync_core` 同时维护 section 与 core 对应字段；`projection.intent_view(doc)`
   原样外泄 core，既有下游（recipes/planner/template_selector）零改动消费。
2. **user-wins 是 patch 层硬约束**：`PatchRecord.actor ∈ {user, agent, system}`；
   `origin=="user"` 的字段被非 user actor 触碰 → typed `PatchConflict`；用户改锁
   只能经 user-actor `remove_lock`。supersede 时 `carry_user_state` 连值带
   provenance 携带；`_restore_user_fields` 仅在「新派生无表达且旧值非默认」时恢复
   （新话语显式表达的面以最新用户表达优先，agent 永远可修正字段）。
3. **patch 可回放**：`PatchOp` 白名单 + path 白名单绑定 schema + 值经 pydantic
   重建校验；`expected_revision` CAS（stale → `PatchStale`）；重复 `op_id` →
   幂等 no-op；journal 有界 200 条，折叠由 service 层负责（`apply_patch` 保持纯
   函数），折叠后 `fold_patches(base, journal)` == 现网文档。
4. **澄清只问 blocking**：非 blocking 记安全默认 + rationale；`context_key` 未变
   不重复问；每次最多 2 问（对齐 `ClarificationRequest` 上限）；`answer_ambiguity`
   答案先经 path 白名单校验（fail-closed 无部分状态）再回写字段 + provenance +
   core 同步。
5. **digest 双轨**：`requirement_digest` 同义稳定（可 replay/drift 比较）；
   `document_digest` 变更检测（envelope 指纹）。`document_id` 为无密钥 sha256
   截断（48-bit，session 内自见；review 记录为后续可换 HMAC）。

## 测试计划（`tests/unit/gis_harness/requirement_ir/`，合并时 169 tests）

| 文件 | 覆盖 |
|---|---|
| `test_classify.py` | 五类正例+负例矩阵、语气护栏、fallback 排除族 |
| `test_patch.py` | CAS/幂等/白名单/回放不变式/归因 diff |
| `test_review_fixes.py` | review P1 回归：`TestJournalFoldingReplay`（205 patch 驱动折叠后回放不变式）、`TestSupersedeCarriesUserValues`（新表达胜出 + agent 可修正）、`TestClarificationWriteBack`（回写 + 非法答案零变化）、`TestLocksRuntimeCap` 等 7 条测试函数 |
| `test_clarify.py` | blocking 策略表、context_key 去重、legacy 兼容出口 |
| `test_digest.py` / `test_normalize.py` | 同义稳定、表驱动规范化 |
| `test_build.py` / `test_contracts.py` / `test_projection.py` / `test_lifecycle.py` / `test_corpus.py` / `test_service.py` | 派生/契约/六路投影（真实下游类型构造期校验）/状态机/corpus/会话服务 |

## Review 结论与清偿（详见 review 文档）

APPROVE-WITH-FIXES：无 P0；3×P1（journal 折叠回放断裂 / 超替幽灵 user-wins /
澄清答后不回写）+ 10×P2 全部修复并附回归测试。记录为后续项：representation 面
（hidden_layer_ids/palette）下游接线、HMAC document_id、归因匹配放宽、裸
「导出/下载」cue 的整单换图细分。
