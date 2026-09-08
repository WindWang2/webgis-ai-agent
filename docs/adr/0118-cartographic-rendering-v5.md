# ADR 0118 — Professional Cartographic Rendering V5

日期：2026-09-09
状态：Proposed（随 feat/cartography-v5-rendering-engine 分支交付）
前置：ADR 0103（制图设计系统 V4，本 ADR 的空间由其非目标显式留白）、
ADR 0104-professional（Workbench V4，ExportChromeModel.degradations）、
ADR 0081（map product completion runtime / live↔export parity）、
ADR 0088（组件库 / MapSpec 唯一 desired 状态）

## 背景

Phase A 只读审计（证据：`.agent-work/cartography-v5/00-baseline.md`）确认
成图链存在四类地基问题，与 Epic "可出版、可验证、跨 live/PNG/PDF/SVG
语义一致" 目标直接冲突：

1. **用户意图可被静默改写**：`SetLayoutIntent(legend={"visible": False})`
   在 commit 前被 `carto.legend.completeness` 的 AUTO_SAFE 修复回路翻回
   True，工具仍报 success —— 显式关闭图例在含专题层的地图上不可能提交
   成功；且 legend/margins 整值替换会静默丢弃既有 position 等键。
2. **长标注静默溢出**：label engine（确定性求解器）无截断/换行/溢出
   诊断且生产零接线；python 孪生 SVG 与前端导出文本原样嵌入，200+ 字符
   标注完整出图。
3. **导出真相分裂**：SVG 导出是 PNG 位图包装（真矢量编译器是零调用孤儿
   代码）；PDF 双重标题 + CJK 文本乱码 + 失实的"矢量版"话术；export
   chrome 只读 committed spec 不合并 pending 投影（与 live 不同源）；
   孪生 SVG 不检查图层可见性；`thresholds.maxFeatures/timeoutMs` 声明
   于契约但零消费。
4. **降级披露无锚点**：前端 4 码降级词表 2 码无发射器、后端无对应物、
   不持久化 —— 导出降级证据只存在于一次对话系统消息里。

## 决策

### D1 — Render Diagnostics 权威词表（唯一真相 + catalog 投影）

`app/lib/cartography/render_diagnostics.py` 是渲染/导出降级诊断的**唯一
权威词表**（19 码，severity ∈ info/warning/error，中文 message 模板）。
经 `component-catalog.generated.json` 的 `renderDiagnostics` 段（schema
4→5）导出前端；前端 `ExportDegradation` 类型收窄为其子集，registry-parity
测试锁定。死码禁止：每个码必须被真实发射路径消费。诊断不是状态、不是
第二 spec —— 是导出产物证据。

### D2 — Layout intent user-wins（意图不可被静默改写）

`review_and_repair_cartography` 新增 `suppressed_repairs` 声明通道：
SetLayoutIntent 显式 `legend.visible=False` 时抑制
`set_map_legend_visibility` 自动修复 —— finding 照常进入 review 证据
（可读性顾虑诚实披露），但提交对象保持显式意图。`CartographicLoopResult`
新增 `suppressed_repairs` 字段留痕。legend/margins 改**字段级 merge**，
partial intent 不再丢键。附带修复：`apply_presentation_batch` 缓存驱逐
`popitem(arg)` TypeError（缓存满 256 即整批回滚）、
`webgis_layout_set.controls` 形参 Dict→List（与引擎/API/前端词表同形）。

被否决的替代：给 layout 塞 `_user_set` 内部印记（污染 spec、泄漏到前端）；
把该检查降级为 warning（所有场景失去自动修复，包括 agent 真忘开图例的
常见情形 —— 抑制是 per-mutation 的，检查语义不变）。

### D3 — Label 文本适配契约（长文本不再静默溢出）

`label_engine` 新增纯函数 `fit_label_text`（60 code point 截断 + 省略号，
`MAX_SVG_LABEL_CHARS=60`）与 `wrap_label_text`；solve 抑制词表新增
`label_too_long`。python 孪生 SVG 渲染器接入截断并发
`label_truncated` 诊断。TS 侧在 `buildVectorSvgExport` 后处理层做同口径
截断（不动 parity 锁定的编译器核心）。跨孪生长文本 parity 由共享
fixture（220 字符标注）锁定：两侧必须产出同一条 59 code point 前缀 +
"…"。

### D4 — 孪生 SVG 编译器正确性（可见性 + 阈值生效）

`compile_mapspec_to_svg_detailed` 返回 `SvgCompilation`（svg + diagnostics
+ feature_count），旧 str 签名逐字节兼容。隐藏层（`layout.visibility==
"none"` / 顶层 `visible:false`）不再进入导出；`thresholds.maxFeatures`
（默认 50000，保序截断 + `features_truncated`）与 `thresholds.timeoutMs`
（默认 30000，协作式中止 + `export_timeout_partial`）真实生效 —— 装饰性
契约字段转为执行语义。report 链编译调用包 `asyncio.wait_for` 有界化。

### D5 — 真矢量 SVG 导出复活（孤儿代码再接线）

数据层走既有孪生编译器 `compileMapSpecToSvg`（TS/Python parity 不动），
整饰层走既有 `svg-marginalia` 纯生成器，组装于新薄层
`vector-svg-export.ts`。编译/组装异常 → 位图包装回退 +
`vector_svg_fallback_raster`（warning）；成功路径 `basemap_omitted_vector_svg`
（info，矢量件不含栅格底图 —— 诚实披露，不虚构底图）。

### D6 — PDF 诚实化 + 诊断服务端锚点

PDF 单一标题事实源：ASCII 文本走 doc.text 真矢量（画布不再重复画）；
非 WinAnsi（CJK）文本随画布栅格化 + `pdf_text_rasterized_cjk`（info）
—— 不引入 CJK 字体资产（仓库无字体、下载超资源约束），但不再乱码、
不再失实宣称"矢量版"。诊断随成品上传：`POST /api/v1/export` 接受
`render_diagnostics` Form 字段，权威词表校验（外码拒绝）后持久化
`{filename}.diagnostics.json` sidecar；`GET /api/v1/export/diagnostics/
{filename}` 与 download 同源 fail-closed 所有权语义。

### D7 — 导出真相与 live 同源

exporter 经 `composeLiveMapSpec` 合并 pendingPresentation/pendingRemoved
（与 map-panel 同一函数），乐观可见性翻转期间导出与 live 同一时刻语义。
图例标题用 legend.title（与 live 同源）；死词表发射器补齐
（`chart_kind_unsupported_export` / `component_skipped_invalid`）；nodata
图例条目 live/export 两侧同语义（与 withNoDataGuard 同源颜色）。swipe
对比导出显式组合（第二画布按 position 裁剪 + 分界线 +
`comparison_export_composed`）或诚实披露（`comparison_second_view_not_exported`），
不再静默丢第二视图。

### D8 — 多帧导出运行时（atlas / small multiples 最小真实闭环）

`ExportRequest.frames/frameLayout` + `frame-composer.ts`：逐帧确定性执行
（保存 → apply → idle → 抓帧 → 恢复），pdf→pages（multipage）/
png→grid（拼板）。上限 50 帧（`atlas_page_limit_truncated`）、单帧失败
跳过继续（`atlas_page_skipped`）、cartogram 请求诚实降级
（`cartogram_unsupported`，按未变形几何渲染 —— 不伪造变形；
Gastner-Newman 算法仍属 planned，见 map-model-catalog 诚实登记）。

### D9 — 语义 scene oracle（可验证的 parity）

`describeRenderScene`：MapSpec → 规范化语义快照（可见性/组件经
resolveMapComponents 单点解析/图例族/label 通道/降级码），golden corpus
锁定投影口径；与 live 合成（pending 翻转）联动验证。语义 parity 断言
"什么在场、什么可见、披露什么"，不依赖像素与字体渲染。

## 后果

- 正向：两个 cartographic regression known gaps（legend visibility、
  long label）修复并转为 passing tests；live/PNG/PDF/SVG 有语义 parity
  oracle 与跨孪生 corpus；vector PDF/SVG 保留真矢量路径；atlas/small
  multiple 形成真实端到端导出；unsupported 特性显式降级；导出证据服务端
  可持久化。
- 中性：catalog schemaVersion 4→5（前端唯一断言点同步）；孪生编译器
  visibility/截断在渲染器内、TS 截断在后处理层 —— 位置不同、产物同径，
  由共享 fixture parity 锁定。
- 限制（诚实登记）：前端 'grid'/'pages' 多帧拼板当前为位图合成（不含
  矢量要素，发 `vector_svg_fallback_raster` 披露）；矢量 SVG 不含栅格
  底图；PDF 地图本体仍为位图画布；cartogram 算法未实现（能力门诚实
  降级）；`layout.legend.visible` 在前端 live 侧仍未消费（真实可见性
  语义是组件 enabled，后端契约字段保留待 Workbench V5 决策）。

## Follow-up（超出本 Epic）

- 后端全量 typed MapSpec pydantic 契约（dict 自由度是 layout drift 类
  缺陷的结构性温床，但改动面横跨全部 intent/API，需独立 Epic）。
- Gastner-Newman cartogram 算法 + 多画幅 MapSpec spec 级模型。
- MapLibre 原生 raster-dem hillshade 图层接线（ADR-0103 非目标的解除）。
- CJK 字体资产化（子集化内嵌）后 PDF 文本层全矢量。
- capture.mjs 视觉基线接入自动 image-diff 门禁（当前为语义 oracle +
  人工 diff 通道）。
