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
