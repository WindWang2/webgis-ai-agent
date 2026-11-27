# INTERFACES — 04 线接口契约（子代理边界依据）

## 已冻结（WP1/WP2，commit f1aec6e71 + 69fc61eeb）
- 几何 oracle：`build_glyph_geometry(kind, [(cat,v)...], scale, size_policy, layout=)` → typed dict（pie: slices[{category,value,share,start_angle_deg,end_angle_deg}] + radius_px + zero_slices；bars: segments[{category,value,x0,x1,y0,y1}] + extent_px/baseline_y/zero_values；segmented: segments[{...,radius_px,area_px2}] + slot_deg）。角度 0°=12 点钟顺时针；条形 glyph-local 基线 y=0；量化 GEOMETRY_DECIMALS=3。
- 公共尺度：`compute_glyph_scale(kind, rows, size_policy)` → {r_max_px, v_ref[, total_area_encoding, r_min_px] | v_abs_max, px_per_unit, h_max_px, bar_w_px, bar_gap_px}。composition 总量编码 v_ref=跨要素总量最大；segmented v_ref=全图单值最大。
- 资格：`qualify_features(features, kind, categories, fields, missing_policy, units, id_field)` → {schema: diagram_symbol_qualification.v1, values: [[(cat,v)...]], glyph_feature_ids, evidence{missing_by_category,...}}。
- 布局：`solve_diagram_symbol_layout(features, qualified, bound_radii, layout_policy)` → {schema: diagram_symbol_layout.v1, glyphs[{feature_id, anchor, anchor_source, position, displacement_px, leader_line, radius_px, priority_rank, min_zoom}], evidence{...}, digest}。
- 图例：`build_glyph_legend(...)` → {schema: diagram_symbol_legend.v1, categories[{category,color_slot}], size_reference, notes_zh}。色值由消费方经 palettes.resolve_palette_colors(palette_id) 按 color_slot 解析。
- 错误面：DiagramSymbolError{code,message,field,correction_hint,details}.payload()；code 表见 DIAGRAM_ERROR_CODES。
- MapSpec：layer.diagram_symbol（2.2 additive，diagram_symbol.v1，extra=forbid）；kind/layout/categories/fields/unit/missing_policy/size_policy/palette_id/id_field。WP2+ 追加子字段走同块 additive（anchor/density/evidence 落地时加）。

## WP3 渲染同源契约（SA/SB 子代理边界）
- 图形层命名：MapLibre custom layer id = `<layerId>-diagram-symbols`（renderingMode '2d'），注册 runtime-layer-registry 保 setStyle 存活。
- TS 孪生：`frontend/lib/mapkit-diagram-symbols/`（新目录，SB 专属）：oracle.ts（逐位对齐 Python build_glyph_geometry/compute_glyph_scale/solve_layout，含同精度 quantize=3）、layer.ts（GL custom layer：饼扇形 triangle-fan、环带 strip、条 quad、分段扇形 fan；per-vertex color）、index.ts 出口。禁止改 types.generated.ts（再生成产物）、禁止改 Python。
- hit-test：底层 circle proxy 层（同源 paint，透明度 0 或 1e-6），queryRenderedFeatures 通道复用既有 interactive-ids。
- 导出：SA 专属 app/services/mapspec_to_svg.py 增 diagram_symbol 分支（消费 Python oracle + layout plan，SVG path + xml 转义）；前端 export-chrome/vector-svg 由主线接（不与 SB 同文件并发）。
- 图例条目入 render_scene.derive_legend_items 扩展（主线接）。

## WP4 双入口契约
- 服务：app/services/diagram_symbol_authoring/{service,errors}.py（inspect/plan/apply；preview 零 mutation；apply 单笔 CAS apply_gis_mutation；UI 路由与 Agent 工具同 service）。
- Agent 工具：webgis_diagram_symbol_inspect / webgis_diagram_symbol_plan / webgis_diagram_symbol_apply（tier 2，side_effect state_mutation，typed 拒绝回执）。
- 路由：app/api/routes/diagram_symbols.py（POST inspect/plan/apply，expected_revision + operation_id）。
- UI：components/hud/diagram-symbol-panel.tsx 挂 layer-style-panel（preview→commit）。
