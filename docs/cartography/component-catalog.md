# Component & Variant Catalog

> 由 registry 生成；真值：`component_registry.py` + `component_templates.py`。

共 20 个组件类型 / 104 个变体模板。

| type | 类目 | variants | 默认 | 卡数 | 位置 | 绑定 | 状态 |
|---|---|---|---|---|---|---|---|
| annotation | annotation.text | text, callout, group, footer, timestamp, projection_note, data_source, highlight | text | multiple | top-left | — | native |
| attribution | annotation.attribution | default, compact | default | single | bottom-left | — | native |
| categorical_legend | legend.categorical | academic, compact, report, horizontal, nested | academic | multiple | bottom-left | layerId | native |
| chart_panel | analysis.chart_panel | default, compact, transparent, report, bar, horizontal_bar, grouped_bar, stacked_bar, line, area, scatter, histogram, box_plot, pie, donut, radar, rose, timeseries, cumulative, heat_matrix, kpi_card, ranking_list | default | multiple | top-left | — | native |
| continuous_colorbar | legend.continuous_colorbar | horizontal, vertical, slim, scientific, stepped | horizontal | multiple | bottom-right | layerId | native |
| decision_panel | disclosure.decision_panel | default, compact | default | zero_or_one | top-left | — | native |
| export_layout | export.page_layout | A4_landscape, A4_portrait, A3_landscape, letter | A4_landscape | single | none | — | native |
| graticule | navigation.graticule | light, geographic, projected | light | single | none | — | native |
| inset_map | inset.map | overview, location, hierarchy | overview | zero_or_one | top-right | — | native |
| label_layer | content.label_layer | auto_field, explicit_field, top_n, hover_only | auto_field | multiple | none | layerId | native |
| legend | legend.graduated | academic, compact, report, horizontal, bivariate, uncertainty, size, line, composite | academic | multiple | bottom-left | layerId | native |
| map_border | frame.map_border | minimal, academic, report, neatline | minimal | single | none | — | native |
| methodology_note | disclosure.methodology_note | default, compact, data_quality | default | zero_or_one | bottom-left | — | native |
| north_arrow | navigation.north_arrow | compass_minimal_black, compass_needle, compass_rose, arrow_simple, monochrome, dual_convention | compass_minimal_black | single | top-right | — | native |
| scale_bar | navigation.scale_bar | minimal, boxed, academic, dual_unit | minimal | single | bottom-right | — | native |
| statistics_panel | analysis.statistics_panel | default, compact, kpi, explanation | default | zero_or_one | top-left | — | native |
| subtitle | annotation.subtitle | default, academic, report, compact | default | zero_or_one | top-center | — | native |
| table_panel | analysis.table_panel | default, compact, dense | default | multiple | bottom-right | — | native |
| title | annotation.title | academic, report, presentation, minimal, government, banner, compact | academic | single | top-center | — | native |
| uncertainty_panel | disclosure.uncertainty_panel | default, compact | default | zero_or_one | bottom-right | — | native |

## 变体模板清单

### annotation
- `annotation/text`（variant=text）
- `annotation/callout`（variant=callout）
- `annotation/group`（variant=group）
- `annotation/footer`（variant=footer）
- `annotation/timestamp`（variant=timestamp）
- `annotation/projection-note`（variant=projection_note）
- `annotation/data-source`（variant=data_source）
- `annotation/highlight`（variant=highlight）

### attribution
- `attribution/default`（variant=default）
- `attribution/compact`（variant=compact）

### categorical_legend
- `categorical-legend/academic`（variant=academic）
- `categorical-legend/compact`（variant=compact）
- `categorical-legend/horizontal`（variant=horizontal）
- `categorical-legend/nested`（variant=nested）
- `categorical-legend/report`（variant=report）

### chart_panel
- `chart-panel/default`（variant=default）
- `chart-panel/compact`（variant=compact）
- `chart-panel/transparent`（variant=transparent）
- `chart-panel/report`（variant=report）
- `chart-panel/kind-bar`（variant=bar）
- `chart-panel/kind-horizontal-bar`（variant=horizontal_bar）
- `chart-panel/kind-grouped-bar`（variant=grouped_bar）
- `chart-panel/kind-stacked-bar`（variant=stacked_bar）
- `chart-panel/kind-line`（variant=line）
- `chart-panel/kind-area`（variant=area）
- `chart-panel/kind-scatter`（variant=scatter）
- `chart-panel/kind-histogram`（variant=histogram）
- `chart-panel/kind-box-plot`（variant=box_plot）
- `chart-panel/kind-pie`（variant=pie）
- `chart-panel/kind-donut`（variant=donut）
- `chart-panel/kind-radar`（variant=radar）
- `chart-panel/kind-rose`（variant=rose）
- `chart-panel/kind-timeseries`（variant=timeseries）
- `chart-panel/kind-cumulative`（variant=cumulative）
- `chart-panel/kind-heat-matrix`（variant=heat_matrix）
- `chart-panel/kind-kpi-card`（variant=kpi_card）
- `chart-panel/kind-ranking-list`（variant=ranking_list）

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
- `graticule/projected`（variant=projected）

### inset_map
- `inset-map/overview`（variant=overview）
- `inset-map/location`（variant=location）
- `inset-map/hierarchy-locator`（variant=hierarchy）

### label_layer
- `label-layer/auto-field`（variant=auto_field）
- `label-layer/explicit-field`（variant=explicit_field）
- `label-layer/top-n`（variant=top_n）
- `label-layer/hover-only`（variant=hover_only）

### legend
- `legend/academic`（variant=academic）
- `legend/compact`（variant=compact）
- `legend/report`（variant=report）
- `legend/horizontal`（variant=horizontal）
- `legend/bivariate`（variant=bivariate）
- `legend/uncertainty`（variant=uncertainty）
- `legend/size`（variant=size）
- `legend/line`（variant=line）
- `legend/composite`（variant=composite）

### map_border
- `frame/minimal`（variant=minimal）
- `frame/academic`（variant=academic）
- `frame/report`（variant=report）
- `frame/neatline`（variant=neatline）

### methodology_note
- `methodology-note/default`（variant=default）
- `methodology-note/compact`（variant=compact）
- `methodology-note/data-quality`（variant=data_quality）

### north_arrow
- `north-arrow/minimal-black`（variant=compass_minimal_black）
- `north-arrow/simple-arrow`（variant=arrow_simple）
- `north-arrow/compass-needle`（variant=compass_needle）
- `north-arrow/compass-rose`（variant=compass_rose）
- `north-arrow/dual-convention`（variant=dual_convention）
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
- `statistics-panel/explanation`（variant=explanation）

### subtitle
- `subtitle/default`（variant=default）
- `subtitle/academic`（variant=academic）
- `subtitle/report`（variant=report）
- `subtitle/compact`（variant=compact）

### table_panel
- `table-panel/default`（variant=default）
- `table-panel/compact`（variant=compact）
- `table-panel/dense`（variant=dense）

### title
- `title/academic`（variant=academic）
- `title/report`（variant=report）
- `title/banner`（variant=banner）
- `title/presentation`（variant=presentation）
- `title/compact`（variant=compact）
- `title/minimal`（variant=minimal）
- `title/government`（variant=government）

### uncertainty_panel
- `uncertainty-panel/default`（variant=default）
- `uncertainty-panel/compact`（variant=compact）

