# DECISIONS — 关键架构决策（保守/兼容/可验证优先）

- **D1 规则是数据，测量是引擎。** CartographicRule 用有界 `check` kind 词表声明义务；`evaluate.py` 把每个 kind 委托到唯一既有实现（required_components_for / context_matrix / 语义检查证据 / profile 字段统计）。禁止复制分类/布局/CVD 算法（Oracle）。
- **D2 无第二裁决。** UBIQUITOUS_LANGUAGE 禁平行 verdict：StandardsQAReport 是有界投影（violations/obligations/pack/profile/fingerprint），显式声明「与 CartographyReport 并行消费，不替代」；绝不输出 SEMANTIC_VALID 类顶层裁决。
- **D3 修复只路由不改权。** fix_hint 三路由：`quality_loop`（操作 ∈ AUTO_SAFE 白名单）/`component_autofill`（required_components_for 域）/`advisory`。QA 本身零 mutation。跨模块契约测试锁定「路由目标 ∈ 既有词表」。
- **D4 版本化 + 旧图兼容。** StandardsPack frozen + semver + fingerprint；**strictness 只由显式 purpose 解锁**（audience/medium 单独声明不 strict，评审 R1-P2 修正）；显式 profile → 义务 error 级（opt-in 严格）；推断 profile → 同义务降 warning（legacy_map 证据）；`enabled=False` → status=disabled 空报告（feature-off 回归零变化）。证据缺失恒 not_evaluated，不伪造。
- **D5 图在构建期 fail-closed。** 未知依赖引用、环、悬空冲突 → StandardsPack 构建即抛 StandardsPackError（确定性错误信息），不在评估期静默降级。冲突规则同时适用 → RULE_CONFLICT violation 双披露（severity 取高者），永不静默丢弃。
- **D6 上下文复用 credential-safe 投影。** StandardsContext = `quality_loop.cartographic_projection(mapspec)` + source profiles + composition flags；O(1) w.r.t. features；evidence_refs 用 `mapspec://layers[id]/…`、`profile://sources[id]/fields[f]`、`engine://semantic_checks/RULE`、`engine://context_matrix/cell/palette/context` 有界词表。
- **D7 竞争文件零改动。** mapspec_schema/semantic_checks/render_diagnostics/契约 JSON/lifecycle_engine/coordinator 一律不改（#1356 竞争面）；standards 输入走显式参数 + additive 字段直读（extra=allow 保证 round-trip）。
- **D8 catalog = 生成投影 + drift 测试。** 遵循 catalog_docs doctrine：`python -m app.lib.cartography.standards.catalog [--check]`；生成 `docs/cartography/standards-catalog.md` + JSON；测试锁漂移。
- **D9 ADR-0200。** 0197–0199 已被在飞分支占用。
- **D10 Pi card = 有界投影字段。** QA 报告带 `pi_card`（严格节点/字节上限，超限 sha256 披露，复用 quality_loop 投影纪律），不接 harness 热区（hotpath/claim 由 #1335/#1336 占用）。
