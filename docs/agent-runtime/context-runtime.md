# Context Runtime —— 预算管理 / 有界投影

## 预算管理（§22）

`app/services/chat/context_budget.py`。

现状痛点：历史有 6000 token 软预算，但 system 块 / 工具 schema / 项目块**无界**
叠加。预算器是**确定性规划 + 度量**（不截断 —— 截断权仍在既有组件）：

```python
plan = plan_budget(context_window=128_000, max_output_tokens=16_384)
# 保留输出 = max(max_output, window×25%) 且 ≤ window/2（大 max_output 不清零）
# 工具 schema 预算 = min(usable×35%, 12k) —— §22 红线：schema 永不吃满上下文

report = measure_assembled_context(messages, tools_payload,
                                   context_window=..., max_output_tokens=...)
report.over_budget / report.violations   # over_limit:tools:... / over_budget:total:...
report.warnings                          # near_budget:total:...（≥90%）
report.as_dict()                         # → ContextAssemblyResult.budget_report
```

- 分类（IntEnum 确定性优先级）：RESERVED_OUTPUT < SYSTEM_INSTRUCTIONS <
  USER_PROMPT < TOOL_SCHEMAS < SESSION_PLAN < HISTORY < HARNESS_EVIDENCE <
  MAP_STATE < LAYER_INVENTORY < PROJECT_CONTEXT < DATA_REFS < TRACE_SUMMARIES；
- 未知 context window → 保守按 8k 规划（宁可误报不漏报）；
- token 估算复用 history_compression 的 CJK-aware `_estimate_tokens`
  （单一估算语义）；
- `context_assembler.assemble()` 结果新增 `budget_report` 字段（加观测不加行为，
  超预算 warning 日志留痕）；
- `LLM_CONTEXT_WINDOW` settings（None = 未知）；按模型精细值走
  `MODEL_DESCRIPTORS_FILE`。

## 有界投影（§23/§24）

`app/services/chat/context_projections.py` —— 大真相反进 prompt 前的有界化：

- `bound_text` / `bound_lines`：确定性截断 + 省略计数披露（模型可感知被裁）；
- `project_layer_inventory` / `project_ref_list` / `project_tool_availability`；
- `content_fingerprint(obj)`：权威内容稳定指纹；
- `ProjectionCache`：`(namespace, key, fingerprint) → 投影文本` 有界缓存，
  真相未变（指纹不变）直接复用（§24「未变不重建」）；线程安全、实例隔离、
  TTL 兜底防伪指纹。

SessionPlan / 地图状态已有各自有界投影（`format_session_plan_projection` /
`build_map_state_summary`）—— 本模块不重写，不建平行持久化状态。
