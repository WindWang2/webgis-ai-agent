# 科学 Oracle 分类表（SCIENTIFIC_ORACLES）

> G02-GIS-EXECUTION-COMPILER。把「科学上不可混淆的语义」钉为可验证 Oracle：
> 每条给出验证入口（函数 / 测试）、fail 形态、pass 判据。静态对账层
> （`app/services/gis_harness/execution_compiler/units.py`）与既有运行期科学层的
> 分工见 §4——**不重合、不接管**。配套管线图见 `GIS_COMPILER_PIPELINE.md`。

## 1. Oracle 总表

| ID | Oracle | 一句话判据 | 静态对账入口 | 运行期权威（不在本层） |
|---|---|---|---|---|
| U1 | 单位量纲互斥（time/length/area/dimensionless） | 秒≠米、分≠公里——`UNIT_DIMENSIONS` 维度不一致即拒绝（Issue #2005 形态） | `units.dimensions_compatible` | 工具 apply_contract / families `unit_contract` |
| U2 | 计数≠率 | 未归一化计数不得被许诺为率；率/人均语义分母缺席 → `missing_denominator` 只给计数+披露 | `units.build_unit_obligations` / `_adjudicate_denominator` | orchestrator `AWF_DENOMINATOR_MISSING` typed skipped |
| U3 | 点≠密度 | 视觉热力（表达候选）≠ 定量密度口径（单位义务）；密度表达需足够点支撑 | `units.EXPRESSION_SEMANTICS` + `requirement_to_tasks.MIN_DENSITY_POINTS` | 工具密度算子 / `point_support_required` 前置 |
| U4 | 几何自足分母 vs 外部分母 | 面积分母由 boundary 几何计算（自足）；人口/字段分母必须外部数据在证 | `units._adjudicate_denominator`（density/per_area 分支） | families `area_density`（`denominator_kind=area`）/ `per_capita_rate`（field） |
| U5 | 网络距离≠欧氏 | 等时/最近设施结论只出自真实路网 Dijkstra；buffer 只是 proxy 且必须披露 | families `_FAMILY_NOTES` / `_ROLE_ALTERNATIVES`（声明面） | orchestrator `AWF_NO_NETWORK_FOR_ACCESSIBILITY` typed blocked |
| U6 | M:N 多方属属披露 | intersects/spatial_join 的多方属合计不得当独立计数——必须随族披露声明 | families `_FAMILY_NOTES` → `contract.risk_notes` | plan/run 返回面 `risk_notes`；验收断言与披露一致 |

## 2. Oracle 详表

### U1 单位量纲互斥（#2005 形态）

- **词表**：`units.UNIT_DIMENSIONS`（封闭）——`m/km→length`、`s/min/h→time`、
  `m2/km2→area`、`unitless/percent/ratio→dimensionless`。与 families
  `UNIT_VOCABULARY`（`m/km/min/h/unitless`）对齐并加派生单位（s/m2/km2/percent/ratio）。
- **验证入口**：`units.dimensions_compatible(expected, actual)` /
  `units.unit_dimension_of(unit)`；测试
  `tests/unit/gis_harness/test_execution_compiler_characterization.py::
  TestUnitObligations::test_time_and_distance_units_never_conflated`
  （断言 `UNIT_DIMENSIONS["min"]=="time"`、`["km"]/["m"]=="length"`、`["h"]=="time"`）。
- **fail 形态**：维度不一致（期望长度、实际时间）→ `False`；任一单位在词表外 →
  `unit_dimension_of="unknown"` → 同样 `False`（fail-closed：无法裁决 = 不宣称
  兼容，绝不静默放行）。
- **pass 判据**：两单位映射到同一量纲维度且均非 unknown。
- **运行期对偶**：families 步骤 `unit_contract`（如 `hazard_buffer` 的
  `{"distance": "m"}`、`isochrone` 的 `{"travel_time": "min"}`）在
  `validate_workflow_contract` 做 `AWF_UNIT_CONTRACT_VIOLATION` 词表校验；
  参数覆盖换单位时 unit_contract 联动（`build_contract`）。本层只做声明面静态
  对账，不重复执行期判定。

### U2 计数≠率（missing_denominator 披露）

- **语义投影**：`units.statistic_semantics(statistic, normalization)`——
  归一化口径优先于 measure 词面（「数量」+「每万人」= per-capita 率，不是计数）；
  `_STATISTIC_SEMANTICS` 把 `count/sum→count`、`density→density(area)`、
  `rate/ratio→rate(field)`、`share→share(total)` 等。
- **验证入口**：`units.build_unit_obligations` → `UnitObligation.verdict`；
  测试 `test_count_statistic_never_promised_as_rate`（计数义务不得出现
  rate 语义 + violated）、
  `test_per_capita_without_denominator_is_disclosed_not_fabricated`
  （每万人请求必须产生义务；分母缺席必须进 `TaskCompilation.disclosures`）。
- **fail 形态**：`per_capita/rate/ratio` 语义而 `data_facts["denominator"]`
  缺席 → `missing_denominator`（detail：「只输出原始计数并披露，不得伪装率」）；
  声明语义与事实矛盾 → `violated`；无分母义务语义（count/none）→ `satisfied`；
  无量纲任务 → `not_applicable`（`UnitObligationVerdict` 封闭四态）。
- **pass 判据**：分母角色数据事实在场（`data_facts["denominator"]` 非空）→
  `satisfied`（「per-capita 率可计算」）。
- **运行期对偶**：orchestrator 对 optional 分母步骤 typed skipped
  （`AWF_DENOMINATOR_MISSING`，含列清单校验 `_denominator_field_gate`——只认
  `role:denominator` 的 `qualifier.fields`）；families `per_capita_rate`/
  `grid_rate` 步骤声明 `denominator_kind=field`。

### U3 点≠密度（视觉 vs 定量分层）

- **分层原则**：义务面（`unit_obligations`）只在**定量口径**（measure=
  density / normalization=per_area 等）产生；视觉热力语义走**表达候选**
  （`ExpressionCandidate.semantics`，来源 `units.EXPRESSION_SEMANTICS`：
  `heatmap/grid_density→density`、`point_map→count`、
  `per_capita_choropleth→per_capita`…）。
- **验证入口**：
  `test_family_step_units_align_with_ir_statistics`——「成都每平方公里小学
  密度」必须在义务面出现 `density/per_area` 语义；「成都小学热力图」在表达面
  出现 `kind=heatmap` 且 `user_requested=True`，义务面不产生定量口径。
- **fail 形态**：数据事实在场而 `feature_count < MIN_DENSITY_POINTS(30)` →
  密度类候选 `suitable=False`（理由携带计数）；数据事实缺席 →
  `suitable=None` + 披露 `expression suitability unknown`（不虚构）。
- **pass 判据**：`feature_count ≥ 30`（密度类）或 `> 0`（点图）；
  choropleth 类需 `data_facts["boundary"]` 在场（per_capita 另需
  `data_facts["denominator"]`）。
- **运行期对偶**：`statistical_zones.grid_stats` 的
  `point_support_required` 前置 + `semantic_contract`（非点几何执行期 typed 拒，
  不静默质心化）。

### U4 几何自足分母 vs 外部分母

- **判据**：`density/per_area` 语义的面积分母由 boundary 几何计算（自足——
  boundary 事实在场即 `satisfied`；缺席 → `not_applicable` + 披露「执行期
  family 单位合同权威裁决」）；`per_capita/rate` 语义必须外部 `denominator`
  角色数据在证（缺席 → U2 的 `missing_denominator`）。
- **验证入口**：`units._adjudicate_denominator`；characterization 的 U2 测试
  同时覆盖本分支（per_capita 请求 + 无分母事实）。
- **fail 形态**：把几何自足分母（面积）当作外部分母的替代（拿 area 密度或
  原始计数冒充 per-capita 率）——在词面上即语义投影错误：`per_capita` 永远
  映射 `denominator_kind=field`，不会回落到 area。
- **pass 判据**：语义→分母类型映射（`statistic_semantics` 三元组）与族步骤
  `denominator_kind` 声明一致（`area_density` 步骤 `area`、`per_capita_rate`
  步骤 `field`）。
- **运行期对偶**：families `admin_facility_distribution.area_density`
  （几何自足，无需外部数据，合法）与 `per_capita_rate`（optional；分母缺失
  typed skipped，ADR-0099 对齐 `ScienceContext.denominator_policy=
  "explicit_only"`）。

### U5 网络距离≠欧氏

- **判据**：等时圈=路网 Dijkstra 可达（速度表近似）；最近设施距离=路网通行
  成本（默认 travel_time 权重）——**不是欧氏距离**；无路网时 buffer 替代
  科学含义不同，必须带 proxy 披露。
- **验证入口**：声明面 `families._FAMILY_NOTES`（`service_accessibility` /
  `network_proximity`）与 orchestrator `_ROLE_ALTERNATIVES["network"]`；测试
  `tests/unit/gis_harness/analysis_workflows/test_orchestrator.py`、
  `test_chain3_network.py`（`AWF_NO_NETWORK_FOR_ACCESSIBILITY` typed blocked
  + alternatives 含 buffer proxy 披露语义）。
- **fail 形态**：`service_accessibility`/`network_proximity` 的必需步骤消费
  `role:network` 而 network 缺失 → typed `blocked` + 专属码（「无真实路网——
  不得给出『15 分钟可达』/『最近设施网络距离』类数值结论」），runnable=False；
  绝无直线 15 分钟冒充。
- **pass 判据**：network 角色绑定在证（`geometry_kinds=["line","network"]`）→
  步骤 ready；无匹配路线的需求点由 `envelope.network.unmatched_demand_count`
  披露（工作流整体判 `partial`，不作完整总体成品）。
- **静态层角色**：编译器不裁决本条（无 IR 信号可判路网真伪）——只在族候选
  信号面（`spatial_relation.kind ∈ {near, service_area}` →
  `network_proximity`/`service_accessibility` 权重 4.0）把需求导向正确族。

### U6 M:N 多方属披露

- **判据**：`intersects` 聚合（点落共边界计入多区）、`spatial_join` 暴露清单
  （重叠缓冲区内一受体多行）、跨区路线（intersects 约定下计入途经每个区）都是
  M:N 多方属——**合计不得当独立计数**，必须随族披露声明并进验收口径。
- **验证入口**：声明面 `families._FAMILY_NOTES`（`admin_facility_distribution`/
  `hazard_exposure`/`network_proximity`）→ `build_contract` 进
  `contract.risk_notes` → 工具 plan 返回 `risk_notes` 字段；测试
  `tests/unit/gis_harness/analysis_workflows/test_executor_e2e.py`（边界点
  多方属：合计 > 去重总数 且 `any("多方属" in note for note in
  plan.contract.risk_notes)`）、`test_known_answer_oracles.py`（计数守恒：
  无丢失 + 多方属真实发生）。
- **fail 形态**：合计≠去重口径而无披露（静默放大分母）；或披露缺席导致
  验收断言「总数=分区合计」误判。
- **pass 判据**：①无丢失（每个 POI 至少计入一区）；②多方属真实发生时披露
  在场；③结论与约定一致（在该约定下允许 `合计>去重`，且必须与披露一致）。
- **验收桥联动**：`acceptance_bridge.workflow_disclosures` 把
  `unmatched_demand_count` 与 skipped 步骤带进 map_product 块
  `workflow_disclosures`（≤8 条）——部分总体/义务缺席对 goal 验收可见。

## 3. 测试锚点

### 3.1 `tests/unit/gis_harness/test_execution_compiler_characterization.py`（已存在，19 测试）

| Oracle | 钉扎测试 |
|---|---|
| U1 | `TestUnitObligations::test_time_and_distance_units_never_conflated` |
| U2 | `TestUnitObligations::test_count_statistic_never_promised_as_rate`、`test_per_capita_without_denominator_is_disclosed_not_fabricated` |
| U3 | `TestUnitObligations::test_family_step_units_align_with_ir_statistics` |
| U2/U4 语义投影 | `TestRequirementToTaskCompilation` 六测（确定性/信号溯源/表达分离/点名优先/blocked/needs_clarification） |
| 桥（证据面） | `TestWorkflowReceiptGoalBridge` 三测（行投影/节点成功≠目标达成/failed 诚实阻断） |
| 声明面健康 | `TestFamilyCatalogConformance` 三测（live 全 pass/悬空 fail/指纹确定） |
| 失效面 | `TestPatchScopedInvalidation` 三测（style-only 零失效/换城全量/统计最小集） |

### 3.2 `tests/unit/gis_harness/test_execution_compiler_oracles.py`

**由本 PR 后续 commit 添加——当前 worktree 尚不存在**（不得声称已存在）。
计划覆盖面（对齐 `PLAN.md` Oracle 表 O1–O7 的语料断言部分）：

- U1 定向语料：`dimensions_compatible("km","min") is False`、词表外单位
  （如 `"ft"`）→ False；族步骤单位合同 × IR 统计词的交叉对账（拒绝
  「距离义务被行程秒数满足」的 #2005 形态组合）。
- U2 反例语料：rate 语义 + 分母事实缺席 → `missing_denominator` + 披露文案
  断言；violated 组合（计数被许诺为率）→ 编译期拒绝。
- U3 数据规模语料：`feature_count<30` → 密度候选 `suitable=False`；
  缺席 → None+披露；用户点名热力图数据不足 → 保留候选 + 不适合披露
  （不静默丢）。
- U4 分母通道语料：`per_area`（boundary 自足）与 `per_capita`（外部
  denominator）分道裁决断言。
- U5/U6 属运行期/执行器既有测试面（§2 所列 analysis_workflows 测试），
  oracles 文件只锁**编译器侧信号与披露**（如 spatial_relation 信号导向
  正确族、risk_notes 经 build_contract 在场）。

## 4. 分工：既有科学层 vs 新静态对账层（不重合、不接管）

| 层 | 职责 | 裁决时机 | 入口 |
|---|---|---|---|
| `app/lib/gis/scientific_preconditions.py` | 五值判定引擎（PASS / PASS_WITH_WARNINGS / REQUIRES_TRANSFORM / INSUFFICIENT_DATA / INVALID_METHOD），吃 resolver 画像事实；事实缺失 = PASS-deferred（unknown ≠ 满足，`pass_evidence_state` 区分 proven/deferred） | 算法选择 / 执行门 | `precondition_exists`；族步骤 `preconditions` 声明引用 |
| `app/lib/gis/science_final_gate.py`（G02 终局门） | 结果入图前 evidence 对账：G-FALLBACK-SEMANTICS / G-NOT-EVALUATED / G-FRESHNESS / G-SCIENCE-BLOCKED（block） | run 全步骤终态后 | `analysis_workflows/evidence.py::run_final_gate` → `state.science_validation` → 桥 `workflow_disclosures` |
| families `unit_contract`（`analysis_workflows/contracts.py` + `families.py`） | 步骤参数单位**声明** + plan 期词表校验（`AWF_UNIT_CONTRACT_VIOLATION`）+ 覆盖联动 + 入 `step_fingerprint`（换单位=新计算） | plan / run | `validate_workflow_contract` / `build_contract` |
| **`execution_compiler/units.py`（本方向新增）** | IR 统计词表 × 量纲语义的**声明性静态义务投影**：编译期把「这个需求承诺了什么量纲/分母」摊开为封闭四态裁决 + 披露；零运行期权力 | 编译期（`compile_requirement_to_tasks`） | `build_unit_obligations` / `dimensions_compatible` / `UNIT_DIMENSIONS` |

边界三条：

1. **不重合**：units.py 不评估运行期画像事实、不做五值判定、不复检
   envelope 实测单位——它对账的是「IR 声明的语义」与「量纲词表的映射」，
   在 dispatch 之前就把语义错配（秒≠米、计数≠率）以披露/blocked 形态暴露。
2. **不接管**：执行期权威仍在 scientific_preconditions / science_final_gate /
   工具 apply_contract；units.py 的裁决只进 `TaskCompilation.unit_obligations`
   与 `disclosures`，不阻断 plan_workflow、不改写 envelope。
3. **词表对齐**：`UNIT_DIMENSIONS` 是 `UNIT_VOCABULARY` 的量纲视角超集
   （多 s/m2/km2/percent/ratio 派生单位）；扩展走 additive，两表通过成员
   名对齐，不另造单位语义。
