# G06 — 现有能力逐项审计证据（present / partial / planned / missing）

判定口径：**以注册代码、执行 body、返回结构为准，不采信文档措辞**。
基线 `origin/master = 585812ee4e2d75612f2b124e6fe6587af3deca5c`。
所有路径相对 worktree 根；行号为该基线实测。

## 1. 权威元数据层

| 能力 | 状态 | 证据 |
|---|---|---|
| `CapabilityDescriptor` / `CapabilityRegistry` | present | `app/lib/gis/capability_registry.py:29`（字段 id/name/input_artifact_types/output_artifact_types/geometry_requirements/required_fields/status/offline_capable/incompatible_with/depends_on/alternative_to）；`:83` 注册/查询；`:255` 单例 |
| `AlgorithmDescriptor` | present | `app/lib/gis/algorithm_registry.py:242`（含 `scientific_preconditions` :284、`uncertainty_outputs` :285、`fallback_semantics` :292、`backend_variants` :290）；`FallbackSemanticsClass` :39 |
| `AlgorithmResolver` | present / partial | `app/lib/gis/algorithm_resolver.py:27,39`；结果用 `not_evaluated` 而非统一三态 |
| `ExecutionCatalog` | present | `app/lib/gis/execution_catalog.py:530`（`generation_fingerprint`）、`:630` 编译、`:218` `build_certification_index_from_host` |
| 八消费面对账 | present | `app/lib/gis/tool_capability_conformance.py:83-95`（8 面常量）、`:127-138` `FALLBACK_CODE_XWALK`、`:635/683/738` 审计入口、`:112-118` 有界预算 |
| `capability_binding_governance` | present | `app/lib/gis/capability_binding_governance.py:56,80` |
| `method_use_contract` | present | `app/lib/gis/method_use_contract.py:47,114,213` |
| `parameter_contracts` | present | `app/lib/gis/parameter_contracts.py:28` `PARAM_UNIT_VOCABULARY`（含 seconds）、`:45` `ParameterSpec`、`:578` `apply_contract`、`:498` 注册表 |
| `science_final_gate` | present | `app/lib/gis/science_final_gate.py:48` `NON_EQUIVALENT_SEMANTICS`、`:135` gate |
| `scientific_preconditions` | present / partial | `app/lib/gis/scientific_preconditions.py:27,32,53,414,420`；五值+deferred，无统一三态 |
| `runtime_manifest` | present | `app/lib/gis/runtime_manifest.py:121,171,294`（fingerprint / `is_stale_plan`） |
| `execution_catalog_staleness` | present | `app/lib/gis/execution_catalog_staleness.py:174` |
| `SpatialSemanticContract` | present | `app/services/gis_harness/spatial_semantics/contract.py:79`；`vocabulary.py:82` 六态；`validate.py:57` |
| `crs_safety` / `measurement` / `uncertainty` / `scientific_errors` | present | `crs_safety.py:36`、`measurement.py`（UnitDimension）、`uncertainty.py:159`（ValidationMetrics loocv）、`scientific_errors.py:44`（18 子类） |
| **可信状态词表 / CapabilityAvailabilitySnapshot** | **missing** | 全 `app/` Grep `native_verified\|available_unverified\|declared_only\|temporarily_unavailable\|CapabilityAvailabilitySnapshot` → 0 命中；现存仅 `CapabilityStatus=native/planned/unavailable` (`capability_registry.py:16`) 与 `ToolStatus` (`app/tools/descriptor.py:34`) |
| **统一三态预检** | **missing** | 语义散在 scientific_preconditions 五值、spatial_semantics 六态、`interpolation_compare.py:96 eligible: bool` |
| **false-advertised** | 无 | 全层贯彻 `not_evaluated` / honest failure / fail-closed；`backend_selection.py:337` 显式拒绝虚构 GPU/分布式 |

## 2. 工具执行链

| 能力 | 状态 | 证据 |
|---|---|---|
| `ToolRegistry` 唯一执行真相 | present | `app/tools/registry.py:547 register`、`:1338 _dispatch_impl`（planned/tier3/policy 单点闸）、`:1125 registry_fingerprint`、`:1093 descriptors` |
| `ToolDescriptor` | present | `app/tools/descriptor.py:155`（status/tier/capabilities/algorithms/produced_refs/required_fields/unit_semantics/crs_semantics/...）、`:232 tool_id`、`:248 executable` |
| 注册面聚合 | present | `app/tools/__init__.py:11-150` `_TOOL_MODULES`（基线 397 工具） |
| `ToolDispatchService` | present | `app/services/tool_dispatch_service.py:108,235,277,870,875` |
| 检索（词法 + 离线语义 + V6 hybrid） | present | `app/services/chat/tool_retrieval.py:137,352`；`tool_semantic_retrieval.py:36,154`；`semantic_retrieval.py:9-31`；`tool_surface_v3.py:79-83`（K 上界 64） |
| 检索**可用性感知** | **missing** | 打分不含 availability_status / 输入类型 / 量纲约束 |
| Pi 原生工具面 | present | `app/services/chat/pi_native_surface.py:23`（7 natives）、`:33`（`webgis_execute`）、`:76` `resolve_pi_tool_call`；`CONTEXT.md:461-476` 裁决「代理不是 Tool」 |

## 3. 扩展平台与认证

| 能力 | 状态 | 证据 |
|---|---|---|
| 包级认证 | present | `app/extensions_platform/certification.py:25` `certify_extension` |
| 能力级认证（6 stage + HMAC） | present | `app/extensions_platform/capability_certification.py:365,722,785,875` |
| 签名 HMAC-SHA256 / Ed25519 | present | `app/extensions_platform/signing.py:48-63,110,141,278` |
| `ExtensionState` / `HostPolicy` | present | `app/extensions_platform/host.py:89,133,192` |
| 生命周期公开 API | present | `host.py:232 discover` / `:634 activate` / `:1588 deactivate` / `:1714 reload` / `:1842 upgrade` / `:2074 health` / `:2102 status_report` / `:2134 get_record` |
| `version_pins` | present | `host.py:179,1917`；`settings_bridge.py:99` |
| `pack_catalog` / capability certification 索引 | present | `app/extensions_platform/pack_catalog.py:27,111`；`execution_catalog.py:218` |
| manifest id 约束 | present | `app/extensions_platform/manifest.py:466` `id == f"{namespace}.{name}"` |
| **lease 语义** | **missing** | 全 `app/extensions_platform/` 无租约（仅 connection release） |
| **自动信任未签名下载插件** | forbidden | broker 默认 deny；本线不引入 |

## 4. ModelOps / 计算模型

| 能力 | 状态 | 证据 |
|---|---|---|
| `GeoModelDescriptor` | present | `app/lib/modelops/descriptor.py:236`（checksum/provenance/normalization/output_transform）；`:114` `DeviceRequirements.allow_cpu_fallback` |
| `ProviderCapabilities` | present | `app/lib/modelops/capabilities.py:164`；`:112-136` PROVIDER_TYPES/WIRED_PROVIDER_TYPES |
| `CompatibilityReport` | present | `app/lib/modelops/compatibility.py:83,115`（`qualify` 纯函数） |
| `ResourceEstimate` / `DevicePlan` / `batch_for_budget` | present | `app/lib/modelops/resources.py:14,34,60` |
| `ModelLineageStore` (provenance) | present | `app/services/modelops/lineage.py:33-120` |
| provider health / loaded cache | present | `app/services/provider_health.py`、`app/services/modelops/loaded_cache.py` |
| **五要素聚合资格门** | **partial → 本线补齐** | 分散在 5 处，无统一只读投影 |

## 5. 原生原语缺口核实（最关键）

| 候选 | 判定 | 证据 |
|---|---|---|
| 条件/多分位数分区栅格统计 | **missing（真实缺口 #1）** | `app/tools/advanced_spatial.py:45` `ZonalStatsArgs` 仅 geojson+raster_path；`app/lib/geo_analysis/raster_ops.py:234-254` stat 白名单 count/min/max/mean/sum/std，其余 → None + warning。已排除的「同等实现」：`remote_sensing.py:346` percentiles 是 SAR 时间维、`rs_cube_tools.py:78` quantiles 是特征摘要、`advanced_spatial.py:1970` SGS 是条件模拟 |
| 线网服务覆盖一致性校验 | **missing（真实缺口 #2）** | 全 `app/tools/` 无 consistency 注册点；只有生成能力 `network_tools.py:591,609` `network_service_area`、`advanced_spatial.py:2662` `service_area_simple`、`isochrone_network` |
| 网络 OD 成本矩阵 | **present（避让）** | `app/tools/network_tools.py:485,503,531` → `engine.solve_od_matrix`；`MAX_OD_MATRIX_PAIRS=10_000`；`apply_contract` |
| 插值 LOOCV / 不确定性 | **present（避让）** | `app/tools/advanced_spatial.py:136,189-205,1019`（`cross_validate=True`、`loocv_residual_quantiles`）；`app/lib/gis/uncertainty.py:159` `ValidationMetrics(method="loocv")` |

## 6. Skills / 可发现工具

- `.agents/skills/` 与 `.claude/skills/`（符号链接）**只读使用**，未授予产品 Agent
  任意 shell / 网络 / 文件写入权限。
- `skills-lock.json` / `ToolRetrievalIndex` 未改；新语料是离线 JSON，
  只供 `availability_rerank` 做负例抑制与正例加权。

## 7. 真实测试环境事实

- 8 面对账全量分布（基线 397 工具）：
  `by_overall = {'warning': 184, 'pass': 213}`，无 fail / not_evaluated；
  top reasons `metadata_incomplete(158)`, `result_size_unknown(76)`,
  `binding_unbacked(63)`, `domain_keywords_missing(5)`,
  `ref_offload_without_ref_contract(4)`。
- 213 个 pass 工具的 registration / dispatch / result 三面均 pass →
  `native_verified` 判定规则有真实样本，不是空快照。
- `get_algorithm_registry().count == 253` 是既有硬断言
  (`tests/unit/gis/test_g02_science_oracles.py:575`) → 本线不新增算法描述符。
- 基线生成物：`execution_catalog_docs --check` up to date；
  `check_generated_staleness.py` 的 8 项 stale 属 Issue #2007 历史债务
  （`git show origin/master` 重算的输入指纹与账本不符，master 自身即不匹配）。
