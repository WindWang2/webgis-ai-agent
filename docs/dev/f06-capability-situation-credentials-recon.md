# F06 — Capability Runtime Situation & Credential/Permission Binding：Recon

- 基线：`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-24，执行时 `git fetch` 复核无新提交；seed snapshot SHA 仍成立）
- 分支/worktree：`zcode/f06-capability-situation-credentials-20260926-9e1ad229` / `wt-webgis-f06-capability-situation-credentials-20260926-9e1ad229`
- 方向：#1482（capability ABI 2.0）明确登记的 follow-up —— situation 生产供给、凭证供应链布线、声明绑定分歧治理、durable 标签诚实性、关系词表、运行时事实入资格、denial evidence、conformance fixtures/canary。

## 1. 执行时 GitHub 状态（2026-09-26 实测）

- open PR：#1489（dependabot docker，零交集）、#1497–#1504（F08–F15 并行方向，全部基于同一基线 9e1ad229）。
- 最近 merged：#1490–#1496（dependabot/quality 收敛）、#1479–#1488（基础大波次，含 #1482 capability ABI 2.0）。
- open issues：#1436（前端 i18n）、#1377（audit 延期跟踪）——与本方向无交集。

### 与 open PR 的 changed-file overlap 检查

| 文件 | open PR 触碰 | F06 处置 |
|---|---|---|
| `app/services/governor/dispatch_adapter.py` | #1498/#1499/#1503 三方 | **只读 import，不改** |
| `app/services/tool_dispatch_service.py` | #1503（ctor 注入 session_data） | 最小 additive 编辑（auto-supply + denial 记录），与 ctor 区不重叠 |
| `app/lib/runtime/{evidence,decision_record,gis_trace}.py` | #1503 | 不改（evidence 面复用既有 add_capability_dispatch；decision 面只新增调用方） |
| `app/services/gis_harness/capability_graph.py` | #1503（get_cached_capability_graph 探针） | 小编辑（词表区 L56-96 / validate 区），与其新增函数区（L872+）不重叠 |
| `app/services/workflow_runtime/*`、`governor/*` | #1499 | **不触碰** durable dispatcher 面；只读消费 `WorkerRegistry` 事实（含异常降级） |
| `app/lib/runtime/capability 相关`（capability_bind.py / capability_conformance.py / runtime_manifest.py / capability_resolution.py / registry.py 凭证段） | 无 open PR | 本方向主战场 |

## 2. 现状精读（file:line 实证）

### 2.1 situation 供给现状

- `bind_tool_capability(tool_name, *, registry, session_id="", situation=None)`（hotpath_convergence/capability_bind.py:148）已全通：`_situation_from_optional`（:85-107）接受 QualificationContext / 24 字段白名单 dict / None→bare context。
- `ToolDispatchService.dispatch(tc, session_id, executed_tools, situation=None)`（tool_dispatch_service.py:372）—— **全部 4 条调用路径都传 None（bare context）**：
  1. Pi 桥：agent_pi_bridge.py:748
  2. legacy pipeline：chat/tool_pipeline.py:177
  3. legacy engine：chat/execution_engine.py:2841
  4. workflow step：workflow_engine.py:86
- 生产 QualificationContext 构造点只在 planner/intent 面：plan_orchestrator.py:576,784（仅 task_hint）、gis_harness/tools.py:569,808、planner.py:1264（finalize 时 situation_from_profile —— 唯一数据事实注入点）。
- `RuntimeSituation` 名字仅存在于评测语料（evaluation/runtime_corpus.py），生产类型不存在。
- 结论：**dispatch 期没有任何路径供给真实资格事实**；#1482 语义承诺「situation 接线后才可能拒绝」尚未兑现。

### 2.2 凭证/权限现状（#1402 闸已立、供应链为零）

- 闸：registry.py:1318-1336 —— `requires_credentials` 对照 `present_credentials()` 缺失→`CREDENTIALS_REQUIRED`；`required_permission` 对照 `granted_permissions()` →`PERMISSION_DENIED`。
- 供给：`present_tool_credentials`/`grant_tool_permissions`（registry.py:148-169 ContextVar CM）**生产零调用**（仅测试）；当前 app/tools 下无任何工具声明这两个字段 → 闸处于「契约完备、事实为空」状态。
- 凭证 type/expiry/owner 元数据：全仓不存在 credential store 模块（recon grep 实证）→ F06 需要新建 presence-only provider（secret 留在安全 provider，只暴露 type/id/presence/expiry/owner-scope）。
- Pi 桥 owner 身份硬编码 None（agent_pi_bridge.py:738-739）；legacy engine 有 owner_id/owner_token（execution_engine.py:1492,1995）。

### 2.3 declared vs derived 分歧现状

- 检测：capability_conformance.py:149-153 `capability_binding_unbacked`（warning）；manifest 编译折叠为单条 `tool_capability_divergence` 聚合 warning（runtime_manifest.py:455-459）；测试钉折叠行为（test_capability_binding_conformance.py:181-186）。
- #1482 实测 61 对 / 57 工具（image_segmentation×18 等）；**逐对明细无持久化产物**，无分类治理、无 linter/门禁。

### 2.4 关系词表现状

- 现有 14 词封闭（capability_graph.py:76-96）：`requires/produces/invokes` 已存在；**`consumes/depends/alternative` 缺席**；alternative 语义当前由 `fallback_to`（capability descriptor `fallback_capabilities` 声明，graph L508-509 建边）+ resolution `_fallback_alternatives`（capability_resolution.py:594-617，深度≤2）近似。
- 声明面即唯一真相：CapabilityDescriptor（pydantic，capability_registry.py:29，已有 `fallback_capabilities`/`incompatible_with` additive 先例）；图从 source registries 确定性构建（build_capability_graph:444）。

### 2.5 运行时事实现状

- worker/backend：`WorkerRegistry.list_active(profile, backend)`（workflow_runtime/cluster.py:223-283，心跳 TTL；查询失败降级「无 worker」）—— 资格面零消费。
- celery：`USE_REDIS` + `GIS_CELERY_REQUIRED`（registry.py:1739-1745）；投递失败回落 THREAD（`_CELERY_FALLBACK`）→ **ABI profile 的 `cancellation="durable"`（capability_resolution.py:626,655-659）不反映部署实况**（durable 标签诚实性缺口本体）。
- offline：无全局 flag；只有 QualificationContext.offline（传入才有）。
- `estimate_bridge.current_resource_pressure()` 已进排序（resolution 侧消费）。

### 2.6 denial evidence / reason codes 现状

- 可见：`TurnEvidence.add_capability_dispatch`（evidence.py:218-231，≤16 FIFO）→ turn summary；`ToolDispatchResult.capability_evidence`（refused/allowed 双面）；Pi details=to_details。
- 不可见：**dispatch 拒绝不产生 decision record**（`DECISION_KIND_CAPABILITY_DISPATCH_DENIAL` 词表 + replay metrics/drift 消费端已存在（metrics.py:124、drift.py:28），全仓无生产者）；链上无 denial 记录（decision_record.py 文档约定「不加新 Stage，拒绝 riding TOOL_CALLS 附加记录」）。
- reason codes 散落多体系（qualification check 词表、CAPABILITY_INELIGIBLE、registry 闸码、governor 码）—— 无 canonical 词表模块。

## 3. 五列表

### Overlap / 热区
见 §1 表。冲突规避策略：新逻辑全部进新模块；对 3 个热区文件只做与 #1503 改动区不重叠的最小 additive 编辑。

### Already Done（不重做）
- bind chokepoint + kill switch + fail-open + 双面 evidence（#1482）
- conformance 校验器 4 码 + manifest v4 折叠披露（#1482）
- registry 凭证/权限执行闸 + ContextVar CM（#1402）
- qualification 六面 + V1（offline/auth_tier/budget）+ DQH + dependency_available/credentials_present 字段
- capability ABI profile V2（derived 聚合：offline/凭证权限并集/cancellation 投影）
- decision_record 词表 + replay 消费端（#1486 线）

### Still Missing（本方向工作面）
1. dispatch 期 situation 生产构造器 + 全调用面统一供给（含与 planner 面等价性）
2. 凭证/权限 presence bridge（provider 抽象 + session→present_tool_credentials 布线）
3. 分歧治理：分类（合法多 provider / 元数据欠缺 / 真实错误）+ 报告 + linter 门禁
4. 关系词表 consumes/depends_on/alternative_to + 声明面 + resolution 消费
5. worker/backend/offline 等运行时事实 → 资格 + alternatives 解释；durable 标签诚实披露
6. denial decision record 生产者 + canonical reason codes 词表
7. extension/provider conformance fixtures + 真实 registry canary

### Must Not Touch
- `app/services/workflow_runtime/**`、`app/services/governor/**`（#1498/#1499/#1503 三方热区）
- `app/lib/runtime/{evidence,decision_record,gis_trace}.py`（#1503）
- ToolDispatchService 结构（方向红线）；manifest v4 结构（升级即全量 stale）
- frontend、扩展包认证管线（ADR-0201）

### Integration Seams
- `bind_tool_capability(situation=...)` 24 字段 dict 白名单 —— situation 供给的现成接缝
- `dispatch(situation=None)` chokepoint —— auto-supply 的单点
- registry ContextVar CM —— presence 授予的唯一事实通道
- `QualificationContext.dependency_available/credentials_present` —— 运行时事实注入口
- `emit_chain(Stage.TOOL_CALLS, decision=...)` —— denial 记录的约定承载（不新加 Stage）
- `compile_runtime_manifest(tool_registry=...)` —— fixture 注册表直接可喂

## 4. 已知 master 失败（与本分支无关，PR 时以 pristine 基线对照）

- `test_capability_graph_v8::test_ra3_cross_scope_model_no_false_duplicate`（顺序依赖 flake，#1482 PR 描述已登记）
- `test_workflow_guards` 3 项 + `test_workflow_v4_budget::test_corpus_evaluation_budget`（#1482 已实证 master 同样失败）

## 5. 架构决策摘要（详见 design 文档）

1. **chokepoint auto-supply**：dispatch 内部 situation=None 时由 `build_runtime_situation(session_id)` 供给（kill switch `GIS_SITUATION_SUPPLY` 默认 ON、fail-open、caller 显式 situation 优先并做 runtime 事实增强）——4 条调用面零编辑消除 bare context，未来调用方自动覆盖。
2. **presence-only 凭证桥**：`app/lib/tool_security.py`（纯契约 + provider）+ hotpath_convergence 接线 CM；secret 永不过桥，evidence/qualification 只见 id/bool/expiry/owner-scope。
3. **事实断言纪律**：只断言有可靠来源的事实（凭证 presence=provider、offline=显式 env、依赖可用性=worker/broker 探针）；无来源面（auth_tier/budget/quality_gate）保持 unknown —— 今日生产行为零变化，声明/配置到位后闸自动生效。
4. **治理不做删除**：分歧分类报告 + ratchet 门禁（suspected_misdeclaration 不增）。
5. **denial 记录不加 Stage**：riding TOOL_CALLS 附加记录（decision_record.py 既有约定），replay 消费端零改动。
