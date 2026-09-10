# Cartography V6 — Architecture（Phase B 冻结）

前置：`.agent-work/cartography-v6/00-baseline.md`（Phase A 审计，file:line 证据）。
本 Epic 不另起第二平台；全部能力挂在既有五链之上。

## 1. 总体数据流（target state）

```
MapSpec(dict, version 1.0/1.1)
  → [冷路径] migrate+validate（mapspec_schema.py，Pydantic 权威；unknown fields 保留+结构化披露）
  → Canonical Render Scene（render_scene.py 纯投影；前端 describeRenderScene 同语义镜像）
      ├── layers（visibility 语义）  ├── components（resolve 单点）  ├── legends（derive_legend 唯一推导）
      ├── frames（spec 级 atlas）    └── label 通道
  → 消费方（同一 scene 语义，禁止各自重推导）：
      ├── 后端孪生 SVG 数据层（既有，byte-stable 入口保留）
      ├── 后端 publication SVG（新：孪生数据层 + svg_marginalia.py chrome 组，scene 驱动）
      ├── vector PDF（新：publication SVG ×frames → HTML → WeasyPrint，SSRF-deny fetcher，worker 线程+预算）
      ├── report_service（升级为 publication 路径，披露 publication_chrome）
      └── 前端 exporter（spec.frames 消费 + legend-model 单源 + label-solver TS 移植）
```

## 2. 权威与不变式

- **MapSpec desired-state 事实源不变**（lifecycle_engine/store 不动）；本 Epic 新增的是**契约层**
  （schema/迁移/canonical 序列化）与**产物层**（publication 编译），不在 mutation 热路径加解析（#1082 对齐）。
- **schema 唯一权威**：`app/lib/cartography/mapspec_schema.py`（Pydantic v2）。
  TS 侧核心文档类型由生成器投影，禁止手改生成文件（与 component-catalog 同纪律）。
- **unknown fields policy**：全模型 `extra="allow"`（保真 round-trip，绝不丢用户数据）；
  解析结果携带 `unknown_fields` 清单（结构化披露，不进导出诊断词表，避免噪声）。
- **version 策略**：已知版本 {1.0, 1.1}；1.1 = 纯 additive（frames、layout.labels.collision、组件 options 扩展）。
  迁移注册表 `MIGRATIONS`：1.0→1.1 补默认、无破坏；更新版本 → typed error（拒绝+说明，不静默）。
- **canonical serialization**：`canonicalize_mapspec(doc) -> dict`（schema 归一 + 保序；JSON dump 稳定）。
  v1.0 输入 canonicalize 后与输入语义等价（不发明字段）——向后兼容测试锁定。

## 3. Canonical Render Scene（跨语言 oracle）

- 后端 `app/lib/cartography/render_scene.py`：纯函数 MapSpec dict → `RenderScene` dataclass。
  组件解析 = 前端 `resolveMapComponents` 的忠实移植（排序/enabled/collapsed 语义一致）。
- **legend 单源**：`derive_legend_items(legend_spec)` 后端权威；前端 `frontend/lib/map-kit/legend-model.ts`
  deriveLegendModel 同语义；categorical/graduated/continuous/nodata 全覆盖。
  消费方收敛：render-scene.ts（oracle 替换 legendEntryCount）、vector-svg-export、svg-marginalia 调用方、
  export-chrome drawChromeLegend 条目计算。live DOM（legends.tsx slice(8) 是 live UX）保留，
  live↔export 差异仍由既有 legend_entries_truncated 披露。
- **共享 golden fixtures**：`tests/cartography/golden_corpus/render_scene/*.json` 同时被 pytest 与 vitest 消费
  （同文件双语言断言同 snapshot）——跨语言 parity oracle。

## 4. Publication SVG（后端 chrome / marginalia）

- 新模块 `app/lib/cartography/svg_marginalia.py`：确定性矢量片段（frame/neatline、north arrow、scale bar、
  legend box、title block、graticule、inset 框、attribution），几何与 TS `svg-marginalia.ts` 同形态，
  双侧 golden 结构测试锁定。
- `compile_publication_svg(spec, scene, *, frame, dpi) -> PublicationSvg`（svg+diagnostics+pages 元数据）。
  legacy `compile_mapspec_to_svg_detailed` 保持 byte-stable（chrome 关）；report_service 显式升级。
- 高级制图 native 范围（backend publication）：title/subtitle、legend（单源）、north_arrow、scale_bar
  （Mercator 纬度修正，center-lat 口径披露）、map_border/neatline、graticule（nice-step + 标注）、
  inset_map（范围框+主图指示）、attribution、categorical/continuous/nodata legend。
- planned 诚实登记（不伪实现）：cartogram（沿用 cartogram_unsupported）、terrain/hillshade 导出
  （沿用 terrain_3d_scale_caveat，live-only）、curved textPath 标签（文档 planned；本 Epic native 为 angled 放置）。

## 5. Label Engine V6

- solve_labels 接入 publication 路径（数据层 label bake）：确定性（排序优先级→贪心）；
  预算：MAX_LABELS_PER_EXPORT=400（超出省略+`label_budget_exceeded`）、协作超时检查、
  放宽路径发 `label_collision_relaxed`。
- opt-in 契约：`layout.labels.collision: "deterministic"`（v1.1 additive）；缺省走 legacy 无碰撞放置
  （byte parity 保住）。TS 侧 `frontend/lib/mapspec-compiler/label-solver.ts` 同算法移植；
  差分 oracle = 预生成 JSON 场景库（禁运行时 RNG），Python/TS 放置结果逐标签一致。
- CJK：estimate_label_box/wrap_label_text 既有 CJK 口径复用；长 CJK 导出回归测试。

## 6. Vector PDF（publication engine 输出端）

- 新服务 `app/services/publication_export.py` + 路由 `POST /api/v1/map/export/vector-pdf`
  （get_current_user + owner 记账 + MAX_EXPORT_SIZE 载荷上限 + worker 线程 + asyncio.wait_for 预算）。
- 流程：migrate/validate → scene → 逐 frame publication SVG → HTML（@page 尺寸=frame pageSize；
  font-family 系统栈）→ WeasyPrint write_pdf（url_fetcher 全拒 → SSRF/外联安全）。
- **可选文本**：全部文字为 SVG <text>/HTML 文本 → PDF 矢量字（系统 Noto CJK 可选可检索）；
  字体探测（fontconfig）缺失 → `pdf_font_fallback` info。
- raster 层：服务端可解析的 raster cursor → 以 <image> 位图嵌入（诚实位图）+ `raster_layer_in_vector_output` info；
  不可解析 → 省略 + `raster_layer_unavailable_vector_pdf` warning。weasyprint 缺失 → 结构化错误 +
  前端回退既有栅格导出 + `vector_pdf_unavailable` warning。weasyprint 进 requirements.txt。
- 诊断词表新增 6 码（全部带真实发射器+死码门测试更新）：
  raster_layer_in_vector_output / raster_layer_unavailable_vector_pdf / pdf_font_fallback /
  label_collision_relaxed / label_budget_exceeded / vector_pdf_unavailable。

## 7. Spec 级 frames / atlas（v1.1）

- `spec.frames?: MapSpecFrame[]`（≤50，超出迁移期截断+披露）：{id?, title?, view?, layerOverrides?,
  pageSize?, enabled?}。
- 后端：publication 编译逐帧 → vector PDF 多页；帧失败 → `atlas_page_skipped` 继续；
  全局 deadline（timeoutMs × min(frames,50) 且全局上限）+ 每帧编译预算。
- 前端：exporter `req.frames ?? spec.frames`；frame-composer 复用；不保留 canvas 快照（逐帧顺序处理）。
- 内存：帧顺序处理、scene 一次构建轻量派生（view override 浅投影），无全 spec 重复 parse。

## 8. 安全边界

- 新增文本（标题/图例标签/标注/graticule 标注）一律走既有转义链（_escape_svg_attr/escapeSvgText）。
- WeasyPrint `url_fetcher` 拒绝全部远程/本地文件协议（防 SSRF/任意读）。
- 端点鉴权与既有 export 同级；载荷/帧数/raster dataUrl 字节上限；服务端生成文件名（防注入/穿越）。
- unknown fields 不进入任何渲染 eval 路径（renderer 只消费 typed 字段）。

## 9. 兼容与 rollout

- legacy 入口 byte-stable；schema v1.0 往返等价；types.ts 导出面不变（内部 re-export generated）。
- report_service 升级 publication（视觉 delta 有意变化）：CHANGELOG + report 元数据 publication_chrome 标志披露。
- 前端 svg 导出语义不变；label collision 为显式 opt-in。
- 无 DB migration（零 Alembic）；无 MapSpec 存储格式破坏（1.1 纯 additive）。

## 10. 资源包络（structural budget）

| 项 | 上限 | 披露 |
|---|---|---|
| 单帧要素 | 沿用 thresholds.maxFeatures（默认 50000） | features_truncated |
| 标签数 | 400/帧 | label_budget_exceeded |
| 诊断条数 | 64/导出（既有） | — |
| frames | 50 | atlas_page_limit_truncated |
| raster 嵌入 | ≤8MB/帧（超限降采样/省略+披露） | raster_layer_in_vector_output |
| PDF 页数 | ≤50 | atlas_page_limit_truncated |
| 编译预算 | timeoutMs 协作超时（既有口径） | export_timeout_partial |

- 字体探测进程级缓存（key=需求族，值=可用性，TTL=进程生命周期，容量=1 组布尔）。
- 性能证据 = 结构断言（caps 生效、元素数上界、页数上界）+ 既有 20k 编译 smoke 模式，不做脆弱 wall-clock 门禁。

## 11. Test oracle

- 共享 fixture 双语言 parity（render_scene、legend、label solver 场景库）。
- twin byte-parity（legacy 路径既有闸不回退）+ chrome 结构 golden。
- vector PDF 断言：文本可提取（pdfminer/pypdf 若可用，否则 WeasyPrint 产物结构 + 文本层 smoke）、页数、诊断。
  （pypdf 可用性实现期验证；不可用则用 weasyprint 自身文本工具/降级为结构断言，不虚称验证。）

## 12. ADR / 文档

- ADR-0120（master 现有至 0118；0119 被 4 个 open PR 并发 claim，本 Epic 取 0120 避让）。
- docs/cartography/renderer-parity-matrix.md 与 catalog 若权威输入变化则再生成；CHANGELOG 最小追加。

## 13. Revision R1（Subagent-A 架构挑战修订，2026-09-10）

挑战结论：conditional GO。以下修订并入冻结架构（覆盖前文冲突表述）：

- **R1-C1（预算机制）**：编译预算=协作式 deadline（monotonic deadline 句柄注入编译循环，
  扩展 mapspec_to_svg.py:406,564,661 既有检查点），**禁止** wait_for(to_thread) 当预算。
  WeasyPrint write_pdf：进程级互斥（threading.Lock，report_service 与新端点共用）+
  专用容量 1 执行器（queue maxsize=1，饱和→结构化 429 `vector_pdf_busy` 诚实拒绝）；
  wait_for 仅做调用方返回时效保护（超时返回错误，孤儿线程自然结束被丢弃，输入已协作式有界）。
- **R1-C2（strict schema）**：全部 schema 模型 `ConfigDict(strict=True)`（inlineData 等 Any 点除外）；
  known 字段类型不符 → 结构化 `invalid_fields` 披露（不静默 coerce）。新增**脏值兼容 corpus**：
  "2000"/"false"/"1.5" 等脏输入在 legacy raw-dict 路径与 publication schema 路径下渲染语义一致
  （锁：thresholds 字符串仍被忽略、layer.visible:"false" 仍渲染）。
- **R1-M1（canonical 合同）**：canonicalize = strict 校验 + **输入 dict 保序拷贝**（非 model_dump）；
  golden：canonicalize(原始) 的 json.dumps == 原始 dumps（corpus 全量）。
- **R1-M2（死码门）**：Wave 1 先建发射器注册表（render_diagnostics.py 内 `EMITTER_REGISTRY`:
  code→发射模块符号），契约测试断言词表⊆注册表且模块可导入；新增码必须同时注册+探针测试。
- **R1-M3（诊断保留槽）**：publication 多帧路径每帧独立子 cap（8 条/帧）+ 全局 cap 64；
  丢弃发生时发 meta 码 `diagnostics_truncated`（第 7 新码，真实发射器）。
- **R1-M4（frames 写入方）**：frames 挂 **`layout.frames`**（layout 配置面，既有 SetLayoutIntent
  字段级 merge 通道可写，**零 lifecycle_engine 改动**）；MapSpecFrame→ExportFrame 显式适配层
  `frontend/lib/map-kit/spec-frames.ts`（view→extent 由 web-mercator 数学换算；不可映射字段诚实降级+诊断）。
  后端直接消费 view 原生语义。
- **R1-M5（layerOverrides 合并）**：逐键 deep-merge（只覆盖出现的键），语义测试锁定"未提及键保留"。
- **R1-M6（WeasyPrint 串行）**：见 R1-C1；并发 smoke 测试（两线程同时 write_pdf 走锁）。
- **R1-M7（legend 收敛验收）**：deriveLegendModel 以**逐调用点行为等价**为验收标准；
  交付"六点语义 diff 表"（render-scene oracle / compiler extract / export-chrome / vector-svg /
  svg-marginalia / live DOM——每处差异保留或有意变更+闸更新清单）。
- **R1-M8（TS 生成范围）**：generated 只覆盖 schema-backed 核心文档类型（Spec/Source/Layer/Layout/
  Frame/Component）；permissive 工具型（StyleMethod 开放联合、index-signature paint/layout、
  inlineData any、legend_spec 旁路）保留手写+契约测试对齐；re-export 面逐名断言。
- **R1-M9（raster/tile 嵌入）**：publication 编译嵌入前解析——raster cursor→会话 raster store
  取位图降采样 dataUrl（≤8MB）；不可解析 raster cursor 与 tile 模板源 → 省略 +
  `raster_layer_unavailable_vector_pdf` warning（detail 指名层，覆盖两类源）。
- **R1-Min1（标签抑制披露）**：collision 开启时聚合 solution.warnings → `label_collision_relaxed`
  （detail=抑制/位移计数）；前端 label-solver 严格 opt-in（默认路径不动，长标签 parity 闸保住）。
- **R1-Min2（report 闸清单）**：publication 路径保留 `mapspec-vector-layers` 组标记、
  超时占位语义（export_timeout_partial 词汇）、既有断言串（'r="25"'、#de2d26 等数据层输出不动）。
- **R1-Min3（unknown_fields 消费方）**：vector-pdf 响应 payload 元数据 + report 元数据 + export
  sidecar 三处消费。
- **R1-N1**：字体探测缓存 = 固定键集 {"cjk","latin_sans"} 布尔值；共享 fixtures 落
  tests/cartography/golden_corpus/（vitest 相对导入验证于 W-tests，不可行则移 tests/fixtures/ 并双跑）。
