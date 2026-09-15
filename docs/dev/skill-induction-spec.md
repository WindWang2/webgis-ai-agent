# Skill Induction V1 技术规范（ADR-0191 配套）

- 分支: agent/07-trajectory-to-skill-induction-engine
- 基线: origin/master 3eb2cc6a
- 日期: 2026-09-15
- 结论速览: 在 `app/services/gis_skills/induction/` 新建五模块归纳引擎，
  以 ReplayTrace 为唯一语料面，经 D1 合取准入门 → 三步法（参数泛化 /
  数据流提取 / 契约封装）→ D4 三重验证门禁（静态检查 + 去毒 + 沙盒
  重放），产出 ADR-0182 `SkillContract`（induced pack）+ 参数 Pydantic
  Schema + 溯源报告；`app/lib/gis/capability_registry.py` 增加数据-only
  动态扩展挂钩。全链确定性、零 LLM、零网络、零 DB。

## 1. 模块布局与公开签名

```
app/services/gis_skills/induction/
├── __init__.py               # SkillInductionEngine 门面 + 公开词表再导出
├── trace_analyzer.py         # D1 准入门 + 调用链/变异事件提取
├── parameter_generalizer.py  # 常量 → typed 参数槽位（Pydantic Schema）
├── procedure_compiler.py     # 泛化产物 → ADR-0182 SkillContract + YAML
├── sandbox_validator.py      # 静态检查 + 去毒 + 离线 Mock 重放验证
└── dynamic_provider.py       # induced 技能资产库（fail-closed 装载/隔离面）
```

| 签名 | 语义 |
|------|------|
| `trace_analyzer.analyze_trace(trace: ReplayTrace \| dict, *, satisfaction_threshold=0.95, capability_map=None) -> TraceAnalysis` | D1 合取门 + 步骤序列/证据/变异事件提取；拒绝时 `accepted=False` + `IND-*` 原因码 |
| `parameter_generalizer.generalize_parameters(analysis) -> ParameterGeneralization` | 常量模式识别 → `InducedParameter` 集 + `build_model()` 动态 Pydantic 模型 |
| `procedure_compiler.compile_skill(analysis, generalization, *, registry, ontology_ok, recipes_ok, artifacts_ok, preconditions_ok) -> CompiledSkill` | 产出 `SkillContract`（必过 `validate_contract` 零违规）+ YAML 文本 |
| `sandbox_validator.validate_compiled(compiled, analysis, *, registry, variant_scenarios) -> SandboxReport` | 三门验证：静态/去毒/重放（自重放 + 变体重放） |
| `dynamic_provider.InducedSkillStore(root)` | induced YAML 资产库：`save/load/list/quarantine`；装载即校验（契约 + 去毒） |
| `capability_registry.CapabilityRegistry.register_dynamic(desc)` / `load_dynamic_capabilities(path)` | 数据-only 动态能力挂钩（`induced.` 前缀纪律，重复 raise，坏文件 fail-closed 跳过） |
| `induction.SkillInductionEngine.induce(trace) -> InductionOutcome` | 端到端编排：准入 → 泛化 → 编译 → 沙盒 →（可选）入库 |

## 2. 核心数据模型（全部 pydantic BaseModel，确定性）

```
TraceAnalysis      accepted / rejection_codes[IND-*] / steps[InducedStep]
InducedStep        seq / tool_name / capability_id / kind(⊆STEP_KINDS) /
                   arguments / evidence_kinds[] / is_mutation
InducedParameter   name / type( float|int|str|bool ) / semantic_role
                   (coordinate|threshold|place_name|field|date|generic) /
                   constraints(ge_bal…)/ example / description
ParameterGeneralization  parameters[] / build_model() -> type[BaseModel]
CompiledSkill      contract: SkillContract / yaml_text / parameter_model /
                   provenance(session_id, turn_id, behavior_digest,
                   source_satisfaction)
SandboxReport      static_ok / detox_ok / self_replay_complete /
                   variant_results[VariantReplayResult] / accepted / reasons[]
InductionOutcome   status(induced|rejected) / analysis / compiled /
                   sandbox / report（全程可序列化，审计面）
```

## 3. 确定性词表

- 拒绝原因码 `IND_*`：`IND_NO_SATISFACTION_FACE` / `IND_SATISFACTION_BELOW_THRESHOLD`
  / `IND_VERDICT_NOT_SATISFIED` / `IND_TRACE_TRUNCATED` / `IND_TOOL_ERROR`
  / `IND_DIGEST_ONLY_ARGS` / `IND_TOO_FEW_STEPS` / `IND_EMPTY_STEPS`
  / `IND_TOO_MANY_STEPS`（与 `procedure_ir.MAX_STEPS=32` 对账，杜绝
  「准入后编译崩溃」）/ `IND_INCOMPATIBLE_TOPOLOGY` / `IND_MEMBER_REJECTED`
  / `IND_COMPILE_VIOLATIONS`。
- 参数语义角色（模式识别规则，全部确定性）：
  - `coordinate`：键名精确命中 `lon|lng|latitude|lat`（或剥尾随数字后
    命中）→ `float` + ge/le 硬边界；不做 x/y 等歧义键猜测；
  - `threshold`：键名含 `threshold|limit|alpha|sig|cutoff|p_value` →
    数值 + 观测值邻域约束（多观测取 [min, max] 张成带）；
  - `place_name`：字符串值且命中行政区后缀（省/市/区/县/镇）→ `str`
    + 长度 1..64；
  - `field` / `date` / `generic`：字段名样式 / ISO 日期样式 / 其余
    安全标量。
  - 敏感键（`token|key|secret|password|credential`）与多行/超长自由
    文本：**不泛化**（记入 `skipped`，不进 Schema）。
- 工具 → 过程 kind 映射（子串规则，首个命中）：`query|fetch|search|load|
  inspect|profile → inspect`；`clip|filter|project|buffer|reproject|prepare|
  extract → prepare`；`density|aggregate|join|statistic|regress|overlay|
  compute|exposure|index → analyze`；`decide|choose|select_method → decide`；
  `symbolize|style|render_map|symbology|label → design`；`validate|qc|check|
  verify → validate`；`export|product|compose|deliver|report → deliver`。
- 工具 → capability 投影：注入 `capability_map: Dict[str, str]` 优先；
  缺省恒等投影；`registry.has()` 为假 → 经动态挂钩登记
  `status="planned"` 的 `induced.cap.<slug>` 描述符（诚实语义，不虚构
  native 能力）。

## 4. 安全不变量（测试钉死）

1. induced 契约 `validate_contract()` 注入离线谓词必须零违规；
2. fallback 三触发器（missing_input / unsupported_geometry /
   insufficient_data）由编译器强制生成，全部带披露（禁止 silent
   fallback）；
3. 去毒命中 → 整体拒绝（`SBX_DETOX_BLOCKED`），不做静默改写放行；
4. 参数越界值在 `build_model()` 产物上必须 `ValidationError`；
5. 变体重放：合法新参数 → 拓扑指纹与**源轨迹**投影一致；非法参数 →
   Schema 层拒绝，执行器不启动；契约拓扑 ≠ 源拓扑 → `SBX_TOPOLOGY_DRIFT`；
6. `InducedSkillStore` 装载即校验，非法资产进 quarantine 不进索引；
   id 路径安全守卫（分隔符/`..`/超长拒绝）；
7. 动态能力挂钩：非 `induced.` 前缀 id、重复 id、非法 YAML 一律
   fail-loud / fail-closed，无代码执行路径；动态描述符禁止携带
   `purpose_template`（format 渲染面）与 native 状态；引擎/编译器
   只写私有注册表实例（进程级能力单例零污染）；
8. core 技能库（fail-loud 单例）在全部流程中零写入；
9. `compile_skill` 的 `registry` 为必传参（消除库函数隐式全局副作用）；
   动态登记失败（预算/纪律）并入编译违规 fail-closed 拒绝，不崩溃；
10. 引擎缺省携带内置变体探针集（带内绑定 / 数值越界 / 注入值），
    D4 第三门缺省即有牙齿；CLI 对单轨迹/单簇引擎异常隔离
    （如实入 `error_records`，不中止批处理）。

## 5. 测试矩阵（tests/unit/test_skill_induction.py）

| 组 | 用例 | 断言核心 |
|----|------|---------|
| Fixtures | 6 步水质暴露度评估轨迹（经真实 `build_trace` 构造，满意度 1.0） | `ReplayTrace.tool_calls` 提取层真实可用 |
| TestTraceAnalyzer | 高分轨迹准入；无满意度面/低分/含错调用/digest-only/truncated 拒绝 | 原因码逐一对账 |
| TestParameterGeneralizer | 坐标/阈值/地名/日期泛化；敏感键跳过；模型越界拒绝 | Pydantic ValidationError |
| TestProcedureCompiler | SkillContract 合法（离线谓词零违规）；6 步 kind 正确；三 fallback 在场；YAML round-trip | `validate_contract() == []` |
| TestSandboxValidator | 自重放 complete；变体重放拓扑一致；非法参数拦截；注入文本去毒拒绝 | `SandboxReport.accepted` |
| TestDynamicProvider | 动态挂钩前缀/重复/fail-closed；Store 隔离与 quarantine | registry 纪律 |
| TestInductionImmunity | 失败轨迹（error/低满意/truncated）端到端拒绝且不入库 | `status == "rejected"` |

## 6. 与既有面的接缝清单（file:line）

- `app/lib/harness/replay/schema.py:263` `build_trace` —— fixtures 走
  同一提取层（消毒/还原语义一致）；
- `app/lib/harness/replay/schema.py:325-331` verdict 打包 —— 满意度面
  读取路径 `trace.verdict["map_product"]["goal_satisfaction"]`；
- `app/services/gis_harness/skills/contract.py:52` `SKILL_PACKS` ——
  additive 扩展 `"induced"`；
- `app/services/gis_harness/skills/contract.py:164` `validate_contract`
  —— 静态门主校验；
- `app/services/gis_harness/skills/replay.py:74` `replay_procedure` ——
  沙盒自重放复用；
- `app/lib/gis/capability_registry.py:75` `register` —— 动态挂钩的
  fail-loud 基线纪律。
