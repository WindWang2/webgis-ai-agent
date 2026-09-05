# Component & Variant Catalog

> 由 registry 生成；真值：`component_registry.py` + `component_templates.py`。

共 19 个组件类型 / 65 个变体模板。

| type | 类目 | variants | 默认 | 卡数 | 位置 | 绑定 | 状态 |
|---|---|---|---|---|---|---|---|
| annotation | annotation.text | text, callout, group | text | multiple | top-left | — | native |
| attribution | annotation.attribution | default, compact | default | single | bottom-left | — | native |
| categorical_legend | legend.categorical | academic, compact, report, horizontal | academic | multiple | bottom-left | layerId | native |
| chart_panel | analysis.chart_panel | default, compact, transparent, report | default | multiple | top-left | — | native |
| continuous_colorbar | legend.continuous_colorbar | horizontal, vertical, slim, scientific, stepped | horizontal | multiple | bottom-right | layerId | native |
| decision_panel | disclosure.decision_panel | default, compact | default | zero_or_one | top-left | — | native |
| export_layout | export.page_layout | A4_landscape, A4_portrait, A3_landscape, letter | A4_landscape | single | none | — | native |
| graticule | navigation.graticule | light, geographic | light | single | none | — | native |
| inset_map | inset.map | overview, location | overview | zero_or_one | top-right | — | native |
| legend | legend.graduated | academic, compact, report, horizontal | academic | multiple | bottom-left | layerId | native |
| map_border | frame.map_border | minimal, academic, report, neatline | minimal | single | none | — | native |
| methodology_note | disclosure.methodology_note | default, compact | default | zero_or_one | bottom-left | — | native |
| north_arrow | navigation.north_arrow | compass_minimal_black, compass_needle, compass_rose, arrow_simple, monochrome | compass_minimal_black | single | top-right | — | native |
| scale_bar | navigation.scale_bar | minimal, boxed, academic, dual_unit | minimal | single | bottom-right | — | native |
| statistics_panel | analysis.statistics_panel | default, compact, kpi | default | zero_or_one | top-left | — | native |
| subtitle | annotation.subtitle | default, academic, report | default | zero_or_one | top-center | — | native |
| table_panel | analysis.table_panel | default, compact | default | multiple | bottom-right | — | native |
| title | annotation.title | academic, report, presentation, minimal, government | academic | single | top-center | — | native |
| uncertainty_panel | disclosure.uncertainty_panel | default, compact | default | zero_or_one | bottom-right | — | native |

## 变体模板清单

### annotation
- `annotation/text`（variant=text）
- `annotation/callout`（variant=callout）
- `annotation/group`（variant=group）

### attribution
- `attribution/default`（variant=default）
- `attribution/compact`（variant=compact）

### categorical_legend
- `categorical-legend/academic`（variant=academic）
- `categorical-legend/compact`（variant=compact）
- `categorical-legend/horizontal`（variant=horizontal）
- `categorical-legend/report`（variant=report）

### chart_panel
- `chart-panel/default`（variant=default）
- `chart-panel/compact`（variant=compact）
- `chart-panel/transparent`（variant=transparent）
- `chart-panel/report`（variant=report）

### continuous_colorbar
- `colorbar/horizontal`（variant=horizontal）
- `colorbar/vertical`（variant=vertical）
- `colorbar/slim`（variant=slim）
- `colorbar/scientific`（variant=scientific）
- `colorbar/stepped`（variant=stepped）

### decision_panel
- `decision-panel/default`（variant=default）
- `decision-panel/compact`（variant=compact）

### export_layout
- `export-layout/A4-landscape`（variant=A4_landscape）
- `export-layout/A4-portrait`（variant=A4_portrait）
- `export-layout/A3-landscape`（variant=A3_landscape）
- `export-layout/letter`（variant=letter）

### graticule
- `graticule/light`（variant=light）
- `graticule/geographic`（variant=geographic）

### inset_map
- `inset-map/overview`（variant=overview）
- `inset-map/location`（variant=location）
- `inset-map/hierarchy-locator`（variant=hierarchy_locator，planned）

### legend
- `legend/academic`（variant=academic）
- `legend/compact`（variant=compact）
- `legend/report`（variant=report）
- `legend/horizontal`（variant=horizontal）
- `legend/bivariate`（variant=bivariate，planned）
- `legend/uncertainty`（variant=uncertainty，planned）

### map_border
- `frame/minimal`（variant=minimal）
- `frame/academic`（variant=academic）
- `frame/report`（variant=report）
- `frame/neatline`（variant=neatline）

### methodology_note
- `methodology-note/default`（variant=default）
- `methodology-note/compact`（variant=compact）

### north_arrow
- `north-arrow/minimal-black`（variant=compass_minimal_black）
- `north-arrow/simple-arrow`（variant=arrow_simple）
- `north-arrow/compass-needle`（variant=compass_needle）
- `north-arrow/compass-rose`（variant=compass_rose）
- `north-arrow/monochrome`（variant=monochrome）

### scale_bar
- `scale-bar/minimal`（variant=minimal）
- `scale-bar/boxed`（variant=boxed）
- `scale-bar/academic`（variant=academic）
- `scale-bar/dual-unit`（variant=dual_unit）

### statistics_panel
- `statistics-panel/default`（variant=default）
- `statistics-panel/compact`（variant=compact）
- `statistics-panel/kpi`（variant=kpi）

### subtitle
- `subtitle/default`（variant=default）
- `subtitle/academic`（variant=academic）
- `subtitle/report`（variant=report）

### table_panel
- `table-panel/default`（variant=default）
- `table-panel/compact`（variant=compact）

### title
- `title/academic`（variant=academic）
- `title/report`（variant=report）
- `title/presentation`（variant=presentation）
- `title/minimal`（variant=minimal）
- `title/government`（variant=government）

### uncertainty_panel
- `uncertainty-panel/default`（variant=default）
- `uncertainty-panel/compact`（variant=compact）

