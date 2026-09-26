# F02 — GIS Intent & Requirement IR · Architecture Decisions

配套 recon：`f02-gis-intent-requirement-ir-recon.md`；ADR：`docs/adr/0215-gis-intent-requirement-ir.md`。

## D-01 单包落点，包内分层

新包 `app/services/gis_harness/requirement_ir/`。理由：IR 需要 lifecycle 持久化（SessionStore）与 harness 接线 → services 层；但 contracts/classify/normalize/digest/patch/clarify/projection 子模块**零 IO、零 DB、零 LLM**，与 `intent.py`（纯函数核）同风格。service.py 是包内唯一 IO 面（可注入 async store 协议）。

## D-02 不造第二理解器：`MapRequestIntent` 是语义核心

GISIntentSpec **内嵌** `core: MapRequestIntent`（由既有 `resolve_map_request_intent` + `merge_intent_hints` 产出），typed sections（aoi/measures/statistics/time/…）由 core **确定性派生**（`derive_sections`），patch 通过显式 sync-map 同时维护 section 与 core 对应字段。IR 不复制规则表/词表/置信度模型。`projection.intent_view(doc)` 返回 core（含 sync 后字段），保证既有下游（recipes/template_selector/planner）零改动消费。

## D-03 需求面基型复用 GoalRequirement 词汇

`RequirementItem.kind` 复用 `goal_satisfaction.contracts.RequirementKind` 词表（map/analysis/comparison/statistics/chart/export），`goal_requirements(doc)` 投影直接产出合法 `GoalRequirement` 实例（构造期校验）——goal 合约是 IR 的下游而非平行物。

## D-04 字段级 provenance：section 级 + 稀疏字段覆盖

每个 section 携带 `provenance`（origin/turn/evidence_refs/rationale）与 `state`；spec 级 `field_provenance: dict[path, Provenance]` 稀疏记录与 section 不同的字段。origin 词表在 slot.source 基础上扩 `user|default`（`user` 即 ownership 最高优先）。所有可外泄对象携带稳定 reason code/rationale，不靠自由文本。

## D-05 user-wins 是 patch 层的硬约束

`PatchRecord.actor ∈ {user, agent, system}`；`origin=="user"` 的字段被非 user actor 触碰 → typed `PatchConflict` 拒绝；用户改锁只能经 `remove_lock`（user actor）。supersede/重建时 user-origin slots 与 locks **必须携带**（carry-over 表），rule-origin 字段允许重推导。

## D-06 clarification：只问 blocking，安全默认必须留 rationale

- code 命名空间：旧 6 code 语义复用；IR 新增 `measure_missing_for_statistics` / `time_range_missing_for_series` / `denominator_missing_for_normalized_statistic` / `export_format_missing_when_publish` / `measure_multiple_candidates` / `aoi_unresolved` 等，不与旧名冲突。
- 策略表声明每个 code 的 `blocking` 与 `safe_default(+rationale)`；只有 blocking（影响正确性或不可逆）才提问；非 blocking 记默认。
- 去重：`context_key = hash(code + 相关字段规范值)`。已问且 context 未变 → 不再问；context 变 → 新 key 可问；waived/answered 按状态终结。每次最多 2 问（对齐 ClarificationRequest 上限）。
- 兼容出口 `to_legacy_request()` 产出旧 `ClarificationRequest` 形状。

## D-07 digest 双轨：语义 digest 与 envelope digest

- `requirement_digest(doc)`：对**规范化语义核**（task/aoi/subject/measures 规范形/statistics/time/spatial_relation/purpose/audience/representation/显式组件/output/locks）做 canonical json + sha256。排除：raw phrase、provenance、turn、revision、patch journal、歧义状态、下游解析产物（field 解析值）。同义请求 → 同 digest（可 replay/drift 比较）。
- `document_digest(doc)`：含 revision/parent/lifecycle 的 envelope 指纹（变更检测）。
- 规范化（normalize.py）只用**有界表驱动**（行政区后缀、统计词、调色板名、格式名、purpose/audience 词表校验），不做开放式 NLP。

## D-08 patch 协议：白名单路径 + CAS + 幂等 + 可回放

- `PatchOp` 白名单（set/add_measure/remove_measure/answer_ambiguity/waive_ambiguity/add_lock/remove_lock/accept/supersede）；path 白名单绑定 schema，值经 pydantic 重建校验。
- `expected_revision` CAS（stale → `PatchStale`）；`op_id` 重复 → 幂等 no-op；journal 有界（200 条 + folded 计数）。
- `fold_patches(base, journal)` 纯重放 == 现网文档（回放不变式，测试覆盖）；`diff_documents` 输出带归因（哪个 patch/actor 改了哪条路径）。

## D-09 六路单向投影，全部只写"输入面"

1. `intent_view` → 既有 MapRequestIntent 消费方（recipes/planner/template_selector）。
2. `field_query_inputs` → field_resolver 的短语+量纲提示输入（解析权威不动）。
3. `grammar_request_face` → GrammarRequest 构造键面（purpose/audience 用 standards 词表，pinned_* 只透传 user-origin）。
4. `template_obligations` → template/composition 上下文义务。
5. `export_obligations` → export/completeness 义务（format/publish/dpi/output_purpose）。
6. `goal_requirements` → GoalRequirement 列表（goal_satisfaction 显式合约缝）。
IR 不写 MapSpec、不选工具、不算 grammar/completeness 结果。

## D-10 生产接线：webgis_map_intent 旁路，加法 fail-open

`ensure_document(session, query, hints, turn)`：载入会话文档 → classify（edit vs new task）→ edit 走 patch（diff→actor=user patches，保锁保确认事实）→ new task supersede 旧文档（携带 user locks）。工具 result 追加 omittable `requirement` 键（document_digest/requirement_digest/task_kind/revision/open_clarifications），失败静默省略——仿 `skill_guidance` 的 omittable-key 先例，不改 contract_version。多轮增量（改成各区统计/隐藏道路/换蓝色/再导出 PDF）经由**既有工具入口**进入 patch 协议，不新增第二 agent 工具面。

## D-11 边界与有界性

列表上限：measures≤8、ambiguities≤16、patches 保存 200（+folded 计数）、locks≤32、datasets≤16、stages≤4；字符串截断对齐 MAX_TEXT 风格。所有模型 `extra="forbid"`、可 round-trip 序列化。

## D-12 ADR 编号避让

open PR 分支已占用 ADR-0204–0214 → 本方向使用 **ADR-0215**，文件独立，无文件级冲突。
