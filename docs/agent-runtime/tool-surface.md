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
