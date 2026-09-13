# ac-05 标注链路勘察报告（P0）

> 线：adaptive-cartography/05-auto-labeling · ADR-0154
> 基线 commit：`1fd4b035`（origin/master，2026-09-13）
> 勘察方式：S1 只读扫描（文件:行号均已核对）+ 本机测量。本文档是 P7 回归基座的基线出处。

## 1. 现状链路（谁在产生标注）

结论先行：**`layer.label` 的消费管道全链路存在（schema → 编译器 → 运行时 → SVG 导出 → 场景快照 → QA 规则），但 `app/` 内没有任何 producer** —— 除 lifecycle 快照恢复的合并回写（`lifecycle_engine.py:1937`）外，无代码路径把 `label` 写进图层。因此现状下标注只能靠"上游 LLM/用户显式给出 `label{field}`"，不给就静默无标注。

| 环节 | 位置 | 触发条件 | 缺省行为 |
|---|---|---|---|
| 字段展示（上下文） | `app/services/chat/context/formatters.py:77` | 选中要素快照 | `label_keys=("name","title","label","id","OBJECTID")` 只拼 LLM 上下文，**从不进制图** |
| schema | `app/lib/cartography/mapspec_schema.py:152-157` | — | `MapSpecLayerLabel{field(必填), size?, color?, haloColor?, haloWidth?}`；spec 级 `MapLabelConfig.maxLabels`（290-297） |
| 无头编译器 | `frontend/lib/mapspec-compiler/compiler.ts:496-525` | `layer.label` 对象含 `field`，或 `layout.labelField` 标量 | 生成 `${id}-label` symbol 子层（text-field/size 12/halo 白 1px/`text-allow-overlap:false`）；**无图层类型限制** |
| 活运行时 | `frontend/lib/mapspec-runtime/runtime.ts:643-651, 710-743` | 同上；**额外排除 `raster`/`heatmap`** | 无 label spec ⇒ **静默无标注**（无告警、无 evidence） |
| 导出（SVG） | `app/services/mapspec_to_svg.py:984-986` | symbol/text 层 | text-field 缺省回退 `"{name}"`；走确定性 `label_engine`/`label_collision` 避让（仅导出） |
| 场景快照 | `app/lib/cartography/render_scene.py:434`（前端孪生 `map-kit/render-scene.ts:71`） | — | `has_label = !!label || !!layout.labelField` |
| QA 规则 | `app/lib/cartography/semantic_checks.py:1280-1378` | symbol/text 层 + 字符串 `layout.text-field` + `sources[sid].profile` | `carto.label.collision_est`：注记盒面积占视口比估计，warn 0.10 / fail 0.25（env 可覆写） |

失败模式清单（现状）：

1. **F1 静默无标注**：spec 不带 `label{field}` ⇒ 无标注、无告警、无 evidence（编译器与运行时一致）。
2. **F2 producer 缺位**：`visualization_plan.py`/`thematic_spec.py` 的 11 处 `label` 命中全是图例条目，无一处写图层标注；后端没有字段挑选决策面。
3. **F3 编译器/运行时分歧**：无头编译器对 raster/heatmap 也生成 label 子层，活运行时排除 —— 同 spec 屏幕与导出可漂移。
4. **F4 密度告警只劝人**：`collision_est` fail 的建议是 `thin_labels`、warn 是 `resize_labels`，但 `repairability="not_repairable"` —— 全靠人工缩字号/抽稀。
5. **F5 无优先级/无分级**：`text-allow-overlap:false` 是唯一避让手段（MapLibre 内置贪心，按层序+书写序）；低 zoom 与高 zoom 标注密度相同。
6. **F6 label 子层运行时零测试**：`frontend/lib/mapspec-runtime/*.test.ts` 无一处 label 断言（编译器有 #1007 halo 契约测试）。
7. **F7 `label_layer` 组件未注册**：taxonomy 有 `content.label_layer`（`component_taxonomy.py:70`），`component_registry._SEED_DESCRIPTORS` 无对应描述符 —— 标注不可寻址、不可局部突变。

## 2. collision_est 告警基线（本机测量，2026-09-13）

方法：`evaluate_cartography_semantics` 直接驱动，symbol 层 + `layout.text-field`，profile 内嵌 `sampleValues`。扫描 30 格：`featureCount ∈ {500,1200,2000,3200,8000} × 平均字长 ∈ {4,8} × zoom ∈ {10,12,14}`，字号 12。

| 结果 | 数值 |
|---|---|
| fail | 23/30 |
| warning | 2/30 |
| **告警率（warn+fail）** | **83%** |
| ratio 极值 | 0.02（500/4字/z14）… 11.72（8000/8字/z10） |

密集层（>2000）在这套基线下 100% 触发告警。P7 目标：同样的 30 格扫描，在 spec 声明了标注策略（`top_n`/zoom 分级）后，检查按策略修正 `est_visible_labels`，告警率显著下降（预期 ≤ 40%，实测见 PR）。

## 3. 组件目录差异（taxonomy vs registry）

- 分类共 61 个 id（9 根 + 52 叶）；已注册描述符 19 个，覆盖 19 个分类。
- **未注册的分类叶子 31 个**，含本线目标 `content.label_layer`，以及 `content.basemap/thematic_layer/reference_layer/overlay_layer`、`legend.bivariate/symbol/size`、`annotation.callout/data_source/metadata`、`interaction.*`（6）、`export.*`（6）、`frame.neatline/background/margin/inset_frame`、`analysis.summary/indicator`、`inset.overview_map/location_map`、`navigation.coordinate_grid`。
- 即 `content.*` 四个叶子全部未注册 —— `label_layer` 并非唯一，但它是本线唯一需要寻址面的（其余归 07 线/后续线）。
- 注册机制：模块级 `_SEED_DESCRIPTORS` 列表 + `load_builtins()`（无装饰器）；`validate()` 只查描述符→分类存在性，空分类合法（`tests/cartography/test_component_library_v2.py:322`）。
- 突变机制：`mutate_component` 类型无关（`components.py:928-1051`）；`rebind_component` 按 `_REBIND_FIELDS` 白名单（每型可绑定通道）；upsert 走 `_FACTORY_BY_TYPE`（1203-1232）。lifecycle 引擎 `PatchComponentIntent`/`RebindComponentIntent` 无类型白名单，size 上限 96KB。

## 4. 字段挑选的输入面（profile 形状）

- 生产者：`app/services/spatial_meta_profiler.py::profile_geojson_source`（278 行起）→ `{featureCount, bbox, crs, geometryTypes, fields: {name: {type, min/max/mean?, sampleValues(≤5 去重), null_count, quantiles?, null_ratio?}}, ...}`，挂在 `sources[sid].profile`。
- 消费口径基准：`semantic_checks._estimate_label_chars`（1381-1407，前 16 样本，CJK 1.0em / 其他 0.6em）—— 本线 `label_plan` 的宽度口径与之对齐。
- `choose_label_field(profile)` / `plan_label_strategy(profile)` 消费同一 profile dict；另提供 `field_stats_from_features`（直接吃 FeatureCollection，全量精确基数）供组件构建与测试。

## 5. 10 数据集 ground truth（P7 基准）

生成器：`tests/fixtures/labeling_datasets.py`（确定性，seed 显式，license clean 全合成）；判定表：`docs/dev/ac-05-label-groundtruth.csv`。

| dataset | 几何 | n | 最佳标注字段 | 诱饵谱系 |
|---|---|---|---|---|
| cn_provinces | polygon | 12 | `名称` | OBJECTID/code/时间戳 |
| cn_cities_points | point | 12 | `name` | osm_id/低基数类别/长地址 |
| rivers | line | 12 | `NAME` | fid/数值分级 |
| metro_stations_en | point | 12 | `title` | UUID/低基数 line |
| sensors | point | 60 | **None（必须不标注）** | hex id/时间戳/数值 |
| land_parcels | polygon | 40 | `地块名称` | 业务编码/低基数用途/12% 空值 |
| countries_mixed | polygon | 10 | `name` | FID/ISO3/双语言名称 tie-break |
| bus_routes | line | 8 | `线路名` | route_id/长文本时刻 |
| air_quality_stations | point | 36 | `站点名称` | station_code/跨站重复 city |
| hotels_dense | point | 3200 | `name` | 主键/低基数 brand（>2000 → top_n） |

## 6. 复核纪要（防重复）

1. `gh pr list --state all`（200 条，关键词 label/标注/注记/annotation/collision/避让）：无自动标注引擎实现线；`#1151`（V4 升级）、`#1181`（V6 publication）触及导出端确定性避让（`label_engine.py`/`label_collision.py` + `label-solver.ts` 孪生），**均为导出/出版物路径，不触交互运行时**。
2. `gh issue list --state all`（300 条，标注/注记/避让/压盖/文字）：无对应 issue（命中均为工具注记/上下文类杂项）。
3. `git branch -a | grep -iE "label|annot|collision"`：仅本线分支命中。
4. 代码 grep（§0.2 清单）与 §1 表一致；`component_taxonomy.py:70` 的 `content.label_layer` 定义在、注册缺位；`formatters.py:77` 的 `label_keys` 只服务上下文。
5. ADR-0118（D3 文本适配契约：`fit_label_text`/`wrap_label_text`/`MAX_SVG_LABEL_CHARS=60`）与 ADR-0126（§4 Label Engine V6：`label_collision.py` 便携子集 + `label-solver.ts` 1:1 孪生，`layout.labels.collision=="deterministic"` 仅导出置顶渲染）已读。**边界判定**：两者皆导出域；本线全部产出作用于交互运行时与 spec 决策面，不改动导出孪生（byte-stable 契约不动）。
6. 本线编号 ADR-0154 未被占用（01→0150 / 02→0151 / 03→0152 / 07→0156 按各自任务书分配）。

## 7. 与 06 线的边界（实测确认）

- `reconciler.ts`/`paint-bridge.ts`/`map-kit/renderer.ts`：本线零改动（P3 只动 `runtime.ts` 标注段 + 新增 `label-layout.ts`）。
- 字号口径：`label-layout.ts` 只产出 `size_ratio` 与 band 比例系数，绝对基准取既有 `labelSpec.size ?? layout.labelSize ?? 12`（06 线落地符号律后该缺省由其给基准）。
- 密度信号：本线 `label_plan.py::DENSE_FEATURE_COUNT` 单份实现；06 线若落地 `density_signal()` 则改为 import（PR 注明）。
