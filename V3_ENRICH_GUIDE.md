# Tool Descriptor V3 富化指南（内部工作文件，勿提交）

目标：给 `/home/kevin/projects/webgis/webgis-ai-agent/.worktrees/pi-gis-runtime-v3` 下的活工具注册调用补充**真实**描述符元数据（ADR-0103）。

## 修改方式

每个工具的注册调用（`@registry.tool(name=..., tier=..., domains=...)` 装饰器或 `tool(registry, name=...)` helper）追加关键字参数。**只加 kwargs，不改函数体、签名、docstring、schema。**

## 字段决策规则（必须先读该工具函数代码再判定）

```python
# 1. side_effect（必填判断，一词定妥当）：
#   DETERMINISTIC_COMPUTE  确定性本地算法（几何/统计/插值，同输入同输出）
#   PURE                   纯会话内读（查目录/查状态/读配置，无重计算）
#   CACHEABLE_READ         外部/网络读（geocode、OSM、overpass、在线底图元数据）
#   STATE_MUTATION         改地图/会话状态（display_layer, add_marker, fly_to, apply_layer_style, map_intent...）
#   ARTIFACT_CREATION      产出文件/报告/图表/导出（report, export, chart, generate_*）
#   EXTERNAL_SIDE_EFFECT   改外部系统状态（极少；仅当真有外部写）
#   DESTRUCTIVE            tier-3 注册期自动强制，无需手写
#   UNCLASSIFIED           真无法判定才留（不要偷懒用它）
# 2. deterministic：True=本地闭式算法；False=LLM 调用/外部时间戳/网页实时；不确定就别写。
# 3. network：True=函数内做 HTTP；False=纯本地；不确定别写。
# 4. latency_class：fast（轻查询/小几何）、medium（常规分析）、slow（raster/KDE/子代理/重聚合）。
#    有 cost=heavy 的工具 slow / cost=light 的 fast 是合理默认，但以代码实感为准。
# 5. memory_class：light / medium / heavy（与大 GeoJSON/raster 全量加载对应 heavy）。
# 6. scale_class：small（点级/单要素）、medium（街区/区县）、large（全市/栅格/大数据集）。
# 7. tags：3-8 个检索关键词，中英混合小写（如 "密度,density,热力,heatmap,poi"）。写用户会说的话，别写营销词。
# 8. output_semantic_type：geojson_fc | map_product | chart | report | stats | table | text | bool | ref | component | list
# 9. result_size_policy：inline_small（小 JSON 直接返回）、bounded（有界列表/分页）、ref_offload（大 GeoJSON 走 ref 游标）。
#    判定看返回：返回 ref cursor / geojson_ref → ref_offload；返回计数/摘要 → bounded；返回小对象 → inline_small。
# 10. capabilities：**默认不写**（派生回填已从 algorithm registry 反查）。
#     仅当派生明显缺失且 app/lib/gis/capabilities/*.py 有准确 capability id 时声明。
# 11. required_context / map_mutations / data_mutations：仅对有副作用工具写。
#     required_context 词表: map_state session_plan data_profile ref_cursor project_memory credentials uploaded_data cartography_state
#     map_mutations 词表: add_layer remove_layer style_layer camera marker component map_product annotation theme filter
#     data_mutations 词表: upload cache_write project_memory_write artifact_write external_write session_state
# 12. examples / anti_examples：只给 tier-1 与高频核心工具（每个 1-2 条，自然语言用户意图，中文为主）。
# 13. failure_modes：1-3 个，从 timeout empty_result network_error invalid_crs missing_data ambiguous_intent rate_limit invalid_args partial_coverage memory 里选代码中真实可见的。
# 14. crs_semantics：wgs84 | gcj02 | cgcs2000 | crs_agnostic | auto_project（代码可见才写）；
#     unit_semantics：meters | square_meters | degrees | ratio | count | percent（清晰才写）。
# 15. fallback_tool：仅当有真正语义相近的替代 canonical 工具名时写。
# 16. summary：tier-1 工具可补 ≤600 字符短摘要（模型可见，schema 压缩时用）。
```

## 红线

- 不确定的字段**留空**（默认 unknown/None/()），绝不猜测填充。
- 不动 `app/tools/registry.py`、`app/tools/descriptor.py`、其他批次的模块文件。
- 不重构 import、不改格式化风格；kwargs 对齐现有风格。
- 完成后必须运行并通过：
  1. `python -c "from app.tools import init_tools; from app.tools.registry import ToolRegistry; r=ToolRegistry(); init_tools(r); print('tools:', len(r.all_metadata()))"`（能注册、数量不减）
  2. `python -m pytest tests/unit/test_tool_descriptor_v2.py tests/unit/test_tool_descriptor_v3.py tests/unit/test_capability_registry_parity.py --no-cov -q`

## 覆盖目标

你批次的每个工具至少落：side_effect、tags、latency_class、memory_class、deterministic（能判定时）、output_semantic_type、result_size_policy。
