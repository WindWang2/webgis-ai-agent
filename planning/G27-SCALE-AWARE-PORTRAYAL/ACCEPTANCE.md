# G27 ACCEPTANCE — A01–A28 映射与实证状态

状态：`verified`（有本地实证） / `partial`（纯函数/契约层已证，真实渲染层未证） / `blocked`（外部条件阻断，未伪造）。

| 验收 | 状态 | 生产入口 | 测试 / 证据 |
|---|---|---|---|
| A01 旧项目默认渲染不变 | verified | `convert_analysis_to_mapspec_layer`（opt-in） | `test_g27_scale_portrayal_wiring.py::test_a01_*`；`test_scale_portrayal_v1.py::test_legacy_no_policy_project_empty`；PublicationIR 指纹不变测试 |
| A02 实际 runtime 消费策略 | partial | converter → `layer.label.zoomBands` → `normalizeLabelStrategy` → `buildTextSizeExpr`/`bandTopRatio`/`selectTopLabels`（`runtime.ts:1811,2157-2181` 既有真实链） | 接线测试 + 前端等价执行 23 checks；**真实浏览器渲染未跑（wasm 阻断）** |
| A03 backend/frontend 档位边界一致 | verified | 共享 `scale_portrayal_cases.json` | `test_scale_portrayal_corpus_parity.py` + `scale-portrayal.test.ts` + `g27-fe-check` 23 checks（逐 case） |
| A04 min/max 边界无闪烁/死带 | verified | `spec_label_zoom_bands()` 投影到 [0,24) | parity 测试「每个 zoom 恰落一档」+ 前端 4 档连续性 + `test_scale_portrayal_v1.py` 边界族 |
| A05 人工隐藏在所有 zoom 保持 | verified | `resolve_effective_scale_portrayal` precedence 1 | pytest 全 zoom 扫描 + 前端 z=0..24 每 0.5 扫描 |
| A06 锁定字号/pin 优先 | verified | precedence 2 | `pinned-size-wins` case + `test_scale_portrayal_v1.py` pin 族 |
| A07 未锁标注密度正确变化 | verified | `lod_for_zoom` + `build_label_zoom_bands` | 4 档单调不降 + 逐 case topRatio |
| A08 无效 policy typed 诊断 | verified | `validate_scale_policy` | corpus `unknown-*`/`invalid-*`/`fixed-density-missing-ratio` + service rejected 回执 |
| A09 缩放不产生每帧 mutation | verified（静态+设计） | runtime `zoomend` 只做跨档重算 + `webgis:zoom` 只读广播 | `runtime.ts:2153-2184`；UI 仅显式按钮走 REST；perf_probe `frame_mutations=0` |
| A10 快速缩放取消旧计算不串状态 | partial | UI epoch token + 跨档去抖；runtime 跨档幂等 | 前端 `scale-portrayal-panel.tsx` epoch 守卫；**浏览器快速手势实证 blocked** |
| A11 渲染抽稀不改分析 count | verified | `AggregationSemantics(visual_only)` 既有 + 固定 disclosure | `test_g27_component_portrayal.py` + perf_probe `feature_count_unchanged` |
| A12 legend 维持完整数据域 | verified | `component_portrayal_state` `full_domain` | 7 个 zoom 档位快照零变化 |
| A13 传输 LOD 不误作统计样本 | verified | 固定 disclosure（第 3/4 条） | `test_scale_portrayal_corpus_parity.py` + 前端常量断言 |
| A14 热力图 px/距离语义披露 | verified | 固定 disclosure（第 2 条）+ `unit` 词表校验 | `validate_scale_policy` unknown unit 诊断 |
| A15 DPI 改变不导致可见层变化 | verified | `physical_scale_context`（DPI 仅换算通道） | `test_g27_publication_scale_context.py::test_a15_*` + pytest dpi 72/300/600 |
| A16 不同 CRS/纬度不套错公式 | verified | `WEB_MERCATOR_CRS` 门 + 中心纬度 | `test_a16_*` + 纬度不同分母不同 + 组件 scale_bar 降级 |
| A17 模板选型与组件变体真实支持 | verified（静态） | `component_portrayal_state` 白名单核对，不发明 variant | `test_a17_unsupported_variant_is_disclosed_not_invented` |
| A18 组件布局不因档位切换重叠 | verified（静态） | 输出无 placement 变更、输入不被原地修改 | `test_a18_component_layout_rect_never_touched` |
| A19 局部 policy 支持 CAS/undo | verified（service 层） | `UpsertLayerIntent` + `apply_gis_mutation(expected_revision)` | `test_a19_a20_commit_is_local_with_cas_and_undo_chain` + superseded 回执 |
| A20 只改一个 layer 不全图重置 | verified（service 层） | 单 layer 深拷贝 + 命名空间子键 | 同上（intent 仅携带 L1，L2 不受影响） |
| A21 多 frame 各自等价 viewport | verified | `plan_scale_contexts` 逐页 bounds/viewport | `test_a21_each_frame_consumes_its_own_extent_and_viewport` |
| A22 Agent 读取真实 effective policy | verified（工具注册 + service） | `webgis_inspect_scale_portrayal` → `inspect_scale_portrayal` | `test_inspect_returns_bounded_real_effective_policy`；**Pi RPC 实跑 blocked** |
| A23 真实 Pi→UI→export 闭环 | **blocked** | 工具/API/UI/导出 seam 均已接 | 宿主 `ulimit -v` 4GiB 硬限使 Vite/wasm 工具链无法实例化；Pi 子进程 + stub LLM + 真实服务栈未启动，见 TEST_EVIDENCE |
| A24 解释量有界 | verified | `bounded_explanation` / `PublicationScaleContext` | parity + service + 组件 bounded 测试；payload ≤4096B |
| A25 大 label/点集测量无无限结构 | partial | `perf_probe.py` 三规模描述符 | 5000/20000/8000，峰值 18–34KB、每档 0.33ms；**真实浏览器帧时间 blocked** |
| A26 旧 schema/未知 schema 降级 | verified | `version` 缺省=旧项目、显式未知=typed 诊断 | corpus `legacy-no-policy` / `unknown-policy-version` + service rejected |
| A27 数据刷新时 policy 引用无幽灵字段 | verified | `available_fields` 有界证据 + ghost 诊断 | `ghost-priority-field` case + `test_ghost_field_reference_is_rejected` |
| A28 无消费者设置不显示已生效 | verified | `hasScalePortrayalConsumer` + 后端 `applied` 权威 | 前后端 A28 族测试；`applied=false` 时 spec 投影为 `{}` |

## 未完成 / 阻断（不冒充交付完成）

- **第 2 层验证（真实 Pi 子进程 + 本地 stub LLM + 真实工具/服务）未跑**：后端服务栈依赖（`pydantic_settings` 等）未在本方向最小 venv 安装；Pi vendor 依赖未装。工具已真实注册到 `ToolRegistry`（`app/tools/__init__.py`），service 与 REST 路径有隔离 fixture 测试，但**未声称 RPC 全链实证**。
- **第 3 层验证（真实浏览器 + 本地后端）未跑**：`ulimit -v` 硬限 4 GiB 导致 pnpm 多 worker / Vitest / Vite / Node amaro 的全部 wasm 工具链 `WebAssembly.instantiate(): Out of memory`。前端改用纯 JS `tsc` 转译 + Node 原生执行同一模块与同一 corpus（23 checks），但这是等价验证，**不是 Playwright 真实旅程**。
- A02/A10/A23/A25 因此为 partial / blocked；A17/A18 为静态实现验证，未接真实 renderer 变体渲染。
- 未合并、未改 master、未等线上 CI、未降低任何门禁。
