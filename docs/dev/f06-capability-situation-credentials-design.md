# F06 — Design：Runtime Situation 生产供给 + 凭证/权限绑定治理（ADR-0215 候选）

前置：recon 文档（同目录）。红线：不重写 ToolDispatchService；secret 不进 QualificationContext/evidence/replay；capability ranking 不绕过硬门；manifest 保持收敛投影（不建第二注册表）。

## D1 — RuntimeSituation 生产构造器（`hotpath_convergence/runtime_situation.py` 新模块）

`RuntimeSituation`：有界 typed snapshot（dataclass，全部字段定长/截断），来源事实分五组，每组带 provenance：

| 组 | 来源 | 事实 |
|---|---|---|
| identity | `current_runtime_context()` / 调用方 hint | session/turn/run/request id、tenant、mission_id（hotpath session_ctx）、owner_scope_key（仅调用方显式提供时） |
| security | D2 凭证桥 | `credentials_present`（id→bool，≤8）、granted permissions 投影（bool 级） |
| availability | D5 探针 | `celery_broker`、`durable_worker:<profile>`、`runtime_offline`（仅显式 env 时断言） |
| caller | 调用方显式 situation（planner 面已有 task_hint/profile 数据事实） | base 字段逐面保留（caller facts win） |
| meta | 构造器本身 | supply 版本、kill-switch 状态、构造时间面（monotonic，不进 digest） |

契约：
- `build_runtime_situation(session_id, *, tenant_id="", base=None, owner_scope_key="") -> Optional[RuntimeSituation]`；kill switch `GIS_SITUATION_SUPPLY`（默认 ON）；任何异常 → None（bare-context 语义兜底，绝不阻断 dispatch）。
- `to_qualification_dict()` → 产出 `capability_bind._situation_from_optional` 白名单 24 字段子集 —— **复用既有接缝，零新消费面**。
- `facts_digest()`：稳定事实投影 sha256 前 16 —— planner↔dispatch 等价性断言键（DoD 1）。
- 进程内 TTL 缓存（(session,turn) 2s / availability 15s，线程安全，≤64 条 FIFO）—— dispatch 热路径零重复探针。

### 断言纪律（向后兼容核心）
只断言有可靠来源的事实：凭证 presence（D2 provider）、offline（`GIS_RUNTIME_OFFLINE` 显式设置）、依赖可用性（D5 探针成功时）。`auth_tier`/`budget_cost_class`/`quality_gate` 无生产来源 → 恒 unknown。**今日全量工具零凭证/权限声明 + 事实全默认 ⇒ bind 裁决逐位不变；声明/配置到位后闸自动生效。**

## D2 — 凭证/权限 presence bridge（`app/lib/tool_security.py` 新模块 + hotpath 接线）

- `CredentialPresence`（frozen）：`credential_id, kind, expires_at(""), owner_scope(""), source` —— **无任何 secret 材料**；`to_dict()` 有界。
- `CredentialPresenceProvider` Protocol：`available_credentials() -> Dict[credential_id, CredentialPresence]`（bounded ≤64）。
- 默认 `EnvCredentialPresenceProvider`：解析 `GIS_TOOL_CREDENTIALS`（`id[:kind[:expiry_iso]]` 逗号分隔，≤64 项）—— presence-only，secret 留在部署侧 secret manager（provider 只回答「某 id 是否配置」）。
- `set_credential_presence_provider()` 进程级注册点（测试/扩展注入）；`resolve_credential_presence()` 绝不抛。
- 接线 CM `bind_session_security(...)`（hotpath_convergence/security_supply.py）：provider presence → `present_tool_credentials(*ids)` ContextVar（registry 闸 #1402 生效）+ 返回 presence view 供 D1 situation 消费。
- 权限同理（`GIS_TOOL_PERMISSIONS` env provider）；tier3/plan-approved 既有授予面不变（user-wins）。

## D3 — chokepoint 统一供给（消除 4 面 bare context）

`ToolDispatchService.dispatch` 在 bind 前：`situation is None and situation_supply_enabled()` → `build_runtime_situation(session_id)`（fail-open）；caller 传入的 dict/ctx → `enrich(base=…)` 注入 security/availability 事实（caller 数据事实 win）。Pi/legacy/workflow 4 面 + 未来调用方一次全覆盖；行为翻转仅来自 D1 断言纪律所述的新配置。kill switch 同 `GIS_SITUATION_SUPPLY`。

## D4 — declared vs derived 分歧治理（`app/lib/gis/capability_binding_governance.py` 新模块）

- 输入：`validate_capability_conformance` 原始 issues + tool metadata + algorithm 面（纯函数，零 I/O）。
- 分类（确定性规则，逐对）：
  - `legal_multi_provider`：声明工具 descriptor 元数据完备（network/deterministic/side_effect/result_size_policy 齐全）且 `output_semantic_type` 与该 capability 既有 provider 一致 → 合法的「声明先到、算法链未注册」provider；处置=保留披露。
  - `metadata_missing`：descriptor 元数据欠缺（与 `descriptor_metadata_incomplete` 同源）→ 处置=补 descriptor 元数据（migration hint 字段级列出）。
  - `suspected_misdeclaration`：`output_semantic_type` 冲突 / 工具 planned 状态 / 声明与工具语义词面矛盾 → 真实错误候选；处置=修声明或补算法链。
- 产出：`CapabilityBindingGovernanceReport`（≤512 条、确定性排序、JSON 可序列化）+ `format_governance_report()`（markdown）+ `python -m app.lib.gis.capability_binding_governance` CLI（live registry 报告）。
- 门禁：canary 测试断言 live registry `fatal==0` 且 `suspected_misdeclaration_count` 不超过 committed baseline（ratchet：`tests/unit/gis/data/capability_binding_governance_baseline.json`）—— 治理收敛单调，不搞一刀切删除。
- durable 诚实性注记：报告含 `durable_label_honesty` 节（execution_policy=celery 的工具在「broker 未配置/required 未满足」部署下的 cancellation 标签降级披露）。

## D5 — 运行时事实 → 资格 + alternatives 解释

- 探针（runtime_situation 内，有界缓存）：`celery_broker_available`（settings.USE_REDIS + GIS_CELERY_REQUIRED 读取，零 I/O）；`durable_worker_available(profile)`：`WorkerRegistry.list_active` **只读 import** + try/except 降级 unknown（#1499 热区不写入）。
- 注入 `QualificationContext.dependency_available`（键：`celery_broker` / `durable_worker:<profile>` ≤8）→ qualification 既有 `dependency` check（qualification_v8:274 先例）产生结构化 reason → bind 拒绝的 alternatives/`make_available` 解释自然携带可用性事实（复用既有 reason 通道，零新裁决逻辑）。

## D6 — 关系词表 v2（不建第二注册表）

- 词表 +3：`REL_CONSUMES = "consumes"`（tool → artifact_type，与 algorithm 面 accepts/produces 对称）；`REL_DEPENDS_ON = "depends_on"`（capability → capability）；`REL_ALTERNATIVE_TO = "alternative_to"`（capability ↔ capability / tool ↔ tool 同 kind 对称）。
- validate_graph 约束：kind 对 + 禁自环；`depends_on`/`alternative_to` 入既有 cycle 审计。
- 声明面（唯一真相扩展，additive 全默认）：CapabilityDescriptor + `depends_on: List[str] ≤6`、`alternative_to: List[str] ≤4`（build 面对称去重建边）；ToolDescriptor metadata + `consumes_artifact_types: List[str] ≤4`。
- resolution 消费：`_fallback_alternatives` 将 `alternative_to` 边并入替代候选源（深度 ≤2 既有预算内）；`resolve_capabilities` 对 `depends_on` 未满足（依赖 capability 无 eligible provider）→ degraded + `dependency_unavailable` reason。manifest 不变（图仍是收敛投影）。

## D7 — denial/allow evidence 进 turn trace + canonical reason codes

- `hotpath_convergence/capability_reasons.py`：canonical 词表模块 —— qualification check → 稳定 reason code（`offline_network_required` / `credentials_missing` / `permission_not_granted` / `auth_tier_insufficient` / `budget_exceeded` / `quality_gate_blocked` / `dependency_unavailable` / `latency_constraint` / `owner_scope_mismatch` / `raster_bands` / `resolution` / `temporal_inputs` / `data_volume` / `gpu` / `min_features`…）；`reason_codes_from_qualification(qual) ≤6`。
- bind evidence + `to_details()` 增加 `reason_codes` 字段（id 级，无参数无凭证）；`CapabilityBindOutcome` 不变形状。
- dispatch 拒绝分支：`emit_chain(Stage.TOOL_CALLS, tool=…, status="denied", decision=decision_record(DECISION_KIND_CAPABILITY_DISPATCH_DENIAL, …))`（riding 附加记录，不新加 Stage —— decision_record.py 既有约定；TOOL_RESULTS 的 once 语义不受影响）。`decision_id` 内容地址确定 → replay metrics/drift 消费端零改动即生效。

## D8 — conformance fixtures + registry canary

- `app/lib/gis/conformance_fixtures.py`：确定性合成 registry 构造器（`make_conformance_tool_registry(profile=…)`）——覆盖 4 conformance 码、新关系词、凭证/权限声明面、execution_policy 变体；供 core/extension 认证测试复用（ADR-0201 面可直接 import）。
- canary（真实 registry）：fatal==0；governance 分类全量可分；分歧 ratchet 不增；planner↔dispatch situation facts_digest 等价（fixture 会话）；`describe_capability()["abi"]` 离线/凭证并集在 situation 供给后不变（单一真相守护）。

## 兼容性 / 风险

- 新字段全 additive；manifest 版本不变（指纹不受影响 —— situation/availability 不进 manifest）。
- 生产行为翻转面 = 新配置（`GIS_TOOL_CREDENTIALS`/`GIS_TOOL_PERMISSIONS`/`GIS_RUNTIME_OFFLINE`）或工具声明新字段；默认部署逐位不变。
- 热区编辑：tool_dispatch_service.py（auto-supply ~10 行 + refusal 记录 ~20 行）、capability_graph.py（词表 + 校验 ~40 行）、capability_resolution.py（替代源 + 依赖降级 ~40 行）、capability_registry.py（2 个 additive 字段 + 建边）、hotpath `__init__.py` 导出。
