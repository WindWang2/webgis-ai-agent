# AGENT-07 Review 备忘录 —— 轨迹到技能自合成引擎（ADR-0191）

- 分支: agent/07-trajectory-to-skill-induction-engine（worktree webgis-wt-agent-07）
- 基线: origin/master 3eb2cc6a
- 审查日期: 2026-09-15
- 审查方式: 独立审查代理全量 diff 精读 + 对抗性实验（只读，临时目录验证），
  开发者复核并逐项修复后回归。
- 门禁证据: `pytest tests/unit/test_skill_induction.py` 75 passed；
  技能域 + capability/trace 消费方存量套件 134 passed（零漂移）；
  `ruff check`（ induction 包 / 测试 / capability_registry / contract /
  CLI 脚本）All checks passed。

## 1. 交付物清单

| 产物 | 说明 |
|------|------|
| docs/adr/0191-trajectory-to-skill-induction.md | 架构决策 D1-D6 |
| docs/dev/skill-induction-spec.md | 技术规范（签名/词表/安全不变量/测试矩阵） |
| app/services/gis_skills/induction/（6 模块） | trace_analyzer / parameter_generalizer / procedure_compiler / sandbox_validator / dynamic_provider / 门面 |
| app/lib/gis/capability_registry.py | 动态外部扩展挂钩（D5，数据-only） |
| app/services/gis_harness/skills/contract.py | SKILL_PACKS additive 扩展 "induced" |
| scripts/skill_induction.py | 离线批处理 CLI（gitignore 白名单入库） |
| tests/unit/test_skill_induction.py | 75 例（准入/泛化/编译/沙盒/免疫/挂钩/合并/CLI/审查钉子） |

## 2. 安全审查结论（动态技能加载与防代码注入）

### 2.1 结构性保证（实测通过，为第一防线）

- **无代码执行路径**：全链仅 `yaml.safe_load`（dynamic_provider.py、
  capability_registry.py）+ Pydantic 数据模型；无 eval/exec/unsafe
  loader。即便去毒被绕过，也没有可执行载荷的落点——这是结构性防线，
  不是运行时过滤。
- **去毒过滤（D4）**：合成资产全部字符串面（契约 model_dump、参数
  dump、溯源）经 `scan_for_injection` 九类模式扫描（import/eval/exec/
  dunder/模板注入/shell/绝对路径×2/URL），命中即整体拒绝
  （`SBX_DETOX_BLOCKED`），不静默改写放行。实测：goal 文本投毒与
  **参数值投毒**两条端到端路径均被拒且 store 零写入。
- **动态挂钩纪律（D5）**：`induced.` 前缀强制、status 白名单
  （planned/unavailable，不得虚构 native）、重复 id fail-loud、
  128 能力/64 文件预算、坏文件 fail-closed（violation 返回调用方）、
  `purpose_template` 禁入动态面（封堵 `purpose_for()` 的
  format-string 渲染面）。实测：前缀/native/重复/预算/purpose_template
  五类全部按纪律拒绝。
- **核心库隔离（D3 红线）**：core loader 只 glob `core/*.yaml`；
  induced 走独立目录 + `InducedSkillStore`（fail-closed 装载、
  quarantine 隔离、id 路径安全守卫——`../`、`C:\`、`..`、超长全拒）；
  引擎/编译器只写**私有** CapabilityRegistry 实例；
  `compile_skill` 的 `registry` 为必传参（消除库函数隐式全局副作用）。
  实测：`get_skill_library().skill_count` 与进程级能力单例 dynamic_ids
  在全部流程前后不变（测试钉死）。
- **fail-closed 合取语义**：D1 准入 8 类原因码 + 编译违规
  （`IND_COMPILE_VIOLATIONS`，含动态登记失败转码）+ 沙盒三门原因码，
  任一非空即 `rejected` 且不入库；拒绝码全程无静默吞没。

### 2.2 审查发现与修复台账

| # | 级别 | 发现（审查代理实验证实） | 修复 |
|---|------|------------------------|------|
| 1 | P1 | 超长轨迹（>32 步）过 D1 后在编译期爆 pydantic ValidationError，异常逃逸出 `engine.induce()`（生产 agent 单 turn 超 32 次工具调用常见） | D1 增设上限门 `IND_TOO_MANY_STEPS`，与 `procedure_ir.MAX_STEPS=32` 对账；测试钉死 40 步轨迹带码拒绝、无崩溃 |
| 2 | P1 | `register_dynamic` 预算耗尽 raise 击穿引擎与 CLI（130 个未注册工具的轨迹令归纳崩溃、批处理中止） | 编译器捕获登记异常 → `capability_register:*` 编译违规 → `IND_COMPILE_VIOLATIONS` fail-closed 拒绝；CLI 逐轨迹/逐簇 try/except，异常如实入 `error_records` 不中止；测试各一 |
| 3 | P2 | D1 完整性门弱于 ADR：未查 `error_msg` 与 outcome 失败码（status 漏标的错误调用可准入） | 补 `error_msg` 在场检查 + TurnEvidence outcome 失败码检查（均归 `IND_TOOL_ERROR`）；测试各一 |
| 4 | P2 | 自重放 facts 取自被测契约自身 → 恒绿灯；变体指纹与同一契约比 → 恒相等；引擎缺省无变体场景 → 第三门空转 | 自重放 facts/指纹全部改为**源轨迹投影**（`_facts_from_source` / `source_topology_fingerprint`），契约拓扑 ≠ 源拓扑 → `SBX_TOPOLOGY_DRIFT`；引擎缺省内置三探针（带内绑定 / 数值越界 / 注入值）；测试钉死"自重放门可失败"与"拓扑漂移可检测"两个反例 |
| 5 | P2 | `compile_skill(registry=None)` 缺省写进程级能力单例（隐式全局副作用） | `registry` 改必传参；引擎/CLI 全部显式私有实例；测试钉死单例零污染 |
| 6 | P2 | 动态描述符可携带 `purpose_template`（`purpose_for()` 的 format-string 属性穿越注入面） | `register_dynamic` 与 `load_dynamic_capabilities` 双路径禁入（raise/violation）；测试钉死 |
| 7 | P3 | detox 词表缺口（`data:`/`javascript:` URL、非列举 unix 路径、YAML `!!python/` 标签）与 dict 键不扫描 | 记录为已知剩余风险（见 §4）；键逐一核实无数据可控键进入持久化面。接受现状：纵深防御第二层（无执行路径）使该缺口不构成可达攻击面 |
| 8 | P3 | 死代码/风格项（`or skill_id` 恒真、内联 import yaml、跨模块私有 `_iter_strings`、重复投影） | 全部清理；`iter_strings` 公开导出 |

## 3. 规格一致性

- ADR-0191 D1-D6 与实现逐条对账通过；spec §3 词表两处漂移（坐标键
  `x|y` 猜测、threshold 键清单）已按实现修正 spec（实现更保守）。
- 已知限制（有意为之，非缺陷）：
  1. digest-only 降级参数的轨迹不可归纳（D1 `IND_DIGEST_ONLY_ARGS`）——
     typed 泛化需要真实值，诚实代价；
  2. 同目标文本 → 同 skill id → `save()` 覆盖旧草案（草案语义：同
     槽位迭代；版本化晋升 core 时人工定版）；
  3. induced pack 默认不参与 resolver 选择（`packs` 过滤显式开启），
     晋升 core 走人工 review。

## 4. 剩余风险与后续建议

1. **detox 词表扩展**（P3，已记录）：`data:`/`javascript:` scheme、
   通用 unix 绝对路径、YAML tag 模式建议在下一迭代并入
   `DETOX_PATTERNS`（当前不可达：无执行路径 + data-only）。
2. **动态装载文件体积上限**（P3）：`load_dynamic_capabilities` 只限
   文件数不限体积；建议补单文件字节上限。
3. **多轨迹满意度面**：`induce_many` 取成员最保守满意度；跨轨迹
   satisfaction 聚合语义（如中位数）可在治理面成熟后再收紧。
4. **ADR-0182 侧对账**：`skill_id` 字段（ReplayTrace 预留 #1278）的
   回填（合成产物 → 源轨迹标注）属生产接线，建议随 #1278 线落地。

## 5. 结论

**可合入。** 两条 P1 崩溃路径与全部 P2 已修复并有回归钉子；红线主体
（防代码注入的结构性 data-only、core 库零触碰、动态挂钩纪律、
fail-closed 合取语义、确定性有界产物）经独立对抗审查实测通过。
门禁：新增 75 例 + 存量 134 例全绿，ruff 零告警。
