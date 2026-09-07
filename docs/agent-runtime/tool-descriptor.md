# ToolDescriptor V2 —— 描述符 / 生命周期 / 指纹 / 归一化 / 结果契约

## 描述符

`app/tools/descriptor.py` · 构造入口 `ToolRegistry.descriptor(name)`。

描述符是**派生只读投影**：注册元数据（`registry._metadata`）+ args model +
schema 三者同源，`register()` 是唯一写入点。派生字段（destructive_level /
requires_confirmation / retry_safe / cacheable / replay_safe）不参与指纹。

### 注册期字段（全部可选；缺省派生 —— 存量工具零改动）

```python
@registry.tool(
    name="buffer_analysis",
    description="…",
    tier=2, domains=["spatial"], cost="medium", execution_policy="thread",
    # ── V2 描述符扩展 ──
    status="stable",                # stable|experimental|deprecated|hidden|external_unavailable|planned
    summary="缓冲区分析（模型可见短摘要，Surface 压缩优先用）",
    side_effect="deterministic_compute",   # 见下表
    capabilities=["cap.buffer"],    # 引用 Capability Registry id —— 不复制语义
    algorithms=["alg.buffer"],      # 引用 Algorithm Registry id
    tags=["缓冲", "buffer"],        # 检索词
    output_semantic_type="geojson_fc",
    produced_refs=["data"], accepts_ref_types=["data"],
    network=False, deterministic=True,
    result_size_policy="ref_offload",
    requires_credentials=("amap",),
    provider_dependencies=("amap",),
)
```

### 副作用类 → 安全推断

| side_effect | retry_safe | cacheable | replay_safe |
|---|---|---|---|
| pure / deterministic_compute / cacheable_read | ✅ | ✅ | ✅ |
| state_mutation / artifact_creation | ❌ | ❌ | ✅ |
| external_side_effect / destructive | ❌ | ❌ | ❌ |
| unclassified（缺省） | — | — | ✅ |

**tier ≥ 3 强制 `side_effect=destructive`** —— 漏标永远不能把危险工具洗成纯读。

### 生命周期

- `stable` 默认；`experimental` 可见可执行；
- `deprecated` 可见可执行，必须 `deprecation_of=<canonical>`（别名语义单一实现，
  模型可见清单优先 canonical）；
- `hidden` / `planned` 永不进模型可见清单；`planned` 在 dispatch 层被拒
  （`TOOL_NOT_EXECUTABLE`）；
- `external_unavailable` 投影期按可用性隐藏，恢复自动回归。

### 注册期校验门（违反即 ValueError —— 启动失败）

未知 kwarg、非法 status/side_effect/result_size_policy、deprecated 缺
deprecation_of、自引用 deprecation_of、重复/非法列表字段、summary > 600 字符。
既有 cost/execution_policy 校验同门。**拼写错误从此显式失败，不再静默吞掉。**

## 指纹（§9）

canonical JSON（键排序、紧凑、ASCII）+ SHA-256 截 16 hex：

- `registry.schema_fingerprint(name)` —— 只含 function 名 + parameters。
  **描述文案变更不影响它。**
- `registry.descriptor_fingerprint(name)` —— 契约字段 + schema 指纹。
  描述变更 → 此变彼不变；入参 schema 变更 → 两者都变。
- `registry.registry_fingerprint()` —— 全库顺序无关指纹（Surface 投影缓存、
  检索索引、replay 兼容判定的失效键；register/update_args_model 自动失效）。
- `manifest_fingerprint(entries)` —— (name, schema_fp) 集合，顺序无关。

## 参数归一化（§10）

`app/tools/argument_normalization.py` —— 声明式规则表（从 registry 旧 if 链
逐字迁移，行为契约由 `tests/test_tool_argument_aliases.py` +
`tests/unit/test_argument_normalization.py` 双面钉住）。

- `TOOL_NAME_ALIASES`：入向工具名别名（dispatch 首步折叠 canonical）。
- `GEOJSON_ALIASES` + `GEOJSON_FAMILY_TOOLS`：geojson 家族（model 声明
  `geojson` 字段或工具在显式名单内时适用）。
- `FIELD_RULES_BY_TOOL`：每工具字段折叠（`FieldAliasRule`，支持 list_wrap、
  requires_target_declared）与取值矫正（`ValueCoercionRule`，如 n_classes 数值）。

不变式：**声明字段/保护性 ref 游标永不被折叠**；无语义猜测（单位/坐标系/
数值含义永不动）；确定性（声明序展开）；有界（每规则至多折叠一个值）。
修复证据 `ArgRepair` 经 `normalization_report_var` ContextVar 暴露，pipeline
写入 trace。一致性门（测试）：别名不得是声明字段（漂移即失败）、别名不得
指向未注册工具。

## 结果契约视图（§11）

`app/lib/runtime/result_contract.py` —— 不改任何工具的返回，只提供只读适配：

```python
view = inspect_tool_result(result)   # ToolResultView
view.ok / view.semantic_type / view.error_code / view.ref_ids
view.contract_key()                  # replay 的形状级比较键
bounded_summary(result, max_chars)   # trace/debug 摘要
```

兼容既有约定：std_error_response 形状、#529/#589 错误家族、data 包裹 FC、
`ref:` 前缀、warnings 列表。估算走 `app.lib.json_size` 预算化遍历（永不 O(巨载荷)）。

## V3 契约扩展（ADR-0103）

`app/tools/descriptor.py` —— 新增可选契约字段，全部缺省 unknown/None/空
（存量工具零改动）；注册期校验覆盖每个词表字段（非法值 ValueError 即启动
失败），未知 kwarg 依旧硬失败。

### 新字段清单

| 字段 | 词表 / 语义 |
|---|---|
| `input_artifacts` | 消费的 artifact 类型（对齐 capability 词表） |
| `required_context` | `REQUIRED_CONTEXT_KINDS`：map_state / session_plan / data_profile / ref_cursor / project_memory / credentials / uploaded_data / cartography_state |
| `map_mutations` | `MAP_MUTATION_KINDS`：add_layer / remove_layer / style_layer / camera / marker / component / map_product / annotation / theme / filter |
| `data_mutations` | `DATA_MUTATION_KINDS`：upload / cache_write / project_memory_write / artifact_write / external_write / session_state |
| `latency_class` | `LATENCY_CLASSES`：fast \| medium \| slow |
| `memory_class` | `MEMORY_CLASSES`：light \| medium \| heavy |
| `scale_class` | `SCALE_CLASSES`：small \| medium \| large（适用数据规模） |
| `crs_semantics` | CRS 语义（"wgs84" / "gcj02" / "crs_agnostic" / …） |
| `unit_semantics` | 单位语义（"meters" / "degrees" / "ratio" / …） |
| `idempotent` | None = 由 side_effect 派生（`effective_idempotent`） |
| `security_tier` | None = tier（`effective_security_tier`；动态面过滤按生效值） |
| `required_permission` | e.g. "admin" / "tier3_confirm" / "bridge_secret" |
| `examples` / `anti_examples` | 典型正确调用意图 / 已知误用模式（negative retrieval 证据） |
| `failure_modes` | 失败类型词（failure taxonomy 自由词表） |
| `fallback_tool` | 失败时建议替代工具（canonical 名） |

### capability 溯源与派生回填

`capability_source ∈ CAPABILITY_SOURCES`（`none` | `declared` |
`derived:algorithm_registry`）—— 声明与派生永不相混。工具未声明
capabilities 时，`ToolRegistry.descriptor()`（`app/tools/registry.py`）从
AlgorithmRegistry 反查索引回填：既有 `tool_to_capability` + 新增
`tool_to_algorithms`（`app/lib/gis/algorithm_registry.py`，注册表静态后按
内容缓存、register 失效），并盖 `capability_source="derived:algorithm_registry"`
溯源戳。回填只是引用既有语义真相 —— 不建第二 capability 注册表，也不编造。

### 覆盖率 gate

`scripts/check_tool_descriptor_coverage.py` 对活注册表出 per-module /
per-field 覆盖率报告（`--json` 机器可读、`--gate` 阈值闸），并作 pytest
红线（`tests/unit/test_descriptor_coverage_gate.py`）。闸含派生一致性检查：
AlgorithmRegistry `tool_candidates` 已声明的工具，描述符 capabilities 必须非空
—— 派生链路不允许静默失联。capabilities 语义是「分析能力归属」（词表 88 项
分析/数据访问 capability），天然不覆盖地图操作/meta/编目类工具 —— 诚实策略
是全局地板，靠编造 capability id 冲高被禁止。

### 富化现状（231 tools）

- side_effect / tags：100%；
- latency_class / memory_class / output_semantic_type / result_size_policy：
  99.6%（230/231）；
- deterministic：96%（9 个本地/在线混合路径工具诚实留空）；
- capabilities：54.98%（127，声明 + 派生合计）。

留空是被允许且被预期的状态；gate 钉住的是「不再退化」。

测试锚点：`tests/unit/test_tool_descriptor_v3.py`、
`tests/unit/test_descriptor_coverage_gate.py`。
