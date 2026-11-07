# RENDER_EXPORT_PARITY — 实时↔导出一致性矩阵(G04-CARTOGRAPHY-COMPOSER)

> 状态:**与代码同步**(2026-11-08)。本文是跨格式一致性的权威矩阵:每个关键图元给出
> live(浏览器)/Canvas PNG/SVG(真矢量)/矢量 PDF 四链的实现语义、机器测试与已知边界。
> "unsupported 应披露而非虚构"是贯穿原则 —— 每行的边界列即诚实披露面。

## 0. 四条链的事实

| 链 | 引擎 | 版面模型 | 文本 |
|---|---|---|---|
| live | MapLibre + React chrome(`frontend/components/map/map-components/`) | resolveMapComponents + composeMapLayout(v4 viewport) | DOM 文本 |
| Canvas PNG/SVG* | `frontend/lib/map-kit/exporter.ts`(canvas 合成) | spec chrome(anchor 七槽,与 live 共用解析层) | canvas 栅格(PDF 文本层除外) |
| 真矢量 SVG | `app/services/mapspec_to_svg.py`(Python) | PublicationIR + `_render_chrome_groups` | SVG 文本 |
| 矢量 PDF | `app/services/publication_export.py`(WeasyPrint) | PublicationIR 单一模型 + solve_layout_v5 | 可选取文本 |

\* 浏览器链的 "SVG" 是 PNG 嵌入容器(工具描述明示);真矢量 SVG 走后端 `mapspec_to_svg`。

## 1. 关键图元 parity 矩阵

| 图元 | live | Canvas | SVG | 矢量 PDF | 机器测试 | 边界 |
|---|---|---|---|---|---|---|
| **指北针方位** | `rotate(-bearing)`(north-arrow.tsx) | `bearing=-mapBearing`(exporter.ts) | `rotate(-bearing)`(svg_marginalia,组级含 N 字) | 继承 SVG 编译 | `test_export_constraint_chain_g04.py`(#2003 三链同语义) | bearing 缺省 0 输出字节不变 |
| **图例分类同步** | legend_spec 驱动 | 同源 chrome 模型 | `plan_legend_draws` 单源(绑定裁决唯一真相) | 同 SVG | `test_render_export_parity_g04.py::TestLegendClassificationSync`(breaks 变化不留旧分类) | 条目 >12 截断 + 披露 |
| **图例单位** | `单位：{unit}`(legendUnitSuffix) | 同 live | `单位：{unit}` 弱化行(G04 补齐;此前**丢弃**) | 同 SVG | 同上(unit 断言) | unit 缺席 → 行缺席,字节不变 |
| **长中文标题** | wrap-not-clip | 折行 + 副标题让位(ADR-0283 D2) | 折行 | PDF 文本层折行(#1975 评审修账) | `test_export_chrome.wrap` / publication_cases(36 结构 golden) | — |
| **约束版面** | 不适用(live 自由摆放) | `applyConstraintLayoutToSpec` 导出副本应用 zones(锁优先) | `prepare_publication_document` 克隆应用 | 同左(显式 page_profile) | `test_export_constraint_chain_g04.py` + `constraint-layout-apply.test.ts` | 屏幕档 not_evaluated;持久 MapSpec 零触碰 |
| **组件锁** | workbench 锁交互 | 导出副本锁实例跳过(FE 谓词) | — | `is_entity_locked` 族语义(#2002) | 同上两文件 | `__` 族双向命中 |
| **中文字体** | 系统/next 字体 | NotoSansSC 出版字体嵌入 | sans-serif + CJK 探测 | weasyprint CJK 内嵌/`pdf_font_fallback` 披露 | `test_vector_degradation_receipt` 等 | 缺字体 → 披露不阻塞 |
| **比例尺** | 投影感知(Web Mercator center-lat) | 同语义 | `meters_per_pixel` 同口径 | 同 SVG | `test_scale_*`(cartography) | 3D 透视 → `terrain_3d_scale_caveat` |
| **瓦片底图** | MapLibre 瓦片 | 画布栅格含瓦片 | **不含**(矢量)→ `basemap_omitted_vector_svg` 披露 | `raster_layer_unavailable_vector_pdf` 披露 | diagnostics 词表三方门 | 矢量链诚实省略栅格 |
| **统计图表** | ChartCore(联动选择) | chart ref 拉取,缺席披露 | svg_charts(kind 不支持 → `chart_kind_unsupported_export`) | 同 SVG | `test_export_chrome.parity` | 联动语义只在 live |

## 2. 一致性的机器保证结构

1. **词表三方门**(`app/lib/cartography/render_diagnostics.py`):每个诊断码必须注册 + 发射器真实存在 + 前端镜像(`ExportDegradationCode`)——死码即测试失败(`test_diagnostics_dead_code_gate`)。
2. **渲染支持矩阵**(`component_renderers._SUPPORT_MATRIX`):类型 × live/canvas/publication 三通道真值;descriptor 声明与矩阵互账,契约撒谎 = validate 失败。
3. **结构 golden**:36 条出版用例 × zh/en × 16:9/A4/A3(`tests/cartography/publication_cases/`)锁 SVG 结构;golden 链测试(golden_corpus + publication_ir + publication_cases)单次运行 646 用例全绿锁编译面。
4. **语义语料对账**(`export_semantic_corpus.expected_semantics`):SVG 实际渲染 vs 期望语义逐条比对(zoom 门/表达窗口/图例条目数)。
5. **像素 diff**(`golden_diff.py`):±16/通道、≥98% 像素通过 —— 只用于受控样本,不用于 live 页面批准(已知边界)。

## 3. 回执链(哪个格式在哪个文件留证据)

```
Canvas PNG/SVG/PDF(栅格)
  └ upload POST /api/v1/export(render_diagnostics Form)
     └ {filename}.diagnostics.json sidecar(词表校验;constraint_layout_evaluated 在此)
     └ session lineage: ref:export/* + export_receipts
矢量 PDF
  └ POST /api/v1/export/vector-pdf(page_profile 显式)
     └ 响应 constraint_layout 报告 + render_diagnostics + schema_disclosures
     └ {filename}.diagnostics.json sidecar(component_coverage/layout_version/指纹)
     └ lineage(layout_version/spec_fingerprint)
Atlas
  └ POST /api/v1/atlas/jobs → manifest_url + pdf_url(durable job)
```

锁定测试:`test_render_export_parity_g04.py::TestCanvasConstraintReceiptSidecar / TestVectorPdfConstraintReceipt`。

## 4. 已知边界(诚实披露,不虚构)

- 浏览器链 "SVG" 是 PNG 容器;真矢量仅后端链。
- 矢量链不含栅格底图(有披露码);统计图 kind 不受支持时省略(有披露码)。
- 像素 golden 不覆盖 live 页面视觉批准;全栈浏览器 E2E 属后续工作。
- 标注避让障碍仅 circle 层(#1975 限制 3)。
- `constraint_layout=not_evaluated` 的导出**不是**出版级成功(状态词 + reason 恒披露;屏幕档缺省即此态)。
