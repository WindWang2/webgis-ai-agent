# AC-V11 导出格式能力矩阵（W6.4，ADR-0166）

> 任务书 W6.4：「多格式交付：SVG / PDF / PNG / GeoTIFF（栅格）/ 打印档
> （CMYK + 出血，V10 已有 color_mode，此处补齐端到端）」。逐格式如实登记
> 实现路径与缺口 —— 已实现/部分/缺口三态，不虚报。

| 格式 | 实现路径 | 状态 | 备注 |
|---|---|---|---|
| **SVG（矢量）** | `frontend/lib/map-kit/vector-svg-export.ts`（`buildVectorSvgExport`：C2 版面 IR + `mapspec-to-svg` 编译；退化层 `layer_approximated_*` 诚实标注 + `data-export-content="mixed"`） | ✅ 端到端 | 栅格底图省略并披露（`basemap_omitted_vector_svg`，矢量语义边界） |
| **PDF（图体矢量）** | 前端：`pdf-vector.ts`（svg2pdf.js 路径嵌入，W6.2 新增）+ 栅格兜底 + 模式回执；服务端：`publication_export.py`（WeasyPrint 矢量全链路，既有） | ✅ 端到端（W6.2 补齐前端矢量通道） | 文字层保持 CJK 字体嵌入（V10 P3） |
| **PNG（位图）** | `exporter.ts` canvas 合成 + `highdpi.ts` DPR 重渲染 | ✅ 端到端 | 高 DPI 栅格限制披露（见 highdpi 评估） |
| **GeoTIFF（栅格）** | 服务端 rasterio 通道（`raster_cartography_converter` 一族提供 nodata/CRS/统计；GeoTIFF 写盘由数据管道既有能力承担） | ⚠️ 部分：分析产物通道已具备；**导出菜单未接线** | 缺口登记：导出 UI 的 GeoTIFF 选项 + 投影元数据断言（W8 批次） |
| **打印档（CMYK + 出血）** | `layout_description.py`（colorMode cmyk → bleedMm=3 + cropMarks）+ `exporter.ts`（bleed 版式数学，ADR-0157 P5）+ PDF 矢量体兼容 | ⚠️ 部分：PDF/PNG 走通；**端到端打印核验（CMYK 落色/裁切角线物理正确）无自动化** | 缺口登记：打印核验属 W8 验证矩阵的 4 输出形态之一 |
| **批量队列** | `app/services/export_batch_queue.py`（W6.5：串行恒 1 + 重试 + 断点续传 + fail-soft） | ✅ 服务面 | 路由/UI 接线归后续批次（诚实登记） |
| **可访问性** | `accessibility_manifest.py`（W6.6：alt/layerLabels/色盲声明/来源/投影，100% 断言） | ✅ 服务面 | 导出物随件携带的接线（PNG 元数据侧车/PDF 元数据）归后续批次 |

## 缺口清单（移交）

1. GeoTIFF 导出菜单接线 + 投影元数据断言（W8 矩阵的 4 形态之一）。
2. 打印档自动化核验（CMYK 落色/角线）——W8。
3. `vectorSvg` 的产生端接线：导出命令在 SVG 可用时把 `buildVectorSvgExport`
   SVG 传给 PDF 路径（`options.vectorSvg` 已就位；命令层接线一批次完成）。
4. 批量队列的路由暴露；可访问性清单随件携带。
