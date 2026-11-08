# G06 — 决策日志（输入事实 → 决定 → 依据 → 后果）

## D1 回退到原生 /goal 循环，不捏造技能

- **事实**：`.claude/skills/` 与 `.agents/skills/` 无 `goal-loop` / `planning-with-files`。
- **决定**：原生 /goal + 自管理 Oracle 循环，账本落 `planning/G06-CAPABILITY-ECOSYSTEM/`。
- **依据**：契约「不得捏造技能已安装」。
- **后果**：不调用 `loop-me` / `autoplan` 等冒充；循环由主 Agent 显式记录。

## D2 不新增 AlgorithmDescriptor

- **事实**：`tests/unit/gis/test_g02_science_oracles.py:575` 硬断言
  `contracts == get_algorithm_registry().count == 253`。
- **决定**：新域包 `capability_ecosystem.py` 的 `ALGORITHMS = []`，只导出
  `PARAMETER_CONTRACTS` 经既有 `iter_contract_packs()` 汇入唯一注册表。
- **依据**：CAP-O14 向后兼容；新增算法会破坏既有 method_use_contract 投影计数。
- **后果**：algorithm count 实测仍 253；两个新原语是 ToolRegistry 真实能力，
  科学元数据在工具结果 `science` 封套 + `docs/dev/GIS_TOOL_AUTHORING_CONTRACT.md`。

## D3 新原语保留 `available_unverified`，不刷成 `native_verified`

- **事实**：两个新工具 8 面对账 `overall=warning`：
  `planner:binding_unbacked`（未挂算法链）、`science:no_parameter_contract`
  （工具级契约未被算法 `parameter_contract_ref` 引用）、
  `pi_native:not_evaluated`（长尾工具非 7 前门）。
- **决定**：按 CAP-O02 硬约束降级到 `available_unverified`，并显式写
  `unverified_reasons`；不伪造 verified。
- **依据**：「声明/历史通过 ≠ 当前环境可执行」「缺依赖 = 不可用或 not_evaluated，
  不能猜」。
- **后果**：快照实测 213 native_verified / 186 available_unverified /
  18 declared_only（417 条）。这本身就是 CAP-O02 的最好证据。
- **后续**：把新工具挂到 AlgorithmDescriptor 算法链可作为独立后续项，
  但需同步更新 253 计数与 method_use_contract 生成物，不在本 PR 范围。

## D4 选这两个原语，避让已有实现

- **事实**：`network_od_matrix`（`network_tools.py:485,503,531` →
  `engine.solve_od_matrix`）与插值 LOOCV（`advanced_spatial.py:136,189-205`
  + `uncertainty.py:159`）已有真实生产实现。
- **决定**：改做审计确认的两个真实缺口：
  条件/多分位数分区栅格统计（`raster_ops.py:234-254` stat 白名单受限）、
  线网服务覆盖一致性校验（全 `app/tools/` 无 consistency 注册点）。
- **依据**：「已有同等实现则替换候选」；不重复造轮子。
- **后果**：两项覆盖不同 geometry/数据形态（面+栅格 vs 线网+点）。

## D5 参数契约 `quantiles` 用受限字符串而非任意 JSON

- **事实**：`ParameterSpec.type` 只有 number/integer/string/boolean/enum；
  `_check_type` 对非 string 直接报 issue。
- **决定**：契约声明为逗号分隔 string，工具侧解析回浮点并逐项做 (0,1) 科学校验。
- **依据**：`apply_contract` 是严格入口，不能让任意 list 穿透；同时保留
  真实分位数计算。
- **后果**：`where` 用受限正则解析（`value <op> <number>`，and 连接），
  **绝不 eval 任意代码**。

## D6 唯一一次改测试：修 fixture 数据，不动断言

- **事实**：`test_cap_o10_*` 的最小 pack fixture 写
  `namespace="cap_o10"` + `name="cap_o10"` + `id="cap_o10.pack"`，
  `host.discover()` 报 `MANIFEST_INVALID`（record=None）。
  真实约束在 `app/extensions_platform/manifest.py:466`：
  `id == f"{namespace}.{name}"`。
- **决定**：把 fixture 的 `name` 改为 `"pack"`（使 `cap_o10.pack` 合法），
  断言（discover→certify→activate→deactivate 全链）一字未改。
- **依据**：这是测试脚手架数据不符合既有 schema，不是放宽验收；
  真实扩展平台行为不能被绕过。
- **后果**：O10 真实通过（1 passed / 10.46s），覆盖停用时 namespace 不可用
  与 catalog generation 变化。

## D7 `query_availability` 的 permissions 语义

- **事实**：契约初稿写 `permissions=frozenset()` 默认值；但「未指定上下文」
  与「显式零权限」需要不同语义（后者必须 fail-closed）。
- **决定**：`permissions=None` = 内部诊断视图（无隔离）；显式集合（含空集）
  = 启用隔离；空集 = 全部 `forbidden` 且脱敏。
- **依据**：CAP-O04「跨 owner、撤权、插件停用均 fail-closed」，
  同时内部诊断/CLI 必须能看到全量矩阵。
- **后果**：CAP-O04 与 CAP-O12 两个用例都通过；接口契约文档已同步说明。

## D8 生成物漂移用既有生成器，且回退非必需生成物

- **事实**：新增 2 工具 + 2 契约造成
  `docs/catalog/execution-catalog/{summary.md,tools.md,manifest.json}` 漂移；
  新增 2 契约造成 `docs/science/ALGORITHM_CATALOG.md` 的
  「127 参数契约 → 129」并触发 `test_foundation_v2_infra.py` 失败。
- **决定**：用既有 `execution_catalog_docs` / `gen_science_catalog` 重生成；
  对 `CAPABILITY_CATALOG.md`（无 parity 测试依赖、且带与 G06 无关的
  环境噪声 `model 10→0` / `orphan 66→75`）**回退到 master**，不把噪声带进 PR。
- **依据**：「仅目标文件发生实质变化」；生成物不得手工编辑。
- **后果**：`--check` up to date；gis 全套 798 passed / 3 skipped。

## D9 基线历史债务不掩盖、不等待、不修改 Actions

- **事实**：`scripts/check_generated_staleness.py` 报 8 个 `docs/quality/*`
  与 `docs/science/BENCHMARK_MANIFEST.md` stale；
  这些输入目录包含 `app/api/routes` / `app/lib/geo_analysis` 等本线**未改动**的文件，
  且 `content_fingerprint` 与账本一致（仅 input_fingerprint 变）。
  Issue #2007 已记录同类历史债务（#1993 合入后）。
- **决定**：如实记录为「基线已坏 / 环境限制」，不强行 `--update` 掩盖、
  不修改 Actions、不等待线上 CI、不宣称 CI 通过。
- **依据**：契约「线上 CI 可能有历史债务，记录状态但不得作为等待条件」。
- **后果**：本地验收以本方向改动范围内的测试为准；PR body 显式列出该风险。

## D10 ADR 编号 0287

- **事实**：master ADR 最大 0285；并行 G01 分支已占用 0286
  （`0286-pi-native-turn-phase-and-idempotent-observability.md`）。
- **决定**：用 0287 避免编号撞车。
- **依据**：G01 所有权避让。

## D11 不抢 Issue #2005 / #2006

- **事实**：#2005（`families.py` 聚合秒数）、#2006（`slim_tool_result` 丢数值）
  是独立 open issue，且可能有其他 agent 处理。
- **决定**：只做本线**只读对拍与诊断**（`parity_report` + 新原语结果封套自身
  保证摘要含数值或可分页 ref），不改这两个实现。
- **依据**：契约「优先不抢修这些独立 issues」。
- **后果**：CAP-O06/O14 回归保护成立，但不宣称这两个 issue 已修复。
