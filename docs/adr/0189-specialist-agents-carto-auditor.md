# ADR-0189: Specialist Agents Pack — Cartographer & CriticAuditor（agent-swarm/05）

- 状态：Proposed（随 `agent/05-specialist-agents-pack-carto-auditor` 分支评审）
- 日期：2026-09-15
- 关联：ADR-0187（swarm 总控编排：委派织物 / Governor / receipt 总线）、
  ADR-0188（专家包基座：BaseSpecialistAgent / RBAC / 输出契约）、
  ADR-0183（Goal Satisfaction Evaluator：false_pass_rate==0 / 不造第二
  verdict / PASS_CAPABLE_CLASSES）、ADR-0180（situation world model：
  unknown 显式成事实）、ADR-0152（symbology v2 唯一裁决）、ADR-0118
  （AUTO_SAFE 修复与 user-wins 抑制）、ADR-0078（no-data 语义）、
  ADR-0061（无证据 ≠ fail）
- 目标书：`agent-swarm/05-specialist-agents-pack-carto-auditor`
- 规格：`docs/dev/carto-auditor-spec.md`

## 背景

ADR-0188 落地了专家包基座与首批两名专家（Data Scout / GeoCompute），
但 swarm 仍缺两端能力，对应本任务的两个痛点：

- **痛点一（制图专业性不足）**：通用 Agent 面对多字段空间图层叠加时，
  倾向默认色板与单一图层类型，不推理视觉变量（位置 / 大小 / 形状 /
  明度 / 色相）的通道占用，也不做版面整饰（图例闭环、比例尺 / 指北针
  必配、组件防撞）。而仓库已有成体系的**确定性制图知识库**：
  `app/lib/cartography/` 的分类五法（含 Fisher-Jenks）、ColorBrewer
  色板表与 WCAG/CIEDE2000/CVD 工具、`resolve_symbology` 唯一裁决、
  `required_components_for` 必配基线、`solve_layout_v4` 自愈防撞链、
  约 50 项 `evaluate_cartography_semantics` 语义检查、
  `review_and_repair_cartography` 质量回路，以及 `RecipeRegistry`
  （17 seed + 25 领域包 = 164 配方）的配方先验。缺的不是制图数学，
  是把它们编排成"一名制图专家"的专精层。
- **痛点二（缺乏客观独立质检）**：自己写、自己调、自己判完成是单
  Agent 架构的自我确证（self-certification）弱点。ADR-0183 已给出
  `evaluate_goal_satisfaction` 纯函数评估面与反作弊语料
  （false_pass_rate 必须恒 0），但它评估的是 chapter 证据面，不对
  "某次制图交付物"出具结构化审计单；swarm 内也没有一个与实现解耦、
  拥有一票否决权的独立裁判角色。

分支纪要：本分支基线为 `agent/04`（origin/master 尚未合入 agent_swarm
系列），并合入 `agent/03`（ADR-0187 总控）。合并的 add/add 冲突按
"内容零改动、只改模块路径"调和：ADR-0187 的契约层改名
`delegation_contracts.py`，ADR-0188 的输出契约保留 `contracts.py`。

## 决策

### D1 — Cartographer = 制图知识库挂载 + 确定性裁决编排（不是提示词审美）

`app/services/agent_swarm/specialists/cartographer.py` 的
`CartographerAgent` 继承 `BaseSpecialistAgent`（ADR-0188 D1 同门），
把三类**既有确定性资产**挂载为知识库，`compose()` 全程零 LLM：

1. **色板分类规则域**：`distribution_stats_from_values` →
   `choose_classification`（分布驱动：重尾 → head_tail，适度偏态 →
   `natural_breaks`/Jenks，近均匀 → equal_interval/quantiles；落选者
   全程留痕）→ `classify_values` → `symbology_decision_from_values`
   （method×k×palette×clip_policy，CIEDE2000 可分辨 + CVD/print 硬
   约束）→ `build_graduated_spec` / `build_categorical_spec` →
   `resolve_thematic_colors`；
2. **组件版面整饰域**：`required_components_for`（title/scale_bar/
   north_arrow/attribution 全用途必配，专题层在场 → legend 必配）→
   `solve_layout_v4`（防撞自愈链 L1 改 anchor → L2 缩尺寸 → L3 折叠
   → L4 隐藏，轨迹入 `repair_steps`）→ `score_layout` 复评；
3. **多尺度与表达式域**：`spec_to_paint` 单一投影点保证图例 ↔ paint
   不漂移；`carto.scale.svs`（小多边形不可符号化）与
   `CLASSIFICATION_DOMAIN_COVERAGE` 等检查作为自检门。

配方先验：`get_recipe_registry()` 的 `select_candidates` /
`keyword_hits` 提供 `primary_cartography` / `default_components` /
推荐分类器等先验；先验只参与排序，不覆盖分布证据。产出经
`canonicalize_mapspec` 过 schema 校验后封装为 `MapSpecDeliveryRef`
（D3）。

### D2 — CriticAuditor = 只读独立审计 + 一票否决（Veto Power）

`app/services/agent_swarm/specialists/auditor.py` 的
`CriticAuditorAgent`：**完全不参与实现**（角色档
`allow_mutation=False`，工具白名单仅审计只读面），只读挂接
`resolve_goal_contract` + `evaluate_goal_satisfaction`（ADR-0183）、
`review_cartography`（只读制图审，复用语义检查全表）、
`explain_trace` 形状的披露面，产出结构化《交付质量审计单》
`DeliveryAuditReport`（D3）：`verdict`、`goal_score`（派生口径：
`counts.fulfilled / len(required)`，**必须**携带
`goal_score_derivation` 披露，不造第二 verdict，符合 ADR-0183）、
`uncovered_requirements`（required 且 verdict ≠ FULFILLED）、
`cartography_risks`（blocking + warning 规则码）、`vetoes`、
`improvement_notes`。

**一票否决红线**（命中任一 → `verdict="fail"`，不可被任何"总体尚可"
对冲；每条 veto 附带不可抵赖因果链：`rule_id` + `severity` +
`message` + `audited_fingerprint`（锁定被审代际）+ `evidence` +
`suggested_fix`）：

| id | 红线 | 判定源（全部确定性） |
|----|------|----------------------|
| V1 | 图例未闭环 | `carto.legend.completeness` fail；可见专题层无 `legend_spec`；`LEGEND_FIELD_CONSISTENCY` fail |
| V2 | 目标要素缺失 | required 需求未满足（`uncovered_requirements` 非空）；必配组件缺席（`required_components_for` 基线 vs `layout.components`） |
| V3 | 指标口径未对齐 | `goal_score < success_threshold`；goal 契约 map 需求 verdict 非 READY 族（复用 evaluator 失败规则词表，不自造） |
| V4 | 数据空洞 | `EMPTY_DATA` / `RESULT_DATA_PRESENCE` fail；`NO_DATA_SEMANTICS`（数值分类无 nodata 规则且有 null） |

**fail-closed 三原则**：无证据 ≠ pass（`not_evaluated` → fail，披露
缺失面，与 ADR-0061 口径一致不伪装）；visual/assisted 证据永不单独
背书 PASS（`PASS_CAPABLE_CLASSES` 同门）；auditor 只读不重算——
G7 已有裁决（READY 族 / final_map_status）按原样采信为口径，不二次
发明判定，报告结论是 advisory，不抢 `task_complete` 权威
（ADR-0183 D-007 同门）。

### D3 — 输出契约冻结（`agent_swarm.contracts` 追加式）

- **`MapSpecDeliveryRef`**（cartographer → 下游唯一通货）：
  `ref_id`（`ref:mapspec-*` 提货券）、`capability="thematic_map"`、
  `mapspec_fingerprint`（`cartographic_fingerprint` 同源，代际锁定）、
  `digest`、有界摘要（layer_count / legend_visible / classification
  {field, method, k, palette} / components 词表 / warnings ≤8）。
  **MapSpec 载荷不过境**（Zero Big Data in Context 同门）：载荷存进程内
  `MapSpecLedger`（`specialists/ledger.py`，ref→payload 有界账本），
  消费方凭 ref 取。
- **`DeliveryAuditReport`**（auditor → 总控/上游唯一通货）：bounded
  pydantic，含上述审计单字段 + `audited_ref_id` + `round_index` +
  `review_status`（quality_loop 词表）。
- 冻结规则与 ADR-0188 D3 同门：v1 之后只允许追加可选字段并携带升级
  测试；改名/改类型必须过 ADR。

### D4 — 对抗博弈闭环 `CartoAuditDuoSession`（组合总控原语，不改 run_swarm 本体）

`app/services/agent_swarm/duo_session.py`：有界对抗循环

```
R0  cartographer.compose(request) → delivery
    auditor.audit(delivery, chapter) → report
    report.verdict == "pass" → 收敛出口
Ri  cartographer.revise(delivery, report)   # 仅按 veto/improvement_notes
                                            # 的 suggested_fix 修复（AUTO_SAFE 优先）
    auditor.audit(...) → 达标出口
上限 MAX_REPAIR_ROUNDS = 2（与 quality_loop MAX_REPAIR_ITERATIONS 同量级）
超限 → verdict="failed_escalated"，全程 receipts + 审计单上交（诚实失败，
绝不静默放行）
```

挂接总控（ADR-0187）的方式：

- **角色注册即路由**：`registry.py` 注册 `cartography_specialist` /
  `audit_judge` 两个角色档（`SwarmSpecialistRole` 既有枚举值），
  `SpecialistDispatcherRuntime._role_name` 按 `role.value` 查
  `SUBAGENT_ROLES` 命中后，`HeuristicSpatialDecomposer` 既有相位
  `swarm.cartography.compose` / `swarm.audit.judge` 在 LLM 委派路径
  自动获得专属角色档与提示词边界，**零 dispatcher 改动**；
- **进程内确定性执行面**：`InProcessSpecialistRuntime` 实现
  `SpecialistRuntime` Protocol（dispatcher.py:36），按 assignment 的
  capability 路由到 `compose/revise/audit` 确定性方法，产出经
  `normalize_receipt` 归一为 `SubagentReceipt`；
- duo session 复用 `validate_swarm_graph` / `SwarmConcurrencyGovernor`
  （并发硬上限 ≤3 的同一信号量纪律）/ `SwarmAggregator` 聚合
  manifest，**不修改** `SwarmOrchestrator.run_swarm` 本体（组合优于
  侵入，ADR-0187 的四端口全部保持可注入）。

### D5 — RBAC 工具面（两两不相交不变量）

- **cartographer**（`cartography_specialist` 角色档）：
  `create_thematic_map` / `apply_layer_style` / `webgis_layout_set` /
  `export_thematic_map` / `webgis_compile_maplibre` / `combine_map_theme`
  （制图与版面出图面，不碰数据接入与重计算）；
- **critic_auditor**（`audit_judge` 角色档）：
  `audit_spatial_quality` / `gis_skill_replay_check`（审计与回放校核
  只读面，零 mutation）；
- 角色档：auditor `allow_mutation=False`、`failure_behavior=
  "fail_closed"`、`budget_class="light"`；cartographer
  `allow_mutation=True`（出图写面）、`budget_class="standard"`；
- 不变量测试：四专家 `TOOL_ALLOWLIST` 两两不相交（扩展 ADR-0188 的
  disjoint 测试）。

### D6 — 心跳 / 熔断 / 契约边界沿用基类

`heartbeat(stage)` 逐相位续期、`check_deadline()` 墙钟熔断、
构造期白名单存在性校验（fail-closed）全部继承 `BaseSpecialistAgent`
（ADR-0188 D7），本 ADR 不新增存活管理语义。

### D7 — v1 非目标

- 不做 LLM 审计叙述：审计单全部确定性产出（LLM 只在委派路径做解释）；
- 不造第二 goal evaluator / 第二 verdict 词表（ADR-0183 红线）；
- 不修改 `quality_loop` / `semantic_checks` / `goal_satisfaction` 既有
  语义，只做只读消费；
- 不迁移 ADR-0188 两专家的文件位置：`specialists/` 子包自 05 起新增
  专家入包，04 两专家原地保留（目录双态为显式过渡，迁移须另立 ADR）。

## 后果

### 正收益

- 制图专业性与版面整饰成为 swarm 内**可复用的确定性能力**，同一输入
  必产同一 MapSpec（fingerprint 可复现），视觉变量与防撞自愈不再依赖
  通用 Agent 的临场发挥；
- 独立审计把 ADR-0183 的 false_pass_rate==0 纪律延伸到交付物粒度：
  四类高危缺陷（图例 / 目标要素 / 指标口径 / 数据空洞）在结构上无法
  通过 self-certification 溜出；
- 对抗闭环收敛轮次有界（≤2），失败诚实升级（failed_escalated），
  全程 receipt 与审计单可回放、可归因。

### 代价与风险

- `specialists/` 子包与 04 平面文件并存的目录双态（D7 显式接受）；
- `MapSpecLedger` 是进程内有界账本，不跨进程；跨进程消费需接
  ref 存储（届时仅换 ledger 实现，契约不变）；
- `goal_score` 是派生口径而非新裁决，若消费方忽略
  `goal_score_derivation` 可能误读——该字段为必填披露面；
- 语义检查表约 50 项，审计单仅投影 blocking/warning 规则码，全量
  findings 留在 review 证据内（有界与完备的折中）。

### 验证

- TDD 矩阵：`tests/unit/test_specialist_carto_auditor.py`（规格
  `docs/dev/carto-auditor-spec.md` §8，T1–T22：制图裁决 / 版面必配 /
  审计四红线 / fail-closed / 对抗收敛 ≤2 轮 / RBAC / registry /
  governor 并发纪律 / orchestrator 相位路由）；
- 对抗回归：false_pass 反例（缺图例 / 空数据 / 口径不齐）必须全部
  veto，套用 `goal_satisfaction_corpus` 的 must_not_pass 模式；
- 门禁：`pytest` 全绿 + `ruff check`（agent_swarm 无豁免）+
  四专家工具面两两不相交不变量。
