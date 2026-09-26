# F02 — GIS Intent & Requirement IR · Recon（只读勘察记录）

- 基线：`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-24 05:00 +08:00，merge PR #1494）。
- 勘察时间：2026-09-26；勘察方式：`git fetch origin --prune` + Subagent A 只读深审（75 次工具调用）+ 主 agent 精读关键契约。
- 本地主仓 `master`（d5315716）与 `origin/master` 分叉：本地 ahead 16 / behind 187。**本任务一律以 `origin/master` 为唯一基线**，worktree 已从 9e1ad229 派生。

## 1. GitHub 状态

- Open PRs：#1489（dependabot）、#1497 F12（Map Plan Compiler）、#1498 F13（MapSpec→Render Runtime）、#1499 F08（Resource Scheduler）、#1500 F15（Visual Observation Repair）、#1501 F11（Template/Component Composition）、#1502 F10（Cartographic Grammar 生产接线）、#1503 F09（trace/replay oracle v3）、#1504 F14（Publication/Export Parity）。
- 与 F02 同名/同域的 open PR：**无**（F02 未被占用）。
- 最近 merged（功能波次）：#1479–#1488（2026-09-21 基础波次，ADR-0204×10 收敛）、此前为 i18n/audit 修复系列。
- Open PR 对本方向相关文件的 changed-file overlap 检查：仅 #1500 与 #1504 触碰 `app/services/gis_harness/completion/pipeline.py`；**无 open PR 触碰** `gis_harness/tools.py`、`intent*.py`、`clarification.py`、`goal_satisfaction/*`。F02 落点避开 `completion/pipeline.py`、`field_resolver`、`grammar_solver`、`harness/replay/*`、`map_plan_compiler`、`composition_selection`、`lifecycle_engine/store`、`session_plan.py`。

## 2. 基础波次 #1479–#1488 复核（F02 视角的未完成面是否仍成立）

| 波次 | 交付 | 对 F02 的含义 |
|---|---|---|
| #1480 | Cartographic Grammar 基座（grammar_solver/grammar_types） | grammar 求解权威已在；F02 只投影其输入面（GrammarRequest） |
| #1482 | capability ABI 2.0 + dispatch chokepoint | capability 真相在 registry；F02 不复制 |
| #1483 | export lineage + unified completeness verdict | completeness 权威在 product_completeness；F02 只投影义务 |
| #1485 | canonical turn lifecycle + event journal | turn/事件真相在 harness kernel；F02 只记录 turn 号 |
| #1486 | trace/replay 闭环 + 决策级 drift 归因 | replay/digest 基建在 `lib/harness/replay`；requirement 级 fingerprint 缺失 → F02 补 |
| #1487 | layered GIS context scopes | mission working context 存在；F02 文档落 SessionStore map_state 面 |
| #1488 | measurement semantics + field_resolver 生产接线 | field_resolver 是指标短语解析权威（FieldQuery/needs_clarification fail-closed）；F02 复用不复制 |

结论：Prompt 列出的未完成面在 9e1ad229 上**仍然成立**——没有任何 PR 已实现 requirement 文档化、条目级 lifecycle、字段级 provenance、clarification 生产去重、requirement digest、可回放 patch 协议。

## 3. 现有机制全图（与 F02 相关部分）

### 3.1 理解面（已存在，直接复用）
- `app/services/gis_harness/intent.py`：`MapRequestIntent`（pydantic v2，validate_assignment）＝ task（21 值 TaskType）/scope/subject/entity_type/geometry_expectation/measure/group_by/time/comparison/analysis_intents/cartography_intents/output_intents/export_intents/confidence/intent_evidence/degraded_reason/fallback_decision/clarification。入口 `resolve_map_request_intent`（纯函数）与 `merge_intent_hints`（白名单合并 + protected tasks 护栏）。
- `app/services/gis_harness/intent_semantic.py`：双语规则/槽位/城市快路径/置信度（intent_evidence_weighted_v1）；slot.source 词表 `rule|llm|ontology|service|session`。
- 消费点：`tools.py`（webgis_map_intent，tier=1，result 组装 :680-702）、`plan_orchestrator.py`、`workflow_compiler.py`、`planner.py`、`template_selector.py`、`recipes.select_candidates`。

### 3.2 约束面（已存在，作为基型）
- `goal_satisfaction/contracts.py`：`GoalRequirement`（id/kind/summary/required/polarity(must|must_not)/pinned/capability/export_format/scope_name/group_by/source）、`GoalContract`（schema goal_contract.v1）、`GoalEvidence`（kind/evidence_class/source/revision/confidence/ref/detail + PASS_CAPABLE_CLASSES fail-closed）。
- `goal_satisfaction/requirements.py`：`derive_goal_contract`（红线 D-004：只从结构化事实派生）、`contract_fingerprint`。
- `evaluator.resolve_goal_contract`（:144-156）已支持显式 `chapter["goal_contract"]` 注入 → IR 的升级载体缝。

### 3.3 澄清面（骨架已存在但无生产接线）
- `clarification.py`：6 个触发 reason code（low_confidence/missing_subject/missing_area/display_without_subject/rule_semantic_conflict/task_ambiguous）+ 4 个 fallback code；`ClarificationRequest`（≤2 问 × ≤4 选项）；`ClarificationPolicy.evaluate(asked_slots)` 去重 + `load/save_asked_slots`（SessionStore map_state）。**全仓无生产调用方**（仅测试路径）；无前端送达通道；`apply_answers` 同样未接线。

### 3.4 增量面
- `intent_diff.py`：章节计划行级 diff（fine_dims/global_reshape/carried/lost），消费于 `session_plan.py` supersede/replace；**不是 requirement patch 协议**。
- MapSpec mutation：`mapspec/lifecycle_engine.py`（expected_revision CAS、幂等键、事务回滚）+ `mapspec/intent_codec.body_to_intent`（Body→MutationIntent 单一映射）。
- user-wins：`doc.lockedLayerIds/lockedComponentIds`、`layer_hidden_by_user` 披露（intent_acceptance.py:127-130）、`origin="user"` mutation provenance、feedback signal 极性表（cartography/intent_learning.py）。

### 3.5 指纹/回放面
- `workflow_instance.canonical_fingerprint`（:129）＝ goal_satisfaction/goal_graph 的既有指纹真相。
- `lib/harness/replay/determinism.py`（canonical_json/sha256/behavior_digest）；**requirement 级 digest 不存在**。

## 4. 五列表

| 列 | 内容 |
|---|---|
| **Overlap** | `MapRequestIntent`（typed 理解面）；`GoalContract/GoalRequirement/GoalEvidence`（约束/证据面）；`clarification.py`（typed code + 会话幂等骨架）；`intent_diff.py`（计划行 diff）；`CartoIntentEvidence`（裁决落库） |
| **Already Done（直接复用）** | 双语解析/置信度/证据包（intent+intent_semantic）；EvidenceClass 分级；canonical_fingerprint 纪律；user-wins 全链（locked ids→disclosures→USER_OVERRIDE→feedback）；mutation 事务/幂等；standards 词表（MAP_PURPOSES/MAP_AUDIENCES/MAP_MEDIUMS/OUTPUT_PURPOSES） |
| **Still Missing（F02 新建）** | ① 版本化持久 RequirementDocument（GISIntentSpec+MapRequirementSpec）；② 需求条目级 lifecycle（proposed→clarified/accepted→superseded）；③ 字段级 evidence/source/ownership（现有 GoalEvidence 为需求粒度）；④ clarification 的生产策略统一（只问正确性/不可逆、context-key 去重、安全默认 rationale）；⑤ requirement 级 deterministic digest（同义稳定）；⑥ 多轮可回放/可 diff/可归因 patch 协议 |
| **Must Not Touch** | `mapspec/lifecycle_engine.py`+`store.py`；`lib/gis/capability_registry.py`；`gis_harness/recipes.py`；`lib/gis/field_resolver.py`、`lib/cartography/grammar_solver.py/grammar_types.py`（F10 热区）；`app/services/map_plan_compiler/`（F12）；`lib/harness/replay/*`（F09）；`lib/cartography/composition_selection.py`+`templates/`（F11）；`gis_harness/completion/pipeline.py`（#1500/#1504）；`session_plan.py` 行状态；`intent.py/intent_semantic.py` 语义权威（只读复用） |
| **Integration Seams** | 新包 `app/services/gis_harness/requirement_ir/`（services 层，包内纯子模块零 IO）；构建入口挂在 `webgis_map_intent` 旁路（加法、fail-open，仿 skill_guidance 先例）；需求派生遵守 D-004 结构化事实红线；goal 合约升级走 `chapter["goal_contract"]` 显式缝；投影出口＝各权威模块既有输入面 |

## 5. 已知基线测试状态

- `tests/unit/gis_harness/test_intent.py`：34 passed（worktree 干净基线，pytest -n 0，39s）。
- 仓库测试基线由 `tests/conftest.py` 钉扎环境；本分支新增失败须与之严格区分。

## 6. 风险

- grammar_solver/field_resolver 处于 F10 open PR 热区：F02 只做**只读 import + 输入面投影**，不修改其文件；merge 时以最新 master 重跑兼容测试。
- `clarification.py` 现有 6 code 与 IR 新增 code 需要命名空间共存：IR code 不与旧 code 重名，兼容映射单向（IR→legacy ClarificationRequest）。
- SessionStore 为 async 协议：service 层用可注入 store 协议，单测注入内存实现，不触 Redis。
