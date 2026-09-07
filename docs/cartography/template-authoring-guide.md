# Template Authoring Guide — 如何新增 model / variant / template

> 所有登记都走 registry（ADR-0101 D3：变体是 registry 事实，不是前端条件）。
> 登记后跑 `python -m pytest tests/cartography tests/unit/gis_harness --no-cov`
> 与 `python -m app.lib.cartography.catalog_docs --check`（生成文档需刷新时
> 先重新生成）。

## 1. 新增 Map Model

位置：`app/lib/cartography/model_packs/<域>.py`（新域则建文件并在
`model_packs/__init__.py` 追加导出）。

```python
m(id="my_model", name_zh="我的模型", purpose_zh="一句话用途",
  geometry_kinds=["polygon"], maplibre_layer_type="fill",
  classification="graduated", color_scheme_kind="sequential",
  default_palette="Blues", recommended_classifiers=["natural_breaks"],
  accepted_artifact_types=["admin_aggregate_table"],   # 必须真实注册
  recommended_components=["legend"],                    # 必须 ∈ ComponentType
  runtime_status="native",                              # 或 "planned"
  fallback_model_id="administrative_choropleth",        # 可选降级链
  pitfalls_zh=["…"])
```

规则（`validate_model_library()` 强制）：

- `native` ⇒ `maplibre_layer_type` ∈ 前端运行时支持族
  （fill/line/circle/symbol/heatmap/raster/fill-extrusion）；
  `planned` ⇒ 必须在 pitfalls 中写明「planned：…原因」；
- `accepted_artifact_types` 必须在 `app/lib/gis/artifacts.py` 注册
  （禁止虚构数据契约）；
- `default_palette` 必须已注册（COLOR_PALETTES / NATIVE_HEATMAP_COLORS）；
- `fallback_model_id` 必须可解析且不得成环。

若模型需要图例族组件，同步把模型 id 加进
`component_registry.py` 中对应图例 descriptor 的 `compatible_map_models`
（否则 composer per-layer 选型会跳过该层）。

## 2. 新增 Component Variant

1. `component_registry.py`：把 variant 加进对应 descriptor 的 `variants`
   （只列**渲染器真实支持**的变体）；
2. `component_templates.py`：新增 `ComponentTemplate`（id 命名
   `type/variant`；priority **大于**该类型既有首个模板 —— 不改变缺省选型；
   `default_options.variant` 与 `variant` 双写一致）；
3. 前端渲染器实现该变体（`frontend/components/map/map-components/` 对应
   tsx；未知变体确定性回退缺省样式，不崩 chrome）；
4. 前端测试补变体断言（`variants-v3.test.tsx` 风格）；
5. 重新生成 catalog：`python -m app.lib.cartography.export_component_catalog`。

前瞻变体：只登记 `ComponentTemplate(runtime_status="planned")`，不改
descriptor.variants —— resolver 的 template 门控会拒绝它进入最终产品。

## 3. 新增 Composition Template

位置：`app/lib/cartography/composition_packs/<域>.py`（用 `_base.py` 的
helper 组槽位）。

规则（`CompositionTemplateRegistry.validate()` 强制）：

- `allowed_component_types` 必须 ⊆ 已注册组件 id；
- `preferred_templates` 必须是已注册模板 id；
- `compatible_map_models` 必须在模型目录可解析；
- `fallback_template` 必须存在，且 pack 模板不得参与 fallback 环；
- required 槽不得含仅 interactive 的组件类型（若模板声明 pdf 目标）；
- `output_targets` 与槽位能力一致（resolver 会在不支持的输出上剔除组件，
  required 缺席会导致组合回退）。

## 3.5 V4 新增维度（Design System）

- **新增图表 kind**：在 `app/lib/cartography/chart_kinds.py` 登记
  `ChartKindDescriptor`（live 引擎/导出能力等级/别名），前端
  `chart-core.tsx` 加渲染分支、`chart-panel.tsx` 的 kind 变体集合同步；
  未实现的 kind 必须标 `live_engine="planned"` + `export_level=
  "unsupported"`（词表校验强制），前端与工具层会诚实拒绝并引导回退。
- **新增模型的主题绑定**：`MapModel.default_theme` 必须是已注册主题 id
  （`validate_model_library` 校验）；领域主题见 `docs/cartography/
  design-system-v4.md` §8。
- **图例族兼容清单**：新模型 native 化时必须把模型 id 补进 legend /
  categorical_legend / continuous_colorbar 中相关描述符的
  `compatible_map_models` —— 否则组合模板 required 图例槽会必然缺失
  （corpus `stress::` 维度锁定此约束）。
- **布局 V3**：组合产物走 `solve_layout_v3`（列单位/碰撞组/占用/诊断）；
  新组件在 descriptor 上声明 `collision_class` 与 `states`。

## 4. 刷新生成物（按序）

```bash
python -m app.lib.cartography.export_component_catalog   # 前端 catalog JSON
python -m app.lib.cartography.catalog_docs               # docs/cartography/*.md
GOLDEN_CORPUS_UPDATE=1 python3 -m pytest tests/cartography/golden_corpus \
  && python3 -m pytest tests/cartography/golden_corpus   # review 差异后复跑
```

黄金刷新必须人工 review —— 语料测试在写盘后**故意失败**一次，
禁止把快照更新当回归修复。
