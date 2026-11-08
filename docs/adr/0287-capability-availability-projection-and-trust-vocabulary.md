# ADR-0287：跨层能力可用性只读投影与可信状态词表

- 状态：Accepted（本地验收通过；未等待线上 CI）
- 日期：2026-11-07
- 方向：G06-CAPABILITY-ECOSYSTEM
- 基线：`origin/master = 585812ee4e2d75612f2b124e6fe6587af3deca5c`
- 关联：ADR-0204（capability dispatch bind 证据）、ADR-0216（扩展平台唯一权威）、
  ADR-0237（ToolDescriptor 扩展字段）、ADR-0282（typed envelope）、
  ADR-0284（Pi v1.5 host 兼容）、ADR-0285（情境交互）

## 背景

master 已有完整的声明面（`CapabilityRegistry` / `AlgorithmRegistry`）、
执行真相（`ToolRegistry`）、只读目录（`ExecutionCatalog` /
`CompiledRuntimeManifest`）与八消费面对账（`tool_capability_conformance`）。
但**「声明/历史通过」与「当前环境可执行」混在同一个 `status="native"` 词里**：

- 213 个工具 8 面 `overall=pass`，184 个 `warning`；两者在目录里都叫 native；
- `planned` 能力、缺 provider 的外部工具、停用扩展没有统一状态词；
- 检索面按词法 + 离线语义打分，不感知可用性，高相似度不可用工具仍可激活；
- 秒/米、经纬度/投影的量纲混用散落在各工具自律（Issue #2005 即其后果）；
- 压缩后的结果摘要可能只剩「有 N 区」（Issue #2006）。

## 决策

### D1 — 新增只读投影，不新增第二个注册中心

新增 `app/lib/gis/capability_availability.py`，把既有权威源**编译一次**：
`collect_live_facts()` + `audit_registry_conformance(facts)`（8 面裁决）+
`ToolRegistry.descriptors()` + `ExecutionCatalog.generation_fingerprint` +
`ToolRegistry.registry_fingerprint()` + 扩展 `status_report()` 摘要。
快照是 frozen dataclass，**不写回**任何权威源。

禁止：平行 ToolRegistry / AlgorithmRegistry / ExecutionCatalog / 第二
fingerprint 体系 / 第二认证体系 / 第二 Planner / 第二渲染器。

### D2 — 可信状态词表与「历史通过 ≠ 当前可执行」

封闭词表：`native_verified` / `available_unverified` / `declared_only` /
`temporarily_unavailable` / `forbidden`。`native_verified` 必须**同时**满足：
真实注册、descriptor 可执行、8 面 `overall=pass`、registration/dispatch/result
三面在场、无 provider 依赖缺席。任一不满足即降级并给显式
`unverified_reasons`。`not_evaluated` 是**理由**不是状态：证据缺席时降级到
对应状态并显式写出，绝不猜。

实测快照：213 native_verified / 186 available_unverified / 18 declared_only
（417 条）。新落地的两个原语因未挂算法链，诚实落在 `available_unverified`
（理由 `planner:binding_unbacked`），**不刷成 verified**。

### D3 — 单一理由码

`reason_codes` 直接取 `FaceVerdict.reason`，回退语义钉在既有
`FALLBACK_CODE_XWALK` 与 `capability_conformance.CODE_*`。
禁止另写一份稍有不同的校验逻辑或空挂码位（`tool_capability_conformance.py:120-126`
的既有纪律）。版本门 `schema_version = "capability-availability/v1"`，
字段 additive-only，旧消费者忽略新字段。

### D4 — 权限隔离在查询面 fail-closed 且脱敏

`query_availability(permissions=None)` = 内部诊断视图；**显式**传入集合
（含空集）才启用隔离；空集 = 全部降级 `forbidden` 并清空能力细节字段。
跨 owner / 撤权 / 插件停用都不泄露 descriptor 细节。

### D5 — 科学预检三态与量纲硬门

`preflight_method()` 收敛既有五值+deferred 到 `eligible / ineligible /
not_evaluated`；秒与米混用 → `UNIT_DIMENSION_CONFLICT`；经纬度下要求线性
距离 → `GEOGRAPHIC_LINEAR_DISTANCE` + `transform_hint`。`fallback_class`
逐字取自 `AlgorithmDescriptor.fallback_semantics`，proxy 绝不冒充 equivalent。
**不改既有判定引擎**，只做只读投影。

### D6 — Pi 面走稳定只读 adapter

新增 `app/services/chat/pi_availability_adapter.py`，消费快照产出有界
候选（默认 8，硬上限 64）。**不修改** `pi_native_surface.py` /
`agent_pi_bridge.py` / `pi_rpc_client.py` / Pi Extension 宿主生命周期
（G01 所有权）。服务端用 `validate_pi_call()` 严格拒绝 hallucinated 工具名、
参数多余字段与宽松 pi-schema coercion。

### D7 — 两项真实原生原语

审计确认 `network_od_matrix` 与插值 LOOCV 已有生产实现（**必须避让**），
改做真实缺口：`conditional_quantile_zonal_stats`（面+栅格）、
`network_service_coverage_consistency`（线网+点）。都真实 `@tool` 注册、
`apply_contract` 校验、`registry.dispatch` 可达、输出 `data/artifact/science/budget`。
算法注册表计数保持 253（`test_g02_science_oracles.py:575` 硬断言），
新原语**不新增 AlgorithmDescriptor**，参数契约经既有域包
`iter_contract_packs()` 汇入唯一 `ParameterContractRegistry`。

### D8 — 漂移处理用既有生成器

新增工具改变 `registry_fingerprint` 与 catalog generation，导致
`docs/catalog/execution-catalog/{summary.md,tools.md,manifest.json}` 漂移。
用**既有** `python -m app.lib.gis.execution_catalog_docs` 重生成，
`--check` 必须通过；不手工编辑产物、不另写生成器。

### D9 — 不抢独立 Issue

Issue #2005（`families.py` 聚合口径）与 #2006（`slim_tool_result` 压缩器）
作为科学可信回归输入，纳入本线**只读对拍与诊断**（`parity_report()`），
不修改其实现，避免与在途独立修复撞车。

## 后果

**正面**：Agent 只调用真实可用工具；不可用工具不因语义相似度激活；
量纲冲突在预检与 dispatch 双层 typed 拒绝；摘要失真可诊断；
扩展停用/权限收缩 fail-closed 且可审计。

**成本**：`compile_availability_snapshot()` 每次做全 registry 8 面审计
（397+ 工具，量级 ~10s 内含 catalog 编译）；生产使用应缓存同代快照，
按 `registry_generation` 失效，不每轮重编译。

**未做（显式边界）**：
- 不新建 lease 服务（扩展平台原生 `missing`），用 `version_pins` +
  停用后 fail-closed 覆盖「执行中工具不被悄悄替换」；
- 快照是编译期只读投影，不做实时 provider ping；慢 provider 走
  `budget.deadline_s` 有界闸返回 `not_evaluated`；
- 演示数据是本地 fixture，非真实业务数据；外部数据缺席标 `not_evaluated`；
- 不等待线上 CI（Issue #2007 有历史生成物债务），本地验收为准。

## 回滚

从 `app/tools/__init__.py:_TOOL_MODULES` 移除注册行 + 关闭投影消费即可
恢复旧目录；无 schema 破坏、无 dispatch/权限/审计/取消语义变化。
