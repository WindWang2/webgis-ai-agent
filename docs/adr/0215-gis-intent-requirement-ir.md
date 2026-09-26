# ADR-0215: GIS Intent & Requirement IR（需求中间表示）

- 状态：Accepted
- 日期：2026-09-26
- 方向：F02（GIS Intent & Requirement IR）
- 基线：master `9e1ad229`
- 配套：`docs/dev/f02-gis-intent-requirement-ir-recon.md`、`docs/dev/f02-gis-intent-requirement-ir-decisions.md`

## 背景与问题

仓库已有稳定的"理解"（`MapRequestIntent`，ADR-0150）、"约束验收"（`goal_satisfaction` GoalContract，ADR-0183）、"澄清骨架"（`clarification.py`）与"计划增量"（`intent_diff`），但没有一个**稳定、可版本化、可回放**的"用户到底要什么"的需求文档：AOI/指标/统计口径/地图用途/输出要求散落在 chapter dict、plan JSON、MapSpec、DB 证据行与记忆缓冲中。后果：

1. 多轮请求（"改成各区统计""隐藏道路""换蓝色""再导出 PDF"）无法以结构化 patch 表达，只能整体重解析；
2. 同一歧义可能反复追问（clarification 的会话幂等骨架无生产接线）；
3. 无 requirement 级指纹，同义请求不可比较，replay/drift 只能到 plan/behavior 粒度；
4. 字段级"这是谁说的/依据什么"无载体，用户显式选择与系统默认不可区分。

## 决策

引入版本化需求文档 **`RequirementDocument`（schema `requirement_document.v1`）**，包含：

- **GISIntentSpec**：理解面——内嵌既有 `MapRequestIntent` 为语义核心（不复制其规则），外加 AOI/datasets/measures/time/statistics/spatial_relation/purpose/audience/representation/required_components/output 等 typed sections，section 级 + 稀疏字段级 provenance（origin: user/rule/llm/ontology/memory/service/default）与条目状态。
- **MapRequirementSpec**：义务面——`RequirementItem`（kind 复用 goal_satisfaction.RequirementKind 词表）+ lifecycle（proposed→clarified→accepted；superseded/rejected 终态）。
- **Patch journal**：白名单 op/path、CAS（expected_revision）、op_id 幂等、有界 200 条 + fold 计数；user-wins 硬约束（user-origin 字段仅 user actor 可改）。
- **歧义账本**：typed clarification code + context_key 去重 + blocking/安全默认(rationale) 策略表，兼容产出旧 `ClarificationRequest`。
- **双 digest**：`requirement_digest`（规范化语义核，同义稳定）/ `document_digest`（envelope 变更检测）。
- **六路单向投影**：intent_view / field_query_inputs / grammar_request_face / template_obligations / export_obligations / goal_requirements ——只写下游权威模块的输入面。

落点 `app/services/gis_harness/requirement_ir/`（services 层、包内纯子模块零 IO）。生产接线为 `webgis_map_intent` result 的 omittable `requirement` 键（fail-open，仿 skill_guidance 先例）；多轮增量经既有工具入口进入 patch 协议。

## 不做什么（边界）

- 不造第二套 LLM planner / agent loop（Pi 仍是 loop 权威）；IR 不发起任何工具调用。
- 不把自由文本 prompt 塞进 MapSpec 当真相；IR 不写 MapSpec、不选工具、不算 grammar/completeness 结果。
- 不复制 field_resolver / cartographic grammar / template selector / capability registry 的权威逻辑。
- 不改 `intent.py` / `clarification.py` 既有公共契约（只 import 复用与单向兼容映射）。

## 后果

- 正面：多轮增量变为可 diff/可归因/可回放的 patch 流；歧义不重复追问；同义请求有稳定 digest；用户显式选择（origin=user）在重建/超替中强存活。
- 代价：新增一个契约面需要与 #1502/#1503 等 open PR 在 merge 时对齐（本 ADR 只读 import 其输入面，文本无冲突）。
- 后续项：representation 面（用户隐藏图层/锁定图层/palette）目前只入 IR 与 digest，其 MapSpec mutation 接线属后续方向；document_id 为无密钥 sha256 截断（仅 session 内自见），未来可换 HMAC。
- 退役路径：若未来 goal_satisfaction 升级为 requirement 文档化，`RequirementDocument` 可整体作为 `chapter["goal_contract"]` 显式缝的升级载体迁移，包级 API 收敛为一个入口。
