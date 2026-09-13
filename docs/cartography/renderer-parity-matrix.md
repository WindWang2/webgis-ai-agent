# Renderer Parity Matrix

> 真值：`component_renderers.py` 单一权威矩阵；本文件是生成视图。
> 空单元格是**声明的缺口**（诚实登记），不是豁免掩盖。

| type | live | png | pdf | svg | print | 说明 |
|---|---|---|---|---|---|---|
| annotation | ✓ | ✓ | ✓ | ✓ | — | 终审 F1：annotation 导出走 drawChromeAnnotation（文本注释卡）（native） |
| attribution | ✓ | ✓ | ✓ | ✓ | — | ADR-0081：导出读 spec attribution 组件（请求 author 仍在 metadata 行）（native） |
| basemap | — | — | — | — | — | 类型占位：底图由 map-panel 底图逻辑承接，非 chrome 渲染（（union）） |
| categorical_legend | ✓ | ✓ | ✓ | ✓ | — | 同 legend（native） |
| chart_panel | ✓ | ✓ | ✓ | ✓ | — | ADR-0081：canvas 导出绘制静态图表（与 live 同一数据协议 chart/chartRef）（native） |
| continuous_colorbar | ✓ | ✓ | ✓ | ✓ | — | ADR-0081：导出绘制渐变 ramp + min/max/unit（与 live colorbar 同形态）（native） |
| decision_panel | ✓ | ✓ | ✓ | ✓ | — | V3：决策面板（候选排名 + 权重来源 + 硬约束否决）（native） |
| export_layout | — | ✓ | ✓ | — | — | exporter 读取 paperSize/orientation/dpi 版面参数（native） |
| graticule | ✓ | ✓ | ✓ | ✓ | — | P3：live 经纬网渲染器落地（#1089 deferred 补齐）—— 与导出侧 _drawGraticules 共享 graticule-math 间隔/吸附语义（live SVG overlay，真实 bounds 比例定位）（native） |
| inset_map | ✓ | ✓ | ✓ | ✓ | — | v2 P1 全链路：live 轻量静态 SVG 投影（不 mount 第二个 maplibre runtime）+ 导出 drawChromeInset 同链（native） |
| label_layer | — | — | — | — | — | labels render via MapSpec layer.label sublayer (runtime/compiler/SVG); component is the binding+strategy surface（native） |
| legend | ✓ | ✓ | ✓ | ✓ | — | ADR-0081：spec 组件在场时导出读组件（enabled/layerId/anchor），HUD 发现仅兜底（native） |
| map_border | ✓ | ✓ | ✓ | ✓ | — | P6：全链路落地 —— live CSS 图框渲染器（map-border.tsx）+ 导出 strokeRect（drawChromeMapBorder），三变体两侧同语义（native） |
| methodology_note | ✓ | ✓ | ✓ | ✓ | — | V3：方法论披露随产品渲染（稳定警告码 + 文案）（native） |
| north_arrow | ✓ | ✓ | ✓ | ✓ | — | exporter 读取 enabled 开关（native） |
| scale_bar | ✓ | ✓ | ✓ | ✓ | — | 同 north_arrow（enabled 开关）（native） |
| statistics_panel | ✓ | ✓ | ✓ | ✓ | — | ADR-0081：canvas 导出绘制统计卡（placement 感知（native） |
| subtitle | ✓ | ✓ | ✓ | ✓ | — | ADR-0081：exporter 经共享 resolveMapComponents 读 subtitle 组件（canvas 与 PDF 文本层同链）（native） |
| table_panel | ✓ | ✓ | ✓ | ✓ | — | Runtime V4：交互表格面板（虚拟化 + 跨视图 SelectionContext 联动）（native） |
| title | ✓ | ✓ | ✓ | ✓ | — | exporter runExport 读取 options.text 绘制画布标题（native） |
| uncertainty_panel | ✓ | ✓ | ✓ | ✓ | — | V3：不确定性披露（区间/置信度/样本限制）live + canvas 导出同链（native） |

已知限制（诚实登记，ADR-0101 D6）：
- `table_panel` 仅 interactive —— 工作区交互面不是制图产物面（Runtime V4 产品决策）。
- 报告 vector SVG（python 孪生 mapspec_to_svg）不含 chrome marginalia；
  画布导出（png/pdf/svg-包装）的 chrome 由 exporter/composeLayout 承载。
- `basemap` 是类型占位（底图逻辑在 map-panel，非 chrome 渲染）。

