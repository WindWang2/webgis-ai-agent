# G03 勘察数据:GIS 工具能力元数据 8 个消费面契约对账(只读勘察)

- 基准: origin/master b1b2ec23(worktree /home/kevin/project/webgis-g03-tool-conformance)
- 方法: 全部为 file:line 级代码证据;不确定处标 unknown。
- 红线遵守: 零代码修改,仅创建本文件。

---

## 1. 工具注册权威(app/tools)

### 1.1 ToolRegistry = 唯一执行真相
- `app/tools/registry.py:545-557` `register(name, description, func, ..., tier, domains, execution_policy, timeout, version, contract_version, cost, **kwargs)` — 描述符扩展字段全部经 kwargs 传入。
- `app/tools/registry.py:572-577` 未知 kwargs 显式 `raise ValueError`(注册期显式失败);`registry.py:541` `_KNOWN_REGISTER_KWARGS` 白名单。
- `app/tools/registry.py:583-610` 注册期调用 `validate_descriptor_fields`(致命错误 → ValueError → 进程启动失败)。
- `app/tools/registry.py:717-758` `_metadata[name]` 单一存储:tier/domains/execution_policy/timeout/version/contract_version/cost/status/side_effect/requires_credentials/capabilities/algorithms/provider_dependencies/output_semantic_type/produced_refs/accepts_ref_types/network/deterministic/result_size_policy/required_fields + V3 全量字段(input_artifacts/required_context/map_mutations/data_mutations/latency_class/memory_class/scale_class/crs_semantics/unit_semantics/idempotent/security_tier/required_permission/examples/anti_examples/failure_modes/fallback_tool)。
- `app/tools/registry.py:714-716` tier>=3 强制 side_effect=DESTRUCTIVE(不允许"忘了标就是纯读")。
- `app/tools/registry.py:759-775` 注册期执行策略审计(`app/tools/policy_audit.py` audit_registration,error 级由测试钉零)。
- `app/tools/registry.py:1140` `dispatch()`;`registry.py:141` `confirm_tier3()` 上下文(tier-3 确认闸唯一入口)。
- 身份/版本字段:`registry.py:962` `tool_version()` = name@version#cvN;`registry.py:1092/1107` schema/descriptor 指纹。
- 能力派生回填:`registry.py:44-53` `_derive_capability_backfill` — 工具未声明 capabilities 时从 AlgorithmRegistry.tool_candidates 反查(引用不是新真相),`capability_source = declared | derived:algorithm_registry | none`(registry.py:1024-1030)。

### 1.2 ToolDescriptor(派生只读投影,非第二注册中心)
- `app/tools/descriptor.py:1-21` 模块头声明"ToolRegistry 仍是唯一执行真相;ToolDescriptor 是派生只读投影"。
- `app/tools/descriptor.py:34-51` ToolStatus(stable/experimental/deprecated/hidden/external_unavailable/planned)。
- `app/tools/descriptor.py:57-71` SideEffectClass 7 值(unclassified/pure/deterministic_compute/cacheable_read/state_mutation/artifact_creation/external_side_effect/destructive),未标注默认 UNCLASSIFIED(:60-62,注册期仅告警)。
- `app/tools/descriptor.py:117` RESULT_SIZE_POLICIES = (unknown, inline_small, bounded, ref_offload)。
- `app/tools/descriptor.py:125-152` V3 词表(LATENCY/MEMORY/SCALE_CLASSES、CAPABILITY_SOURCES、REQUIRED_CONTEXT_KINDS、MAP_MUTATION_KINDS、DATA_MUTATION_KINDS)。
- `app/tools/descriptor.py:155-229` ToolDescriptor 字段(身份 name/version/contract_version/status;调度 tier/domains/cost/execution_policy/timeout;安全 side_effect/requires_credentials/security_tier/required_permission;能力引用 capabilities/algorithms;I/O 契约 output_semantic_type/produced_refs/accepts_ref_types/required_fields/network/deterministic/result_size_policy;V3 资源分级/示例/失败模式)。
- `app/tools/descriptor.py:310-356` `contract_payload()` 指纹载荷;`:363-402` canonical JSON + SHA-256 短指纹(schema_fingerprint/descriptor_fingerprint/manifest_fingerprint);`:405-537` `validate_descriptor_fields` 注册期校验门(status/side_effect/result_size_policy/词表合法性/anti_examples 上限)。
- 构造器:`registry.py:996-1087` `descriptor(name)`(缓存于 `_descriptor_cache`)。

### 1.3 categories.py(功能分类元数据投影)
- `app/tools/categories.py:27-119` `_MODULE_DEFAULTS` 模块默认归类(9 类:planning/data_access/analysis/transformation/rendering/map_mutation/export/inspection/debug_internal);`:119` 起名字覆盖;`:190` classify_tool;`:197` build_tool_category_manifest。分类不改变注册、不影响 dispatch(元数据投影)。

### 1.4 结论(面 1)
- 权威源 = ToolRegistry._metadata(register() 写入);descriptor/categories 均为派生。无独立手写副本(技能热重建 re-register 时 registry.py:611-620 显式告警防静默覆盖)。

---

## 2. GIS execution_catalog(F07)

### 2.1 条目结构与指纹
- `app/lib/gis/execution_catalog.py:1-27` 模块头:CapabilityRegistry/AlgorithmRegistry/ToolRegistry/RecipeRegistry 是唯一真相,catalog 是 versioned、指纹化、有界的只读聚合投影。
- `app/lib/gis/execution_catalog.py:35` `EXECUTION_CATALOG_VERSION = 1`(参与全部指纹)。
- `app/lib/gis/execution_catalog.py:91-137` `CatalogEntry`(kind+id 唯一):version/contract_version/provider(core/dynamic/unknown)/status/capabilities/input_output_semantic_types/geometry_requirements/crs_class/unit_requirements/temporal_constraints/deterministic/random_seed_policy/side_effect/resource_class(latency/memory/scale/cpu/io)/cancellation_profile/resource_envelope/fallback_targets/superseded_by/deprecated/certification/priority/detail。
- `app/lib/gis/execution_catalog.py:139-160` `fingerprint_payload()` + `fingerprint`(canonical JSON SHA-256 前 32 位,含 catalog_version 盐;cert_state 参与指纹)。
- 投影函数:`:286` _capability_entry、`:323` _algorithm_entry、`:393` _tool_entry(逐字段 getattr 读取 descriptor 事实;capability 双口径 capabilities=生效面 / declared_capabilities=纯声明面,:393-414)、`:486` _recipe_entry。
- 编译/缓存:`:639` compile_execution_catalog、`:717` get_execution_catalog(compile-once 缓存)、`:741` refresh_execution_catalog、`:745` seed_execution_catalog(启动时注入扩展认证证据,app/main.py:254-262)。
- 对账:`:825` reconcile_with_manifest、`:923` reconcile_with_capability_graph(ReconcileIssue :812)。
- 解析:`:567` `tool_candidates_for_capability`(capability → algorithm priority 序 → 已注册工具)。

### 2.2 _discovery
- `app/lib/gis/execution_catalog_discovery.py:65` DiscoveryQuery、`:95` DiscoveryCandidate(带 reason codes + 证据)、`:119` DiscoveryResult。
- `:146` `_tool_executable_reason` / `:174` `_algorithm_executable_reason`(候选不可执行的 reason code 投影);`:216` _candidate_score;`:256` `discover()`;`:349` discover_to_payload。
- 消费者:`app/tools/catalog_discovery_tools.py:66-68`(catalog_discover / catalog_lookup 两个 tier-2 只读工具,:78 register_catalog_discovery_tools)。

### 2.3 _staleness
- `app/lib/gis/execution_catalog_staleness.py:89` catalog_snapshot_ref(plan 携带快照)、`:126` validate_snapshot_ref(损坏快照不判 stale 的诚实规则)、`:181` diff_snapshots(added/removed/changed)、`:197` supersession_map、`:215` explain_staleness(stale + reasons + affected_consumers 归因)。

### 2.4 其他消费者
- `app/services/capability_runtime/snapshot.py:155-157`(H05 运行时快照只读引用 entry);`app/services/gis_action/service.py:108-109`;`app/extensions_platform/refresh.py:50`(扩展激活/停用/吊销重播种)。

---

## 3. algorithm / capability registry

### 3.1 AlgorithmRegistry
- `app/lib/gis/algorithm_registry.py:242-316` `AlgorithmDescriptor`(pydantic):id/name/capabilities/tool_candidates/input_artifact_types/output_artifact_type/geometry_requirements/required_fields/crs_requirements/unit_requirements/parameter_contract_ref/deterministic/cpu_memory_io_cost/runtime_status/fallback_algorithms/priority/version/contract_version + 科学元数据(ADR-0099)+ V3(ADR-0117 resource_envelope/approximation_class/cancellation_profile)+ Wave8 uncertainty_producer_tests。
- `:378-410` AlgorithmRegistry(register 重复 id raise;load_builtins 从 `app/lib/gis/algorithms` 域包种子装载 :369-376);`:456` tool_to_capability、`:498` tool_to_algorithms、`:528` capability_tool_map;`:542` validate(available_tools 悬空候选检查)。
- `app/lib/gis/algorithm_resolver.py:116` AlgorithmResolver — capability→algorithm→tool 确定性裁决(artifact/几何/字段/样本量/成本模型/fallback,`resolve` 主流程;`get_algorithm_resolver` :485)。

### 3.2 CapabilityRegistry
- `app/lib/gis/capability_registry.py:29-74` `CapabilityDescriptor`:id/name/input_output_artifact_types/geometry_requirements/domain/category/deterministic/fallback_capabilities/status(native/planned/unavailable)/version/purpose_template + Graph V1(offline_capable/incompatible_with)。
- `:83` CapabilityRegistry;`:186` load_dynamic_capabilities(ADR-0191 D5 induced.* 动态挂钩,仅 planned/unavailable 状态,:23-34 上限 128 能力/64 文件);`:255` get_capability_registry。
- 词表源:`app/lib/gis/capabilities/` 17 个域包(aggregation/data_access/decision/density/ecology/geometry/interpolation/modelops/network/platform/raster/rs_cube/sampling/statistics/temporal/terrain + __init__ 的 iter_capability_packs)。

### 3.3 runtime_manifest(消费 conformance 的启动闸)
- `app/lib/gis/runtime_manifest.py:122` CompiledRuntimeManifest(manifest_fingerprint);`:171` is_stale_plan;issue code 词表含 algorithm_dangling_capability / recipe_dangling_capability / provider_output_contract_divergence(grep 证实)。fatal issues 进启动 fail-fast。

### 3.4 capability_binding_governance(F06, ADR-0215)
- `app/lib/gis/capability_binding_governance.py:1-24` 声明面 vs 派生面三分处置:legal_multi_provider / metadata_missing(network/deterministic/side_effect/result_size_policy 欠缺)/ suspected_misdeclaration(output_semantic_type 冲突);纯函数 + 只读 live 采集,报告 ≤512 条。

---

## 4. GIS skills / procedures(代码消费面)

### 4.1 权威库(审定 core)
- `app/services/gis_harness/skills/` 包:`contract.py`(SkillContract pydantic 权威)、`loader.py:61` load_skill_library(YAML 资产于 `skills/library/`,fail-loud 单例,:14-16)、`catalog.py:80` SkillCatalog(渐进披露:SkillCard ≤1KB/列表 ≤16KB,:13-24)、`resolver.py`、`validation.py`、`procedure_ir.py`(SkillProcedure IR)、`procedure_binding.py:1-30`(ADR-0220:skill→SessionPlan/PlanGraph 派生绑定,绑定不执行)。
- .md 技能:`app/skills/*.md` 7 个;装载/校验 `app/tools/skills.py:388` register_skill_tools、:193 list_md_skills、:244 _validate_skill_code(AST 白名单);skills-lock 由 `scripts/verify_skills_lock.py` 校验。
- API:`app/api/routes/chat.py:2228` GET /chat/skills(metadata only)。

### 4.2 induced(动态)
- `app/services/gis_skills/induction/__init__.py:1-24` Skill Induction Engine(ADR-0191:trace→analyze→generalize→compile→validate 三门→InducedSkillStore,fail-closed);`dynamic_provider.py:1-17` InducedSkillStore(独立目录/quarantine,对 core 单例零写入);登记 induced.cap.* 动态能力(引 capability_registry 词表)。

### 4.3 工具暴露(消费 registry 而非第二注册)
- `app/tools/skill_library_tools.py:78` register_skill_library_tools(4 个工具:81/129/153/186 — gis_skill_search 等,`_TOOL_MODULES` 一行注册模式)。
- `app/tools/skill_surface_refresh.py:1-19` refresh_skill_surface — 如实报告两层真相(注册表层可热刷新经 webgis_execute;native schema 面要 worker respawn)。

---

## 5. Pi tool-spec 投影(#1620, ADR-0224)

### 5.1 typed spec
- `app/services/chat/pi_tool_spec.py:1-20` 模块头:Registry→Pi 的只读投影编译器,不是第二注册中心;schema 投影 fail-honest(约束性键永不改写;超预算整个工具逐出,绝不裁 schema)。
- `:30` PI_TOOL_SPEC_ABI_VERSION=1;`:35` PI_TOOL_ABI 总闸(env kill switch);`:40-42` per-tool schema 预算(默认 16KB);`:66-83` _SUPPORTED_SCHEMA_KEYWORDS 白名单;`:85-95` 封闭 finding codes(desc_truncated/keywords_dropped/session_id_stripped/additional_properties_pinned/union_retained/refs_retained/spec_over_budget)。
- `:250-355` `PiToolSpecV1`(契约字段进指纹:input_schema/tier/effective_security_tier/side_effect/requires_credentials/execution_policy/network/latency_class 等全部来自 ToolDescriptor;tool_id/schema_fingerprint/descriptor_fingerprint/registry_generation 来源锚;findings 审计面)。
- `:357` compile_tool_spec;`:429` surface_fingerprint;`:448` current_registry_generation(= runtime_manifest.fingerprint,缺席诚实空串)。

### 5.2 per-turn qualification + receipt
- `app/services/chat/pi_surface_receipt.py:33` SURFACE_QUALIFICATION_POLICY="pi_surface_qualification.v1";`:41-60` 封闭 DROP codes(tier3_excluded/side_effect_destructive/credential_absent/permission_not_granted/offline_network_tool/durable_worker_unavailable/spec_over_budget/byte_budget)+ SURFACE_DROP_CODES 快照。
- `:136-210` `qualify_surface()` — 逐出仅凭阳性缺席事实(credentials_present[id]==False / offline==True / durable_worker==False / granted_permissions 列表在场且未含);无来源=unknown=不排除;dispatch 闸仍是唯一执行权威(:15-20)。
- `:213` _SurfaceReceiptStore(≤128 receipts,turn 绑定);`:276` record_turn_surface;`:315-339` verify_callback_fingerprints(→ SURFACE_FINGERPRINT_MISMATCH);`:342` enforce_callback_scope(opt-in PI_SURFACE_SCOPE_ENFORCE :44-47)。
- 输入供给:`app/services/gis_harness/hotpath_convergence/runtime_situation.py:201-223` `to_qualification_dict()`(白名单投影,granted_permissions 派生键 H01 K5)。

### 5.3 surface 组装
- `app/services/chat/pi_native_surface.py:23-30` NATIVE_TOOL_NAMES 7 个前门(webgis_map_intent/webgis_map_product/webgis_component_update/webgis_cartography_status/query_local_poi/get_local_admin_boundary/list_available_tools);`:33` EXECUTE_PROXY_NAME="webgis_execute"(长尾代理);`:76` resolve_pi_tool_call;`:174` native_tools_for_pi;`:389` pi_surface_for_spawn(dump v3 = v2 键 + abi/spec/generation);`:552` compute_turn_active_tools(V3 选择器);`:762-831` _apply_surface_qualification(spec 编译失败不逐出,dispatch 闸不变)。
- stale surface receipt:`app/services/chat/pi_bridge_surface_gate.py:53-115` `stale_surface_gate` — 对照 turn receipt 校验回调 echo/scope,不符返回 typed `error="surface_stale"` + `code=SURFACE_FINGERPRINT_MISMATCH`、retryable=True;接线于 `app/agent_pi_bridge.py:565-566`;turn 终态清理 `evict_turn_receipts`(:39-50)。
- turn token:`app/services/chat/pi_turn_context.py:39` issue_turn_token / `:61` verify_turn_token / `:110` attach_turn_context / `:492` PiTurnRegistry。

### 5.4 result ABI
- `app/services/chat/pi_result_abi.py:19` RESULT_ABI_VERSION="pi_result.v1";`:27` OPERATION_CANCELLED;`:31` kill switch PI_RESULT_ABI;`:37` envelope ≤4KB;`:62` build_result_envelope(status/summary/refs(geojson/result/image)/map_action_ids/error class+code;大对象一律 ref-first);`:204` _error_class。

### 5.5 Pi 侧扩展
- `app/extensions/webgis-tools/index.mjs:25-33` FALLBACK_NATIVE(与后端 NATIVE_TOOL_NAMES 同名快照 — 独立手写副本!);`:61` loadNativeTools 读 WEBGIS_NATIVE_TOOLS_PATH dump(spawn 时冻结);`:13` 注释确认由 app/main.py 经 extension_paths 传入 vendor/pi 加载。

---

## 6. ToolDispatchService

- `app/services/tool_dispatch_service.py:259` class ToolDispatchService;`:139-171` `ToolDispatchResult`(判别式:llm_payload / slim_event(SSE 脱敏) / geojson_ref / raw_result / error_msg / map_actions(V3 action_id) / background_job_ids / capability_evidence(ADR-0204 D4) / action_evidence(ADR-0217 H10))。
- 执行闸权威:registry.dispatch(registry.py:1140);错误码发生点 `registry.py:1326` UNKNOWN_TOOL、`:1335` TOOL_NOT_EXECUTABLE、`:1351` TIER3_CONFIRMATION_REQUIRED、`:1375-1386` CREDENTIALS_REQUIRED/PERMISSION_DENIED(#1402/H05)。
- capability bind 闸:`app/services/gis_harness/hotpath_convergence/capability_bind.py:20-26` CAPABILITY_INELIGIBLE_CODE / CAPABILITY_PROVIDER_UNAVAILABLE_CODE(v2 断路)。
- Pi 输入闸:`app/services/chat/pi_input_gate.py:28` GATE_ERROR_CODE="SCHEMA_VALIDATION_REJECTED"。
- `app/services/tool_dispatch_gates.py:34` MAP_ACTION_ID_PREFIX / 并发原语(_MultiSlotAcquire/_SessionWaveGate)/action_id 铸造(god-modules 棘轮抽出,re-export 保持 import 面)。
- `app/services/tool_metrics.py:192` record_tool_call(JSONL logs/tool_metrics.jsonl,:29;队列+后台 writer,队列满丢弃);`:367` aggregator_snapshot;`:390` emit_digest。
- API 直调:`app/api/routes/chat.py:2396-2443` POST /chat/tools/execute(admin-only + tier-3 需 confirm_destructive + confirm_tier3 上下文);`:2390-2393` GET /chat/tools(认证,返回全量 schema 含 tier-3)。

---

## 7. frontend ability hints

- 结论:frontend **没有** GIS 工具能力元数据投影(无 generated tool catalog / ability hints 文件)。
- 唯一 generated manifest:`frontend/lib/map-components/component-catalog.generated.json:1-4`(schemaVersion 5,exportedFrom app/lib/cartography/component_registry.py — 制图组件目录,非工具注册表)。生成器 `app/lib/cartography/export_component_catalog.py:27`;parity 测试 tests/unit/test_component_catalog_parity.py。
- frontend 中的"工具"仅地图交互工具:`frontend/lib/store/slices/toolSlice.ts:23-48` MapToolId(measure/draw/brush 等封闭词表),与后端 ToolRegistry 无关。
- 后端 /chat/tools 列表端点(chat.py:2390)前端不消费;前端只调 `/api/v1/chat/tools/execute`(frontend/lib/api/chat.ts:366 executeToolDirect;frontend/lib/api/modelops.ts:2)。
- 工具可见性到达前端的通道 = SSE step 事件(slim_event)+ agent-run-panel 的地图工具标签(frontend/components/agent/agent-run-panel.tsx:29-36,仅 MapToolId)。

---

## 8. tool_catalog.py / tool_surface_v2.py

### 8.1 ToolCatalog(选择语义)
- `app/services/tool_catalog.py:1-14` 三层目录:tier-1 always-on / tier-2 domain-scoped 关键词触发 / tier-3 仅显式 list_available_tools。
- `:26-51` `DOMAIN_KEYWORDS` 双语关键词库 — **独立手写副本**(人工维护的关键词表,非从 registry 派生;误激活轻损失/漏激活重损失纪律 :47-49)。
- `:129-134` sticky TTL/域上限;`:140` tier-2 schema 预算;`:145` 本地 OSM 抑制集(又一手写事实表);`:153` class ToolCatalog;`:230-260` select_schemas(user_message+sticky+declared_domains+surface 并集语义);`:424` _local_osm_available(60s 缓存)。

### 8.2 ToolSurfaceProjector(V2 投影)
- `app/services/tool_surface_v2.py:1-28` 模块头:投影不是第二注册中心;唯一输入 ToolRegistry + ToolCatalog 语义;输出 schemas/reasons/dropped/bytes_used/fingerprint;消费者 = legacy 引擎 augment 后处理、Pi 适配层 native_surface_snapshot、评测。
- `:49` SurfaceRequest;`:64` ToolSurfaceProjection;`:83` ToolSurfaceProjector(project → _base_select → augment)。
- augment 闸(:118-160 附近):lifecycle(model_visible/EXTERNAL_UNAVAILABLE → dropped "lifecycle:status")、tier-3 → dropped "tier3"(R1 MAJOR:对基线来源同样生效)、检索补强(只补空隙不挤既有)、压缩(TOOL_SURFACE_COMPRESS)。
- 权威归属:ToolRegistry 是权威;catalog/surface_v2 都是投影。qualification(staleness):v2 surface 指纹 = (name, schema_fp) 的 manifest_fingerprint 缓存失效键;turn 级资格/staleness 在面 5(pi_surface_receipt + stale_surface_gate),catalog 级 staleness 在 execution_catalog_staleness。

---

## A. 现有 conformance 校验盘点(检查什么 / 不检查什么)

### A.1 各校验器检查什么
1. `app/lib/gis/capability_conformance.py:1-21`(ADR-0204 D2,实际是"Capability Binding Conformance"):capability↔tool 双声明面对账。code:capability_id_dangling(fatal,声明 id 不在词表)、capability_binding_unbacked(warning,声明无算法链支撑)、descriptor_metadata_incomplete(warning,关键 metadata 缺席)、provider_output_contract_divergence(warning,同能力多 provider 输出契约冲突)。消费方 compile_runtime_manifest(fatal 入启动 fail-fast)。产出 ≤512 条(:33-34)。
2. `app/lib/gis/execution_catalog_conformance.py:1-18`:目录级全链对账 recipe→capability→algorithm→tool→successor。codes:catalog_capability_dangling / catalog_algorithm_tool_missing(候选工具缺席=部署退化)/ catalog_output_contract_mismatch / catalog_unit_geometry_mismatch(单位/几何矛盾,双方已声明才判)/ catalog_fallback_dangling / catalog_deprecation_target_dangling / catalog_deprecated_no_successor / catalog_recipe_capability_unreachable / catalog_extension_contract_incomplete。链入 capability_conformance 既有码。
3. `app/lib/gis/contract_validation.py:1-17`:**运行时**产物契约验证(声明 output_artifact_types vs DatasetProfile 实况):contract_geometry_kind_mismatch / contract_unregistered_type / contract_empty_artifact / contract_crs_undeclared / contract_raster_grid_evidence_missing/incomplete。只进 ArtifactRegistry metadata 不阻断。
4. `app/lib/gis/parameter_contracts.py:1-16`:类型化参数契约注册表(契约 id = descriptor.parameter_contract_ref 单一事实源;type/default/min/max/enum/unit);validate_parameters 证据式 + apply_contract 严格入口。单位词表 PARAM_UNIT_VOCABULARY 封闭。
5. `tests/unit/test_capability_registry_parity.py:1-17`:native 算法 tool_candidates 全部真注册;recipe 声明 capability 至少一个活候选;derived CAPABILITY_TOOLS 视图精确覆盖 recipe 词表;planned capability 正确豁免。
6. `tests/unit/tool-capability-contract`:**不存在**。`tests/unit/gis_tool_conformance/`:**不存在**。

### A.2 相对审计项的缺口(明显不检查)
- **已声明未注册**:capability_id_dangling(词表)与 parity(算法候选)已覆盖 tool_candidates 面;但工具 `capabilities=` 元数据指向未注册 capability 由 capability_conformance fatal 覆盖 — 已检查(注册面)。
- **已注册不可经 Pi 触达**:**无检查**。无任何测试断言"每个 model_visible 工具可经 Pi native 面或 webgis_execute 代理触达"(代理通道构造性全覆盖,但无 per-tool 断言;native 面是子集且逐出只留 reason code)。标 unknown/缺口。
- **schema required/enum/单位不一致(registry schema vs 算法 parameter_contract)**:**无离线交叉校验**。parameter_contract_ref 与实际 args_model schema 的 required/enum/unit 一致性没有对账器;pi_tool_spec 投影只保证不改写键,不保证两侧一致。catalog_unit_geometry_mismatch 只覆盖 catalog 条目级单位/几何且需双方声明。
- **结果 envelope 遗失错误或数据 ref**:**无离线检查**(运行时 contract_validation 只看 artifact profile;pi_result_abi envelope 是加性投影,无 conformance 断言每个 error 必有 code、每个大产物必有 ref)。
- **side effect 未声明**:非硬闸 — 默认 UNCLASSIFIED 过注册(descriptor.py:60-62);只有覆盖率阈值闸 `app/lib/quality/manifest.py:54-61` GATE_THRESHOLDS(side_effect/tags/latency_class/memory_class/capabilities 等 95%),经 scripts/check_tool_descriptor_coverage.py 执行。
- **需要凭据却暴露可用**:仅运行时(turn qualification credential_absent + registry CREDENTIALS_REQUIRED);无静态/离线 conformance。
- **参数在 adapter 被忽略**:**无静态检查**(pi dispatch adapter 行为靠 ad hoc 测试 tests/unit/test_pi_dispatch_adapters.py;无"schema 参数 → adapter 真消费"对账)。

---

## B. 错误 / reason code 权威表

- **单一事实源(H01, #1756)**:`app/services/gis_harness/hotpath_convergence/dispatch_reason_codes.py:1-19` — 机器码常量(:27-47:UNKNOWN_TOOL/TOOL_NOT_EXECUTABLE/TIER3_CONFIRMATION_REQUIRED/CREDENTIALS_REQUIRED/PERMISSION_DENIED/SCHEMA_VALIDATION_REJECTED/CAPABILITY_INELIGIBLE/PROVIDER_UNAVAILABLE/OPERATION_CANCELLED/TOOL_ERROR)+ ERROR_CLASSES 封闭词表(:49-59)+ ERROR_CLASS_BY_CODE 确定性映射(:63-74)+ TURN_DROP_PARITY(turn drop ↔ dispatch canonical 对照,:77-84)。
- **qualification check → canonical 码**:`app/services/gis_harness/hotpath_convergence/capability_reasons.py:24-54` REASON_CODES 封闭映射(offline→offline_network_required、credentials→credentials_missing、required_permission→permission_not_granted 等;未登记 check 投影 `unmapped:<check>` 绝不静默丢失,:22-23 注释)。
- **生产者字面量**:registry.py:1326/1335/1351/1375-1386;pi_input_gate.py:28;capability_bind.py:20-26;pi_result_abi.py:27;pi_surface_receipt.py:41-60;pi_bridge_surface_gate.py:94-104("surface_stale" + SURFACE_FINGERPRINT_MISMATCH)。
- **搜索结果**:credential_absent(pi_surface_receipt.py:49)、offline(REASON_CODES)、stale_surface(pi_bridge_surface_gate.py:96)均有;**unsupported_method 与 data_requirement_missing 全仓无匹配(unknown/不存在)**。
- **新增码注册路径**:① 生产者写字面量 → ② dispatch_reason_codes.py 加常量 + ERROR_CLASS_BY_CODE(+TURN_DROP_PARITY,注释 :73-79:未登记由一致性守卫测试暴露)→ ③ 新 drop code 须入 SURFACE_DROP_CODES(pi_surface_receipt.py:56-64)→ ④ 新 qualification check 须入 REASON_CODES → ⑤ parity 测试钉住:tests/unit/test_pi_h01_reason_code_parity.py:39-55(inspect.getsource 钉 registry 字面量)+ test_pi_h01_result_contract.py。

---

## C. ref-first 结果信封

- **ref 铸造**:`app/services/tool_dispatch_service.py:1097` `geojson_ref = await self._session_data.store(session_id, target_data, prefix="geojson")`(顶层 FC 与 data 包裹 FC 两种形状均入 store,:1085-1097);kriging 不确定面第二 ref(:1100 附近);复用路径 `:1079` reused_result["ref_id"];结果携带 `:156` geojson_ref / `:160` ref_descriptor。
- **ref 解析**:`app/tools/_utils.py:277`(裸 ref:... id 即合法 key)。
- **LLM 载荷防大**:`app/services/llm_result_formatter.py:18-21` MSG_MAX_CHARS=2500/VALUE_MAX_CHARS=80/SAMPLE_FEATURES=5;`:51` 注释"slim_tool_result 是工具结果进入 LLM 上下文前的唯一闸口";`:32-49` _PRESERVED_META_KEYS(ref_id/result_ref/uncertainty_ref 等保留键)。
- **Pi details 防大**:`app/agent_pi_bridge.py:405-441` _slim_pi_details_payload(64KB 上限;剥 image data URL→imageRef、剥 geojson、features>100 只留 feature_count)。
- **result ABI envelope**:`app/services/chat/pi_result_abi.py:62-80` build_result_envelope(≤4KB;refs 一律 ref-first;error class+code 不泄原始异常)。
- **SSE 通道**:ToolDispatchResult.slim_event(tool_dispatch_service.py:157)供前端;LLM 历史入参侧 = slim_tool_result 唯一闸。

---

## D. scripts/ 下只读离线校验命令

- `scripts/check_tool_descriptor_coverage.py:1-15` — 描述符富化覆盖率闸(薄壳 → app/lib/quality/manifest.py GATE_THRESHOLDS/collect/gate)。
- `scripts/gen_capability_catalog.py:1-19` — 重生成 docs/science/CAPABILITY_CATALOG.md(capability graph 投影 + 结构审计:孤儿/环/不可达/无消费者/弃用暴露)。
- `scripts/recipe_eligibility_audit.py`、`scripts/check_generated_staleness.py`、`scripts/check_source_registry.py`、`scripts/verify_skills_lock.py`、`scripts/check_import_boundaries.py`。
- **没有**独立的"execution catalog conformance / Pi surface 对账"CLI(conformance 均为纯函数,由 runtime_manifest 编译与测试消费)。缺口可考虑补 CLI。

---

## E. 测试布局

- `tests/unit/gis_tool_conformance/`:**不存在**。`tests/unit/tool-capability-contract`:**不存在**。
- 最重要的现有测试(≤20):
  1. tests/unit/test_capability_registry_parity.py(algorithm↔registry↔recipe parity)
  2. tests/unit/test_pi_tool_spec.py(spec 投影)
  3. tests/unit/test_pi_surface_receipt.py(qualification/receipt)
  4. tests/unit/test_pi_result_abi.py(envelope)
  5. tests/unit/test_pi_abi_adversarial.py(859 行对抗)
  6. tests/unit/test_pi_surface_abi_route.py(路由/闸)
  7. tests/unit/test_pi_dynamic_surface.py(spawn dump 形状)
  8. tests/unit/test_pi_surface_parity.py(Pi/legacy 校验等价)
  9. tests/unit/test_pi_h01_reason_code_parity.py(词表 parity)
  10. tests/unit/test_pi_h01_result_contract.py(result 契约)
  11. tests/unit/test_pi_h01_permission_qualification.py(权限资格)
  12. tests/unit/test_pi_h01_surface_disclosure.py(披露面)
  13. tests/unit/test_pi_dispatch_adapters.py(adapter 行为,ad hoc)
  14. tests/unit/test_tool_descriptor_v2.py / v3(描述符契约)
  15. tests/unit/test_tool_surface_v2.py / test_tool_surface_v3.py
  16. tests/unit/test_tool_catalog.py / test_tool_catalog_sticky_684.py
  17. tests/unit/test_execution_catalog_docs_drift.py(catalog 文档漂移)
  18. tests/unit/test_capability_dispatch_bind_unified.py(bind 闸统一)
  19. tests/unit/test_audit4_tools_meta.py(工具元数据审计)
  20. tests/unit/gis_harness/test_h01_capability_scenarios.py + test_h01_qualification_completion_convergence.py(H05 场景)

---

## F. #1620 / #1756 ABI 文件位置与关键函数

### F.1 #1620(merge b43f1de2, "Pi Native GIS Tool ABI V2", ADR-0224)
- typed spec:`app/services/chat/pi_tool_spec.py` — PiToolSpecV1(:250)、compile_tool_spec(:357)、project_schema_for_pi(:204)、surface_fingerprint(:429)、current_registry_generation(:448)、tool_abi_enabled(:85)。
- qualification:`app/services/chat/pi_surface_receipt.py` — qualify_surface(:136)、SurfaceDrop/SurfaceReceipt(:78/:89)、record_turn_surface(:276)、_SurfaceReceiptStore(:213)。
- receipt 校验(回调):verify_callback_fingerprints(:315)、enforce_callback_scope(:342);闸 `app/services/chat/pi_bridge_surface_gate.py:53` stale_surface_gate。
- result ABI:`app/services/chat/pi_result_abi.py` — build_result_envelope(:62)、_error_class(:204)。
- 接线:`app/agent_pi_bridge.py:565-566`、`app/services/chat/pi_turn_context.py`、`app/services/chat/pi_native_surface.py`(compute_turn_active_tools :552 / _apply_surface_qualification :762)、`app/extensions/webgis-tools/index.mjs`。
- ADR:docs/adr/0224-pi-native-gis-tool-abi-v2.md。

### F.2 #1756(merge f3928175, "H01 资格→完成判据收敛")
- 新增 `app/services/gis_harness/hotpath_convergence/dispatch_reason_codes.py`(106 行,词表单一事实源)。
- 扩展:pi_result_abi.py(error class 映射 +95 行)、pi_surface_receipt.py(DROP_PERMISSION_NOT_GRANTED + SURFACE_DROP_CODES)、pi_turn_context.py、runtime_situation.py(granted_permissions_view :214-222)、gis_harness/completion/contracts.py、planner.py。
- 测试:test_pi_h01_{permission_qualification,reason_code_parity,result_contract,surface_disclosure}.py + tests/unit/gis_harness/test_h01_*。

---

## G. plugin / vendor pi 位置与只读地位

- `vendor/pi` 是 git submodule:`git submodule status` → `-dd6bea41efa8caa7a10fe5a6401676dc5699f83f vendor/pi`(前导 `-` = 本 worktree 未 init,目录为空)。本仓库不携带其源码,只钉 commit。
- 加载:`app/extensions/webgis-tools/index.mjs:12-13` 注释 — app/extensions/ 不是 Python 包,只承载 Pi coding-agent 扩展入口产物,由 app/main.py 启动时经 extension_paths 传入 vendor/pi 加载。
- 只读地位:submodule 钉版本、独立上游仓库、宿主不修改其内容;Pi 原生面冻结于 spawn(native schema dump 文件 WEBGIS_NATIVE_TOOLS_PATH,index.mjs:61-64),热刷新语义见 app/tools/skill_surface_refresh.py:1-19。

---

## 附:独立手写副本清单(对账风险点)

1. `app/services/tool_catalog.py:26-51` DOMAIN_KEYWORDS + `:145` _SUPPRESS_WHEN_LOCAL_OSM — 人工维护事实表,无 registry 派生。
2. `app/extensions/webgis-tools/index.mjs:25-33` FALLBACK_NATIVE — 后端 NATIVE_TOOL_NAMES(pi_native_surface.py:23)的 JS 快照,无生成机制(parity 靠 test_pi_dynamic_surface 断言超集)。
3. `app/agent_pi_bridge.py:405-441` _slim_pi_details_payload — 与 llm_result_formatter 的 slim 语义并行(64KB vs 2500 chars 两套预算口径)。
4. `_LABELS`/`_SNIPPETS`(pi_native_surface.py:37-56)— native 前门的展示文案手写副本。
5. `app/services/gis_harness/hotpath_convergence/capability_reasons.py:24-54` 生产词汇别名 — 有 parity 测试钉住,风险受控。

## 附:unknown / 未决

- `unsupported_method`、`data_requirement_missing` 两个码全仓(代码+测试)无匹配 — 若审计需要须新增。
- frontend 无工具能力 hints 面(面 7 结论),若 G03 要做前端投影属全新增,非对账既有面。
- tests/unit/ 下未发现名为 tool-capability-contract 或 gis_tool_conformance 的目录/文件。
