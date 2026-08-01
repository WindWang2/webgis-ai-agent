# 架构深化评审 — 实施方案

**生成日期**: 2026-08-01
**评审范围**: Candidates #1–#7（`/improve-codebase-architecture` 报告）
**状态**: 实施方案文档，非代码。每个候选项记录：调查结论、决策树、实施步骤、当前状态。

> 本文档是「方案」而非「报告」。对已实施的候选项，记录实际做法（回溯）；对未实施或前提被推翻的，记录原提案 + 调查结论 + 应采取的方案（前瞻）。所有 ADR 已落盘（ADR-0013/0014/0015）。

---

## 总览

| # | 候选项 | 强度 | 调查结论 | 当前状态 |
|---|--------|------|---------|---------|
| 1 | 删除 SpatialAnalyzer 死分发 seam | Strong | 前提成立 | ✅ 已实施 (`3d19bcd`) |
| 2 | coord transform 适配器委托 CRS 归一化 | Strong | 前提成立 | ✅ 已实施 (`697317f`) |
| 3 | process_layer_ingestion 纯度 | Worth exploring | **前提被推翻**（已纯） | ⚠️ 部分（补测试，`ed3c966`） |
| 4 | 三个 bbox walker 收敛 | Strong | 前提成立 | ✅ 已实施 (`df61c2c`) |
| 5 | attach_legend_spec 发射点 | Worth exploring | **前提被推翻**（emitter 已顶层） | ⚠️ 部分（删死分支，`cd9d57b`） |
| 6 | dispatch 路径绕过 ToolDispatchService | Worth exploring | **前提被推翻**（非 ADR-0006 泄露） | ❌ 未实施（仅 ADR-0014） |
| 7 | 删除 sse_helpers 死 shim | Speculative | 前提成立 | ✅ 已实施 (`3e60222`) |

**关键模式**: 7 个中 5 个前提成立（4 完整实施 + 1 部分实施）；2 个前提被调查推翻（#3/#5 顺带做相关清理；#6 仅记 ADR）。grilling 的深入调查让基于过期/不准确前提的候选项免于不必要改动。

---

## Candidate #1 — 删除 SpatialAnalyzer 死分发 seam

**状态**: ✅ 已实施 (`3d19bcd`) + ADR-0013

### 调查结论
`execute()` / `execute_analysis()` / `OPERATOR_MAP` / `ANALYSIS_OPERATORS` 四个符号**零生产调用方**——所有空间工具直接调具体方法（`.buffer()`/`.overlay()`/`.statistics()` 等）。且 `execute()` 与 `execute_analysis()` 的 `parameters`/`input_data` 参数顺序对调，是潜在 footgun。

### 决策树
1. **删除范围** → 全删四个符号（具体方法成为唯一接口）
2. **测试处理** → 迁移 dispatch 测试到具体方法；删除 unknown_operation 测试（行为无生产者）
3. **`__all__`** → `["SpatialAnalyzer", "AnalysisResult"]`
4. **ADR** → 记录 ADR-0013（防未来重提「加动态分发 seam」）

### 实施步骤
1. 删除 `app/services/spatial_analyzer.py`: `OPERATOR_MAP`(L71-87)、`execute()`(L89-127)、`ANALYSIS_OPERATORS`(L338)、`execute_analysis()`(L341-353)
2. `__all__` 收敛为 `["SpatialAnalyzer", "AnalysisResult"]`；类 docstring 加注指向 ADR-0013
3. `tests/unit/test_spatial_analyzer_module.py`: `test_spatial_analyzer_execute_dynamic_dispatch` → `test_spatial_analyzer_buffer_concrete_method`（直接调 `.buffer()`）；删除 `test_spatial_analyzer_unknown_operation`
4. 写 `docs/adr/0013-delete-spatial-analyzer-dispatch-seam.md`

### 关联修复（评审修复提交 `2416656`）
- `AnalysisResult` 从 `class(GeoAnalysisResult)` 子类（唯一方法是恒等委托 `from_geo`）扁平化为类型别名 `AnalysisResult = GeoAnalysisResult`。这是 ADR-0009「不引入 AnalysisResult 接口」决策的落地，借评审修复提交一并完成（非 #1 原定的 seam 删除工作）。

### 实际改动
3 文件，+76/−92。测试：53 passed。

---

## Candidate #2 — coord transform 适配器委托 CRS 归一化

**状态**: ✅ 已实施 (`697317f`)

### 调查结论
适配器重复拥有深模块的 CRS 校验：`_SUPPORTED_CHINESE` 常量复制 `_CHINESE_CRS`；内联 `.lower().replace("-","").replace(" ","")` 复制 `_normalize_chinese_crs`。Candidate #1 目标是「薄适配器委托而非重新实现」，但适配器仍重新实现了校验。

**关键约束**：适配器的 Chinese-only 策略门是**合法的**——`transform_geojson` 是多态的（Chinese **或** EPSG），删除门会让 LLM 调 `transform_coordinates(from="wgs84", to="EPSG:32650")` 被静默当作 EPSG 重投影，破坏工具契约。

### 决策树
1. **深化形状** → 公开 `_normalize_chinese_crs` 为 `normalize_chinese_crs`；适配器删 `_SUPPORTED_CHINESE` + 内联归一化，改调它做策略门（None → 拒绝）
2. **EPSG 归一化** → 不动（`.strip().upper()` 是琐碎字符串清洗，非会员协议，符合 ADR-0008 立场）

### 实施步骤
1. `app/utils/coord_transform.py`: `_normalize_chinese_crs` → `normalize_chinese_crs`（公开 + docstring）；更新内部 2 处调用
2. `app/tools/coord_transform.py`: 删 `_SUPPORTED_CHINESE`；import + 调用 `normalize_chinese_crs`；保留 Chinese-only 策略门（None → `std_error_response`）
3. `tests/unit/test_coord_transform_module.py`: 加 `test_normalize_chinese_crs`（规范化解析 + None 拒绝路径）

### 关联修复（评审修复提交 `2416656`）
- coord 适配器 6 处错误响应统一改写为 `std_error_response(...)`。**注意：错误契约统一由 Invariant #4 / 提交 `731fc9e`（Candidate #5 的 `ToolRegistry` 封装）驱动，`2416656` 将其落地到 coord 适配器，非 #2 原定工作。** #2 的原定工作仅是 CRS 归一化委托。

### 实际改动
3 文件，+37/−10。测试：22 passed。

---

## Candidate #3 — process_layer_ingestion 纯度

**状态**: ⚠️ 部分（补纯度回归测试，`ed3c966`）。原提案（重构 pipeline/store 边界）**未实施**——调查推翻前提。

### 调查结论（推翻前提）
评审声称 pipeline「通过别名修改 `mapspec["sources"]`」（sources 被修改、view 被返回的不对称）。**代码调查否定**：pipeline 在 L78 已做 `dict(existing_entry)` 浅拷贝，所有写入（`store_data`、`profile`）落在拷贝上，不写回 `mapspec`。直接测试验证：调用前后 `mapspec` 深拷贝完全不变。**pipeline 已经是纯的**，store 已是唯一写入者——这恰是评审建议的理想状态。

### 决策树
1. **处理方式** → 前提不成立，不重构。但补纯度回归测试锁定不变性，防未来回归

### 已实施步骤（补测试）
1. `app/services/mapspec_layer_pipeline.py`: docstring 明确声明纯度契约（只读 `mapspec["sources"]` 种子化现有键，永不写回）
2. `tests/unit/test_mapspec_layer_pipeline.py`: 加 `test_process_layer_ingestion_does_not_mutate_mapspec`（断言调用前后深拷贝不变 + 返回的 source_entry 是拷贝非别名）

### 未实施（原提案，被推翻）
原提案「让 pipeline 返回 (source_entry, suggested_view)、store 做 ALL writes」**不需要**——pipeline 已是纯的。仅补测试锁定。

### 实际改动
2 文件，+47。测试：31 passed。

---

## Candidate #4 — 三个 bbox walker 收敛

**状态**: ✅ 已实施 (`df61c2c`)

### 调查结论
三个独立递归坐标 walker 做同一件事，在承重方式上分歧：
- `calculate_bbox`(dispatch service): 仅走 `features[].geometry`，bare Feature 返回 None
- `_extract_bbox_and_geometries`(profiler): 空输入返回 `[0,0,0,0]`，其 center `[0,0]`(Null Island) 被 MapSpecStore auto-view 注入捕获
- `geojson_bbox`(utils): 最正确（Feature/Geometry/Collection + bbox 短路）但仅 1 个工具用

### 决策树
1. **收敛范围** → 全删两个窄实现，三处调用点改调 `geojson_bbox`；profiler 的 geom_types 拆为内联收集
2. **geom_types 拆分** → profiler 内联 ~5 行（单一消费者 = 假想 seam，不抽工具函数）
3. **空 bbox 契约** → `geojson_bbox` 返回 None（空源）时 profiler 不设 `suggestedView`（空 dict），修 Null Island bug
4. **符号处理** → 全删 `calculate_bbox` + `_extract_bbox_and_geometries`；更新测试

### 实施步骤
1. `app/utils/geojson.py`: 深化 `geojson_bbox` 为真正超集——结构化遍历 features 列表与 geometry（不依赖严格 type 标签），覆盖松散 `{"features":[...]}` 和未标记 Feature
2. `app/services/tool_dispatch_service.py`: 删 `calculate_bbox`；`slim_event_result` 改调 `geojson_bbox`
3. `app/services/spatial_meta_profiler.py`: 删 `_extract_bbox_and_geometries`；bbox 改调 `geojson_bbox`；geom_types 内联收集；修 Null Island bug（None → `suggestedView: {}`）
4. `app/services/chat_engine.py`: 删未用的 `_calculate_bbox` 别名 import
5. `app/services/chat/sse_helpers.py`: 删 `calculate_bbox` re-export
6. `tests/unit/test_chat_helpers.py`: `TestCalculateBbox` → `TestGeojsonBbox`（加 bare Feature 覆盖）；删 `_calculate_bbox` 别名断言

### 关联修复（评审修复提交 `2416656`）
- `mapspec_store.py` 的 `layer_upsert` 返回值从原始 `layer` 改为 `processed_layer`。这是 #4 提取 `LayerIngestionPipeline` 时引入的回归——返回原始输入而非管线产物会让调用方拿到未处理（未入库、未 profile）的图层。评审发现并随 `2416656` 修复。

### 实施中发现
`geojson_bbox` 原本比 `calculate_bbox` 更严格（依赖 type 标签），无法处理调用方实际传入的松散字典。深化它成为真正超集（结构化遍历），这才是「收敛到正确版本」。

### 实际改动
6 文件，+50/−96。测试：58 passed。

---

## Candidate #5 — attach_legend_spec 发射点

**状态**: ⚠️ 部分（删 converter 死分支，`cd9d57b`）+ ADR-0015。原提案（引入 helper）**未实施**——调查推翻前提。

### 调查结论（推翻前提）
评审声称 5 个 emitter 有 2 个挂载位置（4 顶层 + `heatmap_data` 嵌在 data 内），converter 的 double-lookup 是承重分支。**追踪每个 emitter 的 payload 形状经 `is_analysis_result` 后否定**：

1. **所有 5 个 emitter 实际都已顶层挂载**——`heatmap_data` 的 `data["legend_spec"]` 看似「在 data 内」，只是因为 `data` 本身就是返回的 FeatureCollection。测试 `test_heatmap_native.py:63-64` 直接读 `result["legend_spec"]`。
2. **converter 的 data 内 lookup 分支是不可达死代码**——它的逻辑目标（`heatmap_data`）输出 FeatureCollection 形状，`is_analysis_result` 明确「GeoJSON wins」→ 对 FC 返回 False → converter 永不到达。能到达的 3 个 emitter（h3_binning/apply_template/create_thematic_map）全是顶层。

### 决策树
1. **ADR-0009 重开** → 重开（触发条件 #2），但 normalize 后发现分歧不存在
2. **helper 位置** → 原定 `app/tools/_utils.py`（避免 tools→services 反向依赖）——**作废**，无分歧可统一
3. **converter** → 删死分支

### 已实施步骤（删死分支）
1. `app/services/analysis_cartography_converter.py`: 删 `data` 内 lookup 死分支（L367-368），保留单一顶层读取 + 注释指向 ADR-0015
2. 写 `docs/adr/0015-no-attach-legend-spec-helper-emitters-already-top-level.md`

### 未实施（原提案，被推翻）
原提案「引入 `attach_legend_spec(payload, spec)` helper，5 个 emitter 路由经过它」**不需要**——所有 emitter 已顶层挂载，引入 helper 会是推测性泛化。

### 实际改动
2 文件，+78/−2。测试：43 passed。

---

## Candidate #6 — dispatch 路径绕过 ToolDispatchService

**状态**: ❌ 未实施（仅 ADR-0014，零代码改动）。原提案被调查推翻。

### 调查结论（推翻前提）
评审声称 `plan_mode` 和 `/tools/execute` 直接调 `registry.dispatch` 是「ADR-0006 泄露」。**代码调查否定**：

1. **接口根本不匹配**：`ToolDispatchService.dispatch` 需要 OpenAI `tc` dict + `executed_tools` set（agent-loop 专属契约），两个调用方都没有——它们持有 `(tool_name, args_dict)`。
2. **6 项横切职责中只有 2 项共享**：执行（#2）和错误形状（#3 半），且已由 `registry.dispatch` 统一拥有（错误形状在 Candidate #5 统一为 `std_error_response`）。其余 4 项（ref_id Fetch-on-Demand、WS 广播、event_log、LLM slimming）是 agent-loop UI 专属——`plan_mode` 需内联结果、`/tools/execute` 需原始 HTTP 返回。
3. **非 ADR-0006 泄露**：ADR-0006 统一的是**两条 agent 路径**（ChatEngine + Pi bridge）。`plan_mode`/`/tools/execute` 不是 agent 路径。混淆「agent dispatch」与「任何工具执行」是过度泛化。

### 决策树
1. **处理方式** → 记 ADR-0014 关闭，防未来重提

### 已实施步骤（仅 ADR）
1. 写 `docs/adr/0014-keep-non-agent-dispatch-paths-on-registry.md`

### 未实施（原提案，被推翻）
原提案「强制两个 bypass 路径经由 ToolDispatchService」**不需要**——会要求改 service 接口（加不需 tc/executed_tools 的入口点）或加丢弃 4/6 横切的适配层，无收益。

### 重开触发条件
非 agent 调用方需要某个 agent-loop 横切职责（如 `plan_mode` 需 `ref_id` 处理大输出）时，提取该职责到更窄的 seam，而非强推整个 agent-loop 契约。

---

## Candidate #7 — 删除 sse_helpers 死 shim

**状态**: ✅ 已实施 (`3e60222`)

### 调查结论
`sse_helpers.py` 自述「已废弃」，纯 re-export shim（接口即实现），零生产消费方。仅 2 个测试引用它，其中 `test_slim_tool_result_upgrade.py` **预存损坏**（import `_infer_simple_type`，该符号全仓不存在）。

### 决策树
1. **损坏测试处理** → 修复（`_infer_simple_type` 的行为后继是 `utils/geojson.py` 的 `infer_field_type`，同 null/bool/number/string/array/object 分桶）+ 迁移到真实模块

### 实施步骤
1. 删除 `app/services/chat/sse_helpers.py`
2. `tests/unit/test_chat_helpers.py`: 8 符号从 shim 迁移到真实模块（`tool_dispatch_service` + `llm_client`）
3. `tests/test_slim_tool_result_upgrade.py`: 修复损坏（`_infer_simple_type` → `infer_field_type`）；truncate 系列从 `tool_dispatch_service` 导入
4. `app/services/chat/__init__.py`: docstring 移除 `sse_helpers` 条目

### 实施中发现
`test_slim_tool_result_upgrade.py` 引用的 `_infer_simple_type` 全仓不存在（预存损坏，import 即崩）。验证行为后继 `infer_field_type` 完全匹配，顺带修复。

### 实际改动
4 文件，+28/−50（含 1 文件删除）。测试：48 passed。

---

## 累计改动统计

8 个提交（含本轮开始的审查修复 `2416656`），`master` 领先 `origin/master` 8 个提交。

**新增 ADR**:
- ADR-0013: 删除 SpatialAnalyzer 动态分发 seam
- ADR-0014: 非 agent dispatch 路径保留在 registry（ToolDispatchService 仅 agent-loop）
- ADR-0015: 不引入 attach_legend_spec helper（emitter 已顶层）

**净代码影响**（估算）: 约 −100 行死代码删除 + 3 个 bug 修复（Null Island、layer_upsert 回归、code-less 错误字典）+ 多处深化（CRS 归一化集中、bbox 收敛）。

---

## 后续建议

1. **推送**: `git push origin master`（8 个提交待推送）
2. **次级问题**（审查中发现但未在本轮处理）:
   - `execute` 与 `execute_analysis` 参数顺序不一致 —— **已随 #1 删除解决**
   - CRS 归一化逻辑在 adapter 与 deep module 间重复 —— **已随 #2 解决**
3. **环境问题**（预存，非本轮引入）: `test_tool_dispatch_service.py` / `test_mapspec_layer_pipeline.py` 在会话期间因 Nominatim DNS 解析变化触发 SSRF 配置校验错误，无法 pytest 收集。建议固定 `.env` 中的 `NOMINATIM_URL` 或在 CI 环境预置。
