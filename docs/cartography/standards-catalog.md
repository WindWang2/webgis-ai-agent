# Cartographic Standards Catalog

> 由 `app.lib.cartography.standards.catalog` 从 StandardsRegistry 生成；
> 手改无效。真值：`app/lib/cartography/standards/**`（ADR-0200）。

义务轴：purpose exploration/analysis/publication/briefing；audience public/technical/executive/education；medium screen/mobile/print。

## Pack `core` v1.0.0

- fingerprint: `stdpack-sha256:4a6fbbfa627c67d43a62e995c0952d6832c5b09f2254e7c6da42f09b64e90555`
- 规则数：12

| rule_id | kind | severity | 义务 | fix 路由 | 依赖 | 参考 |
|---|---|---|---|---|---|---|
| `CORE.LABEL_DENSITY_DECLARED` | label_density_declared | warning | 高密度点层未声明标注预算（mode/topN/maxLabels），屏幕/mobile 可读性风险。 | advisory:— | — | engine://label_plan, ADR-0200 |
| `CORE.LEGEND_PRESENT` | legend_present | error | 专题编码在场但没有任何图例（组件、面板或 legend_spec）。 | component_autofill:legend | — | engine://semantic_checks/LEGEND_FIELD_CONSISTENCY, ADR-0200 |
| `CORE.CLASSIFICATION_DECLARED` | classification_declared | info | 分级图例未声明分类方法（quantiles/equal_interval/…），复核不可复现。 | advisory:— | CORE.LEGEND_PRESENT | engine://thematic_spec/resolve_classification_decision, ADR-0200 |
| `CORE.COUNT_VS_RATE` | count_vs_rate | warning | 面分级疑似直接分级原始计数字段（choropleth 经典错误：应归一化为比率/密度）。 | advisory:— | CORE.LEGEND_PRESENT | engine://semantic_checks/CLASSIFICATION_CARDINALITY, ADR-0200 |
| `CORE.CVD_SAFE_PALETTE` | cvd_safe_palette | warning | 色带在色觉障碍模拟下相邻可分辨性不足（CVD 上下文 ΔE 低于阈值）。 | quality_loop:change_palette | CORE.LEGEND_PRESENT | engine://context_matrix/evaluate_cell, ADR-0200 |
| `CORE.LEGEND_UNIT_DISCLOSURE` | legend_unit_disclosure | warning | 分级/连续图例未同时声明 title 与 unit（读图者无法得知数值口径）。 | advisory:— | CORE.LEGEND_PRESENT | engine://thematic_spec/build_graduated_spec, ADR-0200 |
| `CORE.PRINT_LEGIBLE_PALETTE` | print_legible_palette | warning | 色带经印刷去饱和后灰度可分辨性不足。 | quality_loop:change_palette | CORE.LEGEND_PRESENT | engine://context_matrix/evaluate_cell, ADR-0200 |
| `CORE.REQUIRED_COMPONENTS` | required_components | error | 画幅必配组件缺失（图名/比例尺/指北针/署名等 §0.5 基线）。 | component_autofill:title | — | §0.5, engine://component_composer/required_components_for, ADR-0200 |
| `CORE.SOURCE_DISCLOSURE` | source_disclosure | error | 数据署名缺失或仍为占位文本（署名不可省略）。 | component_autofill:attribution | — | §0.5, ADR-0200 |
| `CORE.THEMATIC_PROFILE_DECLARED` | thematic_profile_declared | info | 专题层在场但 spec 未声明 cartographic_profile（规则 profile 选择退回推断）。 | advisory:— | — | ref://semantic_checks/_review_profile, ADR-0200 |
| `CORE.TIME_DISCLOSURE` | time_disclosure | warning | 数据含时间语义但 spec 未携带 time 证据块（时间口径不可追溯）。 | advisory:— | — | engine://quality_loop/cartographic_projection(time), ADR-0200 |
| `CORE.UNCERTAINTY_DISCLOSURE` | uncertainty_disclosure | warning | 数据含不确定性语义但缺少 uncertainty_panel/methodology_note 呈现面。 | component_autofill:uncertainty_panel | — | ADR-0200 |

### 适用轴

- `CORE.LABEL_DENSITY_DECLARED`：purpose=any；audience=any；medium=['mobile', 'screen']；data=any
- `CORE.LEGEND_PRESENT`：purpose=any；audience=any；medium=any；data=any
- `CORE.CLASSIFICATION_DECLARED`：purpose=['analysis', 'publication']；audience=any；medium=any；data=any
- `CORE.COUNT_VS_RATE`：purpose=['analysis', 'publication']；audience=any；medium=any；data=any
- `CORE.CVD_SAFE_PALETTE`：purpose=any；audience=any；medium=any；data=any
- `CORE.LEGEND_UNIT_DISCLOSURE`：purpose=['analysis', 'briefing', 'publication']；audience=any；medium=any；data=any
- `CORE.PRINT_LEGIBLE_PALETTE`：purpose=any；audience=any；medium=['print']；data=any
- `CORE.REQUIRED_COMPONENTS`：purpose=any；audience=any；medium=any；data=any
- `CORE.SOURCE_DISCLOSURE`：purpose=['analysis', 'publication']；audience=any；medium=any；data=any
- `CORE.THEMATIC_PROFILE_DECLARED`：purpose=any；audience=any；medium=any；data=any
- `CORE.TIME_DISCLOSURE`：purpose=['analysis', 'briefing', 'publication']；audience=any；medium=any；data=any
- `CORE.UNCERTAINTY_DISCLOSURE`：purpose=['analysis', 'publication']；audience=any；medium=any；data=any
