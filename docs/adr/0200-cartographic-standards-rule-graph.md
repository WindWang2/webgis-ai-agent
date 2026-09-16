# ADR-0200: 制图规范 Rule Graph 与确定性 QA Pack

- 状态: Accepted（本分支）
- 日期: 2026-09-17
- 关联: ADR-0078（cartographic-semantic checks）、ADR-0118（user-wins 抑制）、ADR-0152（symbology 决策）、ADR-0156（component composer）、ADR-0186（visual self-healing）、`.agent-work/contextual-cartographic-harness-v6/04-unified-findings.md`（词表碎片化普查）、`review/AGENT-05-REVIEW.md`（制图知识散落清单）

## 背景

「为什么必须这样画」的制图义务散落在多处：`required_components_for` 的硬编码必配表、semantic_checks 的 16+ 内联规则、symbology/context_matrix 的裁决常量、composition validation / render diagnostics 的错误码词表。义务无法按 **map purpose × audience × medium** 组合声明，无法版本化，也无法解释「这条义务从哪来、由谁测量、修复走哪条既有通道」。

## 决策

D1 **规则是数据，测量是引擎。** `CartographicRule`（`app/lib/cartography/standards/rule.py`）以有界 `kind` 词表声明义务；每个 kind 的测量**委托**唯一既有实现（`required_components_for` / `context_matrix.evaluate_cell`+palettes 原语 / `thematic_spec` / source profile 统计）。禁止复制分类/布局/CVD 数学。

D2 **无第二裁决。** StandardsQAReport 是与 CartographyReport **并行消费**的有界投影（violations/obligations/pi_card/pack fingerprint），绝不输出替代 SEMANTIC_VALID 的顶层裁决。

D3 **修复只路由。** fix_hint 三路由：`quality_loop`（操作 ∈ AUTO_SAFE 白名单，跨模块行为契约测试锁定）/ `component_autofill`（required-components 补全域）/ `advisory`。QA 零 mutation。

D4 **版本化 + 旧图兼容。** StandardsPack frozen + semver + 内容寻址 fingerprint + fail-closed registry。显式 profile → strict（error 可阻断 gate）；推断 profile → error 上限降 warning（旧图不新增阻断）；`enabled=False` → disabled 空报告。证据缺失恒 `not_evaluated`。

D5 **图构建期 fail-closed。** 未知依赖、环、require+conflict 矛盾 → 构建即错。冲突双规则同时违反而非矛盾声明时发 `RULE_CONFLICT` 双披露；builtin pack v1 只声明依赖边（当前义务无真实冲突对，诚实不造）。

D6 **上下文复用 credential-safe 投影**（`cartographic_projection`），O(1) w.r.t. features；evidence refs 有界词表（`mapspec://` `profile://` `engine://`）。

D7 **竞争文件零改动**（#1356 在飞面：mapspec_schema/semantic_checks/render_diagnostics/契约 JSON）；standards 输入走显式参数。

D8 **catalog = 生成投影 + drift 测试**（`standards/catalog.py` → `docs/cartography/standards-catalog.{md,json}`）。

## 义务覆盖（core v1.0.0，12 kinds）

required_components / source_disclosure / legend_present / legend_unit_disclosure / count_vs_rate / cvd_safe_palette / print_legible_palette / classification_declared / label_density_declared / time_disclosure / uncertainty_disclosure / thematic_profile_declared —— 覆盖任务面的 map type 声明、count-vs-rate/density、分类方法、palette/CVD/print、图例 title+unit、标注预算、scale/north/source/time/uncertainty。

## 后果与边界

- 新义务 = 新 pack version（registry 注册，fingerprint 变化可审计）；旧 pack 不可变。
- CVD/print 判定与 resolve_symbology/context_matrix 同源常量；矩阵口径变化自动传导。
- 已知边界：count-vs-rate 是字段名语义启发（诚实标注「疑似」）；raw-colors CVD 采样与注册 ramp 采样在非中点采样时可能有微小 ΔE 差异（fallback 只在缺 palette 名时启用）。
- Pi card 为报告上有界投影，未接 hotpath/claim 热区（#1335/#1336 占用）。
