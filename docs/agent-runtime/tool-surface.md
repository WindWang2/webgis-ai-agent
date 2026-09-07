# ToolSurface V2 —— 统一投影 / 确定性检索 / Schema 压缩

## 投影

`app/services/tool_surface_v2.py` · `ToolSurfaceProjector(registry, catalog)`。

ToolSurface 是**投影**不是注册中心：唯一输入是 ToolRegistry 真相与既有
ToolCatalog 选择语义（tier1 恒发 / tier2 关键词∪计划域∪sticky / 24KB 预算 /
`list_available_tools` 自救 —— 全部保留），输出：

```python
proj = projector.project(SurfaceRequest(
    user_message="成都的泰森多边形分析",
    session_id=..., declared_domains=..., turn_id=..., surface=harness_surface,
    compress="none",          # none|compact|minimal
    retrieval=True, retrieval_k=6,
))
proj.schemas        # 模型可见 schema（registry 序 + 检索补强追加）
proj.reasons        # name -> [tier1|domain:*|surface_preferred|retrieval(score,matched)]
proj.dropped        # name -> lifecycle:*|budget
proj.bytes_used / proj.fingerprint
```

### 消费方

- **legacy 引擎**：`execution_engine._select_tools` 经 `_augment_tool_surface`
  additive 后处理（仅真实 ToolCatalog；subagent `_FrozenCatalog` 白名单是权威
  边界，检索绝不越权）。异常 → 既有行为不变。
- **Pi 适配层**：`native_surface_snapshot(names)` 用同一投影栈产出原生面
  schema（仍从活注册表 dump，绝不手写第二清单）。
- **评测**：`reasons`/`dropped` 是召回率/泄漏率的断言面。

### 检索补强（§13）

`app/services/chat/tool_retrieval.py` —— 零网络、零嵌入、纯确定性：

- 语料 = descriptor 的 name/domains/tags/capabilities/algorithms/description；
- CJK bigram + ASCII 词切词；名字命中 > tags > domains > capability > 描述；
- 索引按 `registry_fingerprint()` 缓存（注册表变化自动失效）；
- 只在预算空隙补工具（≤ retrieval_k），**tier-3 永不因检索进入可见面**；
- tie-break 按名升序 —— 同输入必同输出。

## Schema 压缩（§14）

`app/services/chat/schema_compression.py`：**不隐藏校验性约束**
（required/类型/enum 语义保留），压缩文本与噪音：

- `compact`（默认档）：描述 → summary（有界），参数描述有界化，长 enum 截断
  展示 + 计数提示（registry 校验仍按完整 enum —— 校验模型与展示 schema 本就分离），
  剥离 pydantic 噪音（$defs/additionalProperties/format/title）；
- `minimal`：再剥参数 default/description（subagent 等角色视图）；
- `compression_report(schemas)` 输出前后字节（观测/评测）。

引擎默认 `TOOL_SURFACE_COMPRESS=none`（回归零风险）；compact 经投影 API 或
环境变量启用。

## 基准（§36 抽样）

`tests/unit/test_tool_surface_v2.py::test_tool_selection_golden_benchmark`：
golden 用例（意图 → 必须可达 / 禁止泄漏），recall ≥ 0.99 门 + 零禁止工具泄漏 +
tier-3 零泄漏。全量语料见 [trace-replay.md](trace-replay.md)。

## V3 动态面（ADR-0103）

`app/services/chat/tool_surface_v3.py` · `DynamicToolSurface(registry)` ——
完整选择管线（V2 是既有 catalog 结果的 additive 补强；V3 从上下文出发独立
投影）：

```text
ToolSelectionContext（user_message / task_type / workflow_stage /
    active_capabilities / declared_domains / data_profile_domains /
    map_state_summary / role / k_min / k_max / byte_budget）
  → capability 检索（AlgorithmRegistry.capability_tool_map 反查直入候选
    + 词法 rank_tools，capability 命中加权）
  → contract 过滤（lifecycle 非模型可见 / tier≥3 或
    effective_security_tier≥3 / 角色副作用策略）
  → rank & select（10-30 个，DEFAULT_K_MIN/K_MAX；分数降序 tie-break 按名
    —— 同输入必同输出）
  → 压缩投影（compress_schema + byte_budget + 指纹）
```

- 核心前门 `CORE_TOOL_NAMES`（webgis_map_intent / webgis_map_product /
  webgis_cartography_status / list_available_tools）无论检索结果如何在面
  （仍过 contract 过滤）；
- k_min 是提示不是配额：检索不中不强行凑数 —— 模型保有
  `list_available_tools` 两跳通道；选择全量可解释（reasons / dropped /
  retriever / selection_trace 进 trace 与评测断言面）；
- tier-3 / destructive 永不经本管线进入模型可见面。

### 可插拔语义检索

`TOOL_RETRIEVAL_SEMANTIC="module:callable"`（签名
`(registry, query, top_k) -> Sequence[RetrievalHit]`）注入语义检索；未设置 /
加载失败 / 调用异常一律降级词法 baseline（词法永远先跑，投影绝不失败）。
实际服务方记录在 `SurfaceSelection.retriever`（`lexical` /
`semantic:<spec>`）。

### 角色副作用策略

`ROLE_SIDE_EFFECT_POLICY`：corpus_worker / doc_crosscheck /
descriptor_enrichment / static_analysis 四个高吞吐角色按白名单过滤突变类
工具（static_analysis 只留 pure / deterministic_compute；corpus_worker 另放
行 artifact_creation）。未列角色全部允许（tier-3 仍被闸拦）；命中过滤记
`role_policy:<role>:<side_effect>`。

### Pi 动态注册面

`app/services/chat/pi_native_surface.py`：

- **spawn dump v2**（`pi_surface_for_spawn` → `write_surface_file` 原子写、
  fail-fast）：`{version: 2, tools, default_active, execute_proxy}`。tools =
  注册超集（`registered_surface_names`：全部 model-visible、非 tier-3、非
  external_unavailable；native 7 保持完整 schema，长尾注册 dormant 压缩
  schema）；`default_active` 恒为冻结 native 面（Phase 1 兼容）。
- **per-turn 激活**：`bind_turn_prompt`（`app/services/chat/pi_turn_context.py`）
  经 `compute_turn_active_tools`（V3 selector；恒含 native 7、绝不含 tier-3，
  失败/关闭 → 空串不注入）在用户消息与 turn marker 之间追加
  `[WEBGIS_ACTIVE_TOOLS:[...]]` marker（必须保持最后）；扩展在
  `before_agent_start` 扫描并 `pi.setActiveTools`（vendor 语义：下个 agent
  turn 生效 —— 零 vendor 修改）。`PI_DYNAMIC_TOOL_SURFACE=0` 整体退回冻结
  7 + proxy。
- **直呼分类**：`resolve_pi_tool_call(..., registered_surface=...)` 把注册
  面上的裸名调用分类为 `execute` —— 与 `webgis_execute(inner)` 同一条
  ToolDispatchService 管线（ToolRegistry 仍是唯一执行真相，Pi 面只是投影，
  dispatch 侧 registry 存在性/tier/确认闸不变）；面外裸名照旧 reject 并指向
  `list_available_tools`。

测试锚点：`tests/unit/test_tool_surface_v3.py`、
`tests/unit/test_pi_dynamic_surface.py`。
