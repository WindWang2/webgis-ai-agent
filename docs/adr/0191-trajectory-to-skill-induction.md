# ADR-0191: 基于执行轨迹的 GIS 技能自合成与演化引擎（Skill Induction Engine）

- 状态: Accepted
- 日期: 2026-09-15
- 线: agent/07-trajectory-to-skill-induction-engine（gis-skills/induction-v1）
- 关联: ADR-0182（GIS Skill / Procedure Library V1，产物规范事实源）、
  ADR-0183（Harness Replay + Benchmark：ReplayTrace 语料面；Goal Satisfaction
  Evaluator：满意度事实面）、ADR-0184（Execution Graph：图语义对齐）、
  ADR-0181（Capability Graph V1：能力词汇事实源）
- 勘察: docs/dev/skill-induction-spec.md（技术规范，含模块/签名/词表/测试矩阵）

## 1. 背景与问题

ADR-0182 建立了版本化的 GIS 技能库（37 个 core 技能，YAML 资产 +
fail-loud 装载），ADR-0183 建立了轨迹级可重放单元（ReplayTrace，
`replay-recordings/<sid>/<tid>.json`）与任务级满意度裁决
（GoalSatisfactionReport 经 completion pipeline 写入 finalization 块，
随 trace.verdict 持久化）。但整条链在"沉淀"方向是断的：

- 高满意度（≥0.95）的成功轨迹（例如「水质监测站时序超标多边形提取 +
  下风向人口暴露度评估」这类多步复合流程）在任务结束后只留下 JSON
  日志，系统无法把"这次是怎么做对的"泛化为"下次这类任务应该怎么做"；
- 技能资产 100% 依赖工程师手工编码与注册，无代码发布（no-code
  release）意义上的技能自主生长不存在；
- 反面同样成立：失败或低满意度的轨迹绝不能被固化为技能——错误分析
  模式一旦入库，会被 resolver 当作审定方法推荐，污染面远大于单次失败。

## 2. 决策

### D1: 语料与归纳门槛 —— ReplayTrace 是唯一语料面，满意度 fail-closed

- 语料 = `ReplayTrace.from_dict`（`app/lib/harness/replay/schema.py`），
  不直接消费 chain JSONL（证据链是评测门的面，不是打包单元）。
- 归纳准入是**合取门**，任一不满足即拒绝（fail-closed，全部确定性、
  零 LLM）：
  1. 满意度：`trace.verdict.map_product.goal_satisfaction` 在场，且
     确定性投影分数 `fulfilled / Σcounts ≥ 0.95` 且全局 `verdict ==
     satisfied`；**无满意度面的轨迹一律拒绝**（unknown ≠ 合格）；
  2. 完整性：`truncated == False`、无 `is_error` 工具调用、无
     `error_msg`、`outcome` 无失败码；
  3. 可泛化性：关键步骤参数不得为 `_digest_only` 降级形态（常量
     提取需要真实值；形状证据 `_arg_keys` 不足以支撑 typed 参数化）。
- 拒绝必须留下机器可读原因码（`IND-*` 词表），供轨迹治理面消费。

### D2: 抽象归纳三步法（参数泛化 → 数据流提取 → 契约封装）

1. **参数泛化（Parameter Generalization）**：对步骤参数做确定性模式
   识别，把硬编码常量提取为 typed 参数槽位——经纬度（ge 边界约束）、
   数值阈值（ge/gt 约束）、地名/文本（长度与字符约束）、字段名/类别、
   日期。产物是 Pydantic 动态模型（`pydantic.create_model`）表达的
   参数 Schema：新值可校验、越界值 ValidationError。敏感键（token/
   key/password/secret）与不可分类自由文本不泛化、不入 Schema
   （去毒前置）。
2. **数据流提取（Dataflow Extraction）**：将成功轨迹的连续工具依赖
   拓扑提炼为**声明式过程模板**——直接对齐 ADR-0182 `SkillProcedure`
   IR（steps/decisions/requirements/evidence_nodes/fallbacks），不新造
   第五套 DAG（尊重 ADR-0184 D1）。步骤 kind 由工具语义词表确定性
   映射（inspect/prepare/analyze/decide/design/validate/deliver）；
   依赖边 = 轨迹执行序；工具 → capability 投影可注入、缺省走
   registry 查证，未注册能力经 D5 动态挂钩以 `status="planned"`
   诚实登记（不虚构 native 能力）。
3. **契约封装（Contract Packaging）**：自动合成符合 ADR-0182 的
   `SkillContract`（含 SkillCard 选择面：intent_patterns/when_to_use/
   required_situation；质量义务列表；完成证据）+ 参数 Schema + 溯源
   （来源 trace 的 session/turn/digest），并渲染为版本化 YAML 资产。
   合成契约必须通过 `validate_contract()`（注入离线谓词）零违规——
   这意味着三触发器 fallback（missing_input/unsupported_geometry/
   insufficient_data）由编译器**强制生成**，不依赖轨迹碰巧包含。

### D3: 产物形态与词汇纪律 —— 引用不复制，induced pack 纯加法

- 产物是 ADR-0182 `SkillContract`，不是新技能格式；capability id /
  data role / artifact type / 语义词汇全部复用既有词表并经谓词校验。
- `SKILL_PACKS` 由 `("core",)` additive 扩展为 `("core", "induced")`
  （contract.py:52 已预留 pack registry 纯加法演进）：induced 技能
  是**草案资产**，与审定 core 资产隔离。
- 运行期不得自修改技能（ADR-0182 §2.5 红线）不破：induced 技能走
  独立目录、独立 fail-closed 装载面（`InducedSkillStore`），永不写入
  core fail-loud 单例；`SkillResolver` 经既有 `packs` 过滤参数按需
  纳入 induced 面。晋升（induced → core）= 人工 review 后把 YAML
  移入 `library/core/` 走正常 code review，引擎不自动晋升。

### D4: 安全沙盒与验证门禁（静态检查 + 去毒过滤 + 沙盒重放验证）

合成技能入库前必须全过三门，任一失败整体拒绝并给出原因码：

1. **静态类型检查**：`validate_contract()`（离线谓词）零违规 + 参数
   Schema 可实例化 + YAML round-trip 幂等（dump → load → model 相等）。
2. **去毒过滤（Detox）**：值级敏感模式过滤（token/key/password/
   secret 键名族）；文本字段代码注入模式拦截（`import `/`eval(`/
   `exec(`/`__`/`${`/jinja 标记/可调用片段）、绝对路径与 URL 拒绝、
   全部长度钳制（guidance ≤240 等 ADR-0182 红线）。去毒命中即拒绝
   （不静默改写后放行——合成产物宁可不入库）。
3. **沙盒重放验证（Replay Verification）**：零 I/O、零网络、零 LLM
   的离线 Mock 执行器：
   - *自重放*：以源 trace 投影 plan/evidence facts 调
     `replay_procedure`（ADR-0182 §2.7 既有函数），报告必须
     `complete`；
   - *变体重放*：以 N 组新参数（约束内合法值 + 约束外非法值）驱动
     Mock 执行器沿编译出的过程拓扑走全流程——合法参数必须产出与源
     轨迹同构的能力序列（拓扑指纹一致）与全部证据；非法参数必须在
     Schema 校验层被拒绝；过程不得因参数变化而增删步骤。

### D5: 动态外部扩展挂钩（Dynamic Skill Provider）

`app/lib/gis/capability_registry.py` 增加**数据-only** 动态扩展挂钩：

- `register_dynamic(descriptor)`：带命名空间前缀纪律（动态能力 id
  必须带 `induced.` 前缀），重复 id 一律 raise（与 `register()` 同一
  fail-loud 纪律）；`load_dynamic_capabilities(path)` 从外部目录装载
  YAML 描述符——**纯数据**，`CapabilityDescriptor.model_validate`
  装载，无任何代码执行路径；单文件失败 fail-closed（跳过 + 记录
  violation，绝不部分装载脏描述符）。
- 挂钩只扩 capability 事实面，不触 tool/algorithm 执行面（capability
  → algorithm → tool 解析仍归 AlgorithmResolver）；运行期可装载的
  只有"声明"（planned 状态），没有任何可执行载荷——这是防代码注入
  的结构性保证，而非运行时过滤。

### D6: 治理、可观测与回滚

- 引擎全程产出 `InductionReport`（准入裁决/拒绝原因码/参数清单/
  编译违规/沙盒结果），随 induced 资产一起落盘（溯源审计面）。
- 技能 id 命名空间：`induced.<domain>.<slug>`；version 从 0.1.0 起
  （草案语义），晋升 core 时人工定为 1.x。
- 回滚：删除 `app/services/gis_skills/induction/`、
  capability_registry.py 挂钩块、`SKILL_PACKS` 的 "induced" 元素与
  对应测试即整体回退；无 schema/DB 迁移，core 库零触碰。

## 3. 与既有对象的对账（防重复声明）

| 对象 | 语义 | 本引擎关系 |
|------|------|-----------|
| ReplayTrace | 一次 turn 的可重放打包单元 | 语料输入（只读） |
| GoalSatisfactionReport | 任务级满意度裁决 | 准入门的事实面（只读） |
| SkillContract / SkillProcedure | 审定技能资产与过程 IR | 唯一产物形态（D3） |
| replay_procedure | 过程重放对账 | 沙盒自重放复用（D4） |
| SkillLibrary / loader | core 资产 fail-loud 单例 | 不触碰；induced 走独立面 |
| CapabilityRegistry | 能力词汇事实源 | 经 D5 挂钩受控扩展 |
| ExecutionGraph / PlanGraph | 运行期执行 | 不新造；步骤只声明意图 |
| trace_store JSONL | 证据链评测面 | 不消费、不修改 |

## 4. 后果

- 正面：高价值复合流程可自动沉淀为可校验、可重放、可选择的技能
  资产；错误模式被 D1/D4 双门挡在库外；技能增长从"工程师手工"变为
  "轨迹驱动 + 人工晋升"，无代码发布即可扩充能力面。
- 代价/风险：归纳质量依赖轨迹参数消毒形态（digest-only 降级轨迹
  不可归纳——诚实代价）；诱导出"过度具体"技能的风险由参数泛化的
  typed 约束与变体重放对冲；induced pack 扩大 resolver 候选面，由
  packs 过滤默认不启用对冲。
- 测试钉死：`tests/unit/test_skill_induction.py`（6 步轨迹归纳、
  换参重放复现、异常轨迹免疫、去毒注入拦截、动态挂钩纪律）。
