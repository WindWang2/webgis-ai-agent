# export_semantic_corpus 语料（F14 D7）

live vs export 结构语义 golden corpus。每个 `case_*.json`：

```json
{"name": ..., "description": ..., "mapspec": {...}, "notes": ...}
```

消费方：`tests/unit/test_export_semantic_corpus.py`（编译 → 抽取 → 比较闭环）。
对账实现：`app/lib/cartography/export_semantic_corpus.py`（纯函数、无 I/O）。

## 语料清单

| case | 覆盖点 |
| --- | --- |
| `case_basic_chrome` | title+subtitle+north_arrow+scale_bar+legend（绑 layer，3 条目）+attribution+graticule；geojson 源两层 |
| `case_colorbar` | continuous_colorbar 组件 + layer legend_spec type=continuous（palette_colors 5 色、min/max/unit） |
| `case_annotations` | annotation text 两行 + items 组 2 条 |
| `case_panels` | statistics_panel（stats.items 3）+ chart_panel（inline bar 3 点）+ table_panel（inline table 3 行） |
| `case_disclosures` | methodology_note（warnings 2）+ uncertainty_panel + decision_panel（rows 3 含一个 vetoed） |
| `case_hidden_layer` | 同 basic 但 l2 `visible=False`（隐藏层泄漏 = extra = fail） |
| `case_disabled_component` | north_arrow `enabled=False`（双侧缺席） |
| `case_no_chrome` | `layout.components` 缺席（expected 空、不炸） |

## ABI 驱动规则

- `expected_semantics` 只收 `PUBLICATION_COMPONENT_TYPES`
  （`app/lib/cartography/component_renderers.py` 矩阵派生常量）内的族。
  colorbar/annotation/panel/披露族**当前不在矩阵内** → 不进 expected；
  主 agent 落地渲染器并置 `publication=True` 后自动纳入，语料无需改动。
- chrome marker class 契约已冻结：`mapspec-chrome` 组内
  `chrome-title / chrome-north-arrow / chrome-scale-bar / chrome-legend /
  chrome-inset / chrome-attribution / chrome-graticule / chrome-map-border`，
  即将新增 `chrome-colorbar / chrome-annotation /
  chrome-panel[data-kind=statistics|chart|table|methodology|uncertainty|decision]`。

## 已知偏差

- `case_basic_chrome` 已含 `map_border` 组件（F14 WP2 集成时加回）：`chrome-map-border` 组包装
  （F14 WP2）落地前，类名解析器无法从产物核验该族，闭环会出现诚实
  missing。落地后把 `{"id": "c-mb", "type": "map_border", "enabled": true}`
  加回该 case 的 `layout.components`（矩阵一致性用例以 xfail(strict) 锁转正）。
- 图例条目实然计数 = `chrome-legend` 组内 `rect[fill]` 数 − 1（每组恰一块
  `render_legend_box` 卡片背景 rect；任务书公式按裸 rect 计会多 1）。

## panel 内联数据契约（expected.panel_specs 探测键）

- `statistics_panel` → `options.stats.items`（列表）
- `chart_panel` → `options.chart.points`（列表；兼容 `data`）
- `table_panel` → `options.table.rows`（列表）
- `methodology_note` → `options.warnings`（列表）
- `uncertainty_panel` → `options.uncertainty`（非空 dict）
- `decision_panel` → `options.rows`（列表）
