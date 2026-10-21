# H02 RECON — 现场调查报告（Wave 0）

- Worktree: `../webgis-ai-agent-h02-cartography`，branch `feat/h02-cartographic-product-conformance-vnext`
- Baseline: `origin/master @ 0415ad8d3a03096ab405ece643753bc93974a760`（2026-10-20 fetch 确认最新）
- 调查方式：3 个只读侦察 agent（后端制图链 / 前端渲染导出 / 文档+测试基建）逐文件实码阅读，全部结论带 file:line 证据；GitHub PR/Issue 全量核对。

## 1. 上游语义与数据流（实码确认）

```
MapRequestIntent (app/services/gis_harness/intent.py:132-169)
  → MapProductPlanner.plan_from_intent (planner.py:591-639, selector 打分 :900-920)
  → finalize: composition_template_id 接线 (planner.py:1576-1596)
      → component_resolver.resolve (:1607-1613) → component_composer.compose (:1637-1645)
      → validate_component_composition (:1651-1665, error → _CompositionRejectedError → build_default_components 兜底+FallbackDecision)
  → MapProductPlan (template_id/map_layers/components/template_selection/manifest_fingerprint)
  → webgis_map_product 落盘 (tools.py:1014-1063) → mapspec_store.layout_set
  → 语义产品层（旁路 additive）: build_product_spec_from_plan (product_shapes.py:183-311)
      → compile_product_spec (product_compiler.py:170-351) → validate_product_completeness
```

**CompletionContract 在全仓不存在**（grep 证实）。H01 可依赖的既有名：`MapRequestIntent` / `MapProductPlan` / `ProductFacetContract`（product_facets.py，completion/inputs.py:151 消费）。H02 按任务书用旧版可运行 additive adapter，不等待 H01。

## 2. 29 个组合模板（运行时注册表实测，与 docs/cartography/composition-template-catalog.md 一致）

seed 8（composition_templates.py:58-228）+ 域包 21（composition_packs/，聚合序见 __init__.py:29-40）。全 ID 清单见文档；`load_builtins()` 撞 id 直接 ValueError（:241-245）。**不重复创建。**

## 3. 关键单源权威（已确认存在，禁止平行建设）

| 权威 | 位置 |
|---|---|
| MapSpec schema v1.6 | app/services/mapspec/schema/mapspec_schema.py:63-68 |
| 组件 ABI v1 | app/lib/cartography/component_abi.py（INSTANCE_ID_BY_TYPE :40-56；COMPONENT_ABI_META :181-246；fail-closed 三码 :410-436） |
| 表达协议 v1.6 | app/lib/cartography/representation.py（REPRESENTATION_MODES :64；MAX_WINDOWS=8 :98；user-wins pin :30-34） |
| RenderWork v1 | app/lib/cartography/render_work_envelope.py（render_work.v1 :44；work_id 确定性 :366-370；红线"不是第二 MapSpec" :17-30） |
| ACK v1 | app/lib/cartography/render_apply_ack.py（stale 判定 :171-173；reconcile_work_echo :253-300） |
| 状态词表 | semantic_checks.py:92（pass/fail/warning/not_evaluated）；ACK 词表 render_apply_ack.py:36-50；观察阶梯 observation_states.py:36-42；L1-L5 定义 docs/cartographic-closed-loop.md:11-18 |
| 前端镜像 | frontend/lib/render-protocol/（envelope+ACK+contract-manifest 三重锁）、lib/carto-ir/（visibility/representation/version/style-abi） |
| 图例单源 | frontend/lib/map-kit/legend-model.ts:62-153；比例尺 scale-math.ts |
| live/export 合成器同源 | composeExportSpec → composeLiveMapSpec（exporter.ts:1430-1455）；chrome 同 resolveMapComponents（export-chrome.ts:537+） |
| golden corpus | tests/cartography/golden_corpus/（610 文件，578 digest）+ carto_ir_corpus + render_work_corpus（12 场景） |
| parity oracle | frontend/lib/map-kit/render-scene.ts describeRenderScene + §13 十维矩阵 export-chrome.parity.test.ts:93-383 |

## 4. 导出链全景（真实支持范围）

| 格式 | 链路 | 入口 |
|---|---|---|
| PNG | live 画布高 DPI 截图 + chrome 合成 | POST /api/v1/export (map.py:249-330) |
| PDF（栅格） | jsPDF 嵌位图 + 文本层 | POST /api/v1/export/pdf (map.py:400-414) |
| SVG（前端矢量） | TS 孪生 compileMapSpecToSvg（与 Python 字节 parity 有测试锁） | exporter.ts:2221-2260（失败回退位图包装） |
| PDF（矢量） | 后端 WeasyPrint + compile_mapspec_to_svg_detailed | POST /api/v1/export/vector-pdf (map.py:418-470)，**前端未接线（契约披露 audit3 C-4/#1213）** |

## 5. 缺口清单（Wave 1 failing test 目标）

### 5a. H02 自有（无 issue 归属，实码+实测证实）

| # | 缺口 | 证据 | 影响 |
|---|---|---|---|
| G1 | `cartographic_fingerprint` 对 v1.6 `representation`/`visibility` 失明 | quality_loop.py:43-58 `_LAYER_METADATA_KEYS`；实测仅改这两字段的 spec 指纹逐位相等 | RenderWork work_id/C13 截图门/superseded 代守卫（lifecycle_engine.py:229,628-630,766,1206）对 representation-only 变异全部失明——语义缩放改了不算新代 |
| G2 | `representation_at_scale_report` 生产死路 | quality_loop.py:527 字段仅 review_cartography(:685) 计算；to_dict()(:533-547) 不含该键；主 mutation 路径 review_and_repair_cartography(:689-841) 不算；生产消费为零 | 语义缩放质量证据（任务 B/E 节）无处产出 |
| G3 | 组合模板身份不落 MapSpec：`MapProductSpec.composition_template_id` 是语义层披露字段，不自动写 `layout.composition` 身份块；唯一写点是契约工具 composition_contract.py:448 | product_shapes.py:183-311 | 终验/导出无法从物理 MapSpec 追溯产品模板身份 |
| G4 | 终验 required_slots 兜底 `[["title"],["scale_bar"]]` 弱于真实模板契约 | completion/inputs.py:165-182（chapter.template_selection.composition_template_id 缺席时） | 模板要求的必需组件（legend/north_arrow/chart…）终验不查 |
| G5 | 交付目标并集无冲突裁决：非 report_product 显式要 pdf/svg 时组合模板可能缺 export 槽，仅靠 warning/error 兜底 | product_shapes.py:277-289（并集）；planner.py:1577-1596（仅 report_product 重选） | 用户要 PDF 得到无 export_layout 的组合，降级披露链被动 |
| G6 | conformance_report 生产消费面单一（仅 webgis_apply_composition 预检） | composition_tools.py:292-303 | 产品编译/终验链不复跑 conformance，export_parity_gap 等 error 级检查绕过 |
| G7 | nodata guard 仅 live 生效：legend_spec.nodata 时 live 把 null 转无数据色，headless 不编码 → export 把 null 压进最低类 | frontend/lib/mapspec-runtime/thematic-paint.ts:20-24（模块自述）；#1726 未覆盖此项 | live↔export 着色语义分歧（科学制图正确性） |
| G8 | 客户端 SVG 导出不调 injectResolvedRefSources：非 HUD 挂靠 ref 层静默丢矢量要素 | mapspec-to-svg.ts:560-561（无 inlineData 即 return）；对比 map-panel.tsx:687 / publication-export.ts:136 | SVG 导出比 live 少层且无披露 |

### 5b. 他属 issue（H02 只建回归契约，不抢文件）

| Issue | 位置摘要 | H02 动作 |
|---|---|---|
| #1747 | heatmap 导出 paint 掉键（compiler.ts:1096-1098 vs runtime.ts:1352-1356）、fmtNum 无非有限守卫、reconciler 文档反、i18n `$chat`、SOURCE_TYPES、tablist | 新测试文件 xfail 契约（不动其文件） |
| #1726 | 主层型/门不同窗、SVG 孪生（TS+Python）不求值、reorder 反、过滤证据误报、图表高亮、SSE 无 catch | 同上；其中 Python 孪生 mapspec_to_svg.py:391-397 归 #1726 |
| #1713 | vlm/hybrid 跳 C13 门、approved 压 rejected、stale overlay 误判（provider.py:168-235 / repair_decisions.py:113-131 / map_verification.py:250-261） | 回归契约 + 引用，不改 |
| #1719 | 视口纬度 cos 修正反、normalize_windows 钳位复校缺 | 引用登记（cartography 域内但已有归属） |
| #1753/#1702/#1707 | 全量挂起 / 同名收集冲突 / p4_test 不收集 | 只跑定向 lane，BASELINE.md 实证披露 |
| #1695–#1701 | dependabot 依赖 PR | 完全不碰 |

### 5c. 已确认不存在（防重复建设）

- 无第二组件注册表/组合校验/publication 组件清单/状态词表（见 §3）。
- CompletionContract 不存在；MapProductPlan 已有 manifest_fingerprint。
- 模板选择/装配确定性已达成（纯函数 + memo 键含 registry 指纹 planner.py:941-948）。
- 导出降级披露机制已建（render_diagnostics 权威词表 + sidecar、chrome rendered/omitted 回执、export_partial_coverage warning）——缺的是消费端接线，不是披露本身。
- 观察新鲜度/幂等已建（C13 两道门 + ACK stale + 信封单调守卫）。

## 6. 本地验证命令（CI 同款）

- 后端制图 lane：`.venv/bin/python -m pytest -m cartography --no-cov --timeout=120 --timeout-method=thread -q`
- 覆盖率闸：`python scripts/coverage_cartography_gate.py`
- 后端定向：`pytest <path> --no-cov -q`（全量套件受 #1753 影响禁用）
- 前端：`pnpm run typecheck` / `pnpm run lint` / `pnpm run test`（vitest run）/ 定向 `pnpm exec vitest run <file>`
- golden 刷新：`GOLDEN_CORPUS_UPDATE=1 .venv/bin/python -m pytest tests/cartography/golden_corpus`
- 环境注意：系统 Python 3.14.7 超声明范围（#1731），worktree venv 用 uv 建 3.12 + `pip install -c requirements.lock -r requirements-dev.txt httpx psycopg2-binary`（CI 同款约束模式；`uv pip sync` 会剥 lock 未钉的传递依赖，勿用）
