# Composition Template Catalog

> 由 registry 生成；真值：`composition_templates.py` + `composition_packs/`。

共 28 个组合模板（seed 8 + 域包）。

## composition.minimal_interactive（Minimal Interactive）

- 描述：最小交互地图：标题可选、色条/图例按需、比例尺必备。
- 版式：minimal；输出：interactive
- 兼容模型：（泛匹配）
- fallback：composition.standard_analysis
- 标签：—
- 槽位：
  - `title`：optional（title）@ top-center
  - `legend`：conditional（legend, categorical_legend, continuous_colorbar）@ bottom-left；bind_scope=all_thematic
  - `north_arrow`：optional（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `map_border`：forbidden（map_border）@ none
  - `export_layout`：forbidden（export_layout）@ none

## composition.standard_analysis（Standard Analysis）

- 描述：标准分析地图：标题+图例/色条+指北针+比例尺+归属+统计可选。
- 版式：standard；输出：interactive, png, pdf
- 兼容模型：visual_heatmap, administrative_choropleth, aggregate_grid, proportional_symbol
- fallback：composition.minimal_interactive
- 标签：—
- 槽位：
  - `title`：required（title）@ top-center
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：conditional（legend, categorical_legend, continuous_colorbar）@ bottom-left；bind_scope=all_thematic
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `statistics_panel`：optional（statistics_panel）@ top-left
  - `map_border`：optional（map_border）@ none

## composition.presentation_map（Presentation Map）

- 描述：演示用图：大标题、简洁图例、指北针可选、比例尺必备、无图框。
- 版式：presentation；输出：interactive, png
- 兼容模型：（泛匹配）
- fallback：composition.standard_analysis
- 标签：—
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/presentation
  - `legend`：conditional（legend, categorical_legend, continuous_colorbar）@ bottom-left；bind_scope=all_thematic；preferred=legend/compact
  - `north_arrow`：optional（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left

## composition.report_map（Report Map）

- 描述：报告用图：标题必备、副标题推荐、图例/色条按主题、指北针/比例尺/图框/元数据/统计面板。
- 版式：report；输出：png, pdf
- 兼容模型：（泛匹配）
- fallback：composition.academic_map
- 标签：—
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/report
  - `subtitle`：recommended（subtitle）@ top-center
  - `legend`：conditional（legend, categorical_legend, continuous_colorbar）@ bottom-left；bind_scope=all_thematic；preferred=legend/report
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `map_border`：required（map_border）@ none；preferred=frame/report
  - `attribution`：required（attribution）@ bottom-left
  - `statistics_panel`：recommended（statistics_panel）@ top-left
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-portrait

## composition.density_map（Density Map）

- 描述：密度图：连续色条必备、指北针/比例尺必备。
- 版式：standard；输出：interactive, png, pdf
- 兼容模型：visual_heatmap, raster_surface
- fallback：composition.standard_analysis
- 标签：—
- 槽位：
  - `title`：required（title）@ top-center
  - `colorbar`：required（continuous_colorbar）@ bottom-right；bind_scope=all_thematic；preferred=colorbar/horizontal
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right；fallback_zones=bottom-center
  - `attribution`：required（attribution）@ bottom-left
  - `statistics_panel`：optional（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right

## composition.statistical_map（Statistical Map）

- 描述：统计专题图：分级图例必备、统计面板推荐、学术风格。
- 版式：standard；输出：interactive, png, pdf
- 兼容模型：administrative_choropleth, aggregate_grid
- fallback：composition.standard_analysis
- 标签：—
- 槽位：
  - `title`：required（title）@ top-center
  - `legend`：required（legend）@ bottom-left；bind_scope=all_thematic
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `statistics_panel`：recommended（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `map_border`：optional（map_border）@ none

## composition.academic_map（Academic Map）

- 描述：学术出版地图：含标题、图例/色条、指北针、比例尺、经纬网、归属、图框、数据来源，导出必备版式。
- 版式：academic；输出：png, pdf, svg
- 兼容模型：visual_heatmap, administrative_choropleth, raster_surface
- fallback：composition.standard_analysis
- 标签：—
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/academic
  - `subtitle`：optional（subtitle）@ top-center
  - `legend`：conditional（legend, categorical_legend, continuous_colorbar）@ bottom-left；bind_scope=all_thematic；preferred=legend/academic, colorbar/horizontal
  - `north_arrow`：required（north_arrow）@ top-right；preferred=north-arrow/compass-rose
  - `scale_bar`：required（scale_bar）@ bottom-right；preferred=scale-bar/academic
  - `graticule`：optional（graticule）@ none
  - `attribution`：required（attribution）@ bottom-left
  - `map_border`：required（map_border）@ none；preferred=frame/academic
  - `statistics_panel`：optional（statistics_panel）@ top-left
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-landscape
  - `inset_map`：optional（inset_map）@ top-right；fallback_zones=bottom-right

## composition.remote_sensing_map（Remote Sensing Map）

- 描述：遥感栅格图：连续色条、标题、指北针、比例尺、归属、图框。
- 版式：academic；输出：png, pdf, svg
- 兼容模型：raster_surface
- fallback：composition.density_map
- 标签：—
- 槽位：
  - `title`：required（title）@ top-center
  - `colorbar`：required（continuous_colorbar）@ bottom-right；bind_scope=all_thematic
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `map_border`：required（map_border）@ none
  - `export_layout`：optional（export_layout）@ none

## composition.statistical_report（Statistical Report Map）

- 描述：统计专题报告版式：分级图例必备、统计面板推荐、图表可选、图框 + A4 版式，面向 PDF 报告产物。
- 版式：report；输出：interactive, png, pdf
- 兼容模型：administrative_choropleth, normalized_choropleth, diverging_choropleth, aggregate_grid, vulnerability_index
- fallback：composition.statistical_map
- 标签：statistical, report, pdf
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/report
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：conditional（legend, categorical_legend, continuous_colorbar）@ bottom-left；bind_scope=all_thematic；preferred=legend/report
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `statistics_panel`：recommended（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `map_border`：required（map_border）@ none；preferred=frame/report
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-portrait

## composition.density_exploration（Density Exploration Map）

- 描述：密度/连续场探索版式：scientific 色条 + 统计面板 + 图表，交互探索与 PNG 导出兼顾。
- 版式：standard；输出：interactive, png
- 兼容模型：visual_heatmap, raster_surface, terrain_analytical_surface, spectral_index_surface
- fallback：composition.density_map
- 标签：density, exploration, continuous
- 槽位：
  - `title`：required（title）@ top-center
  - `legend`：required（continuous_colorbar）@ bottom-right；bind_scope=all_thematic；preferred=colorbar/scientific
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right；fallback_zones=bottom-center
  - `attribution`：required（attribution）@ bottom-left
  - `statistics_panel`：optional（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right

## composition.flow_map_report（Flow Map Report）

- 描述：OD 流向报告版式：流向图例 + 统计面板 + 图框，出行/迁徙/物流流量的报告表达。
- 版式：report；输出：interactive, png, pdf
- 兼容模型：flow_od_arc, graduated_line
- fallback：composition.standard_analysis
- 标签：network, flow, transport
- 槽位：
  - `title`：required（title）@ top-center
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：required（legend, categorical_legend, continuous_colorbar）@ bottom-left；bind_scope=all_thematic
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `statistics_panel`：recommended（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `map_border`：optional（map_border）@ none
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-landscape

## composition.service_area_report（Service Area Report）

- 描述：服务区/可达性报告版式：分类图例 + 统计面板 + A4 版式，回答『N 分钟能到哪』的产品化表达。
- 版式：report；输出：interactive, png, pdf
- 兼容模型：service_area_overlay, proximity_overlay
- fallback：composition.standard_analysis
- 标签：network, accessibility, service-area
- 槽位：
  - `title`：required（title）@ top-center
  - `legend`：required（legend, categorical_legend）@ bottom-left；bind_scope=all_thematic；preferred=legend/report
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `statistics_panel`：recommended（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-portrait

## composition.terrain_analysis（Terrain Analysis Map）

- 描述：地形分析版式：连续色面 + scientific 色条 + 经纬网 + academic 图框 + A4 横版，地形解析的出版表达。
- 版式：academic；输出：png, pdf, svg
- 兼容模型：terrain_analytical_surface, raster_surface, isoline_contour
- fallback：composition.remote_sensing_map
- 标签：terrain, academic, raster
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/academic
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left；preferred=subtitle/academic
  - `legend`：required（continuous_colorbar, legend）@ bottom-right；bind_scope=all_thematic；preferred=colorbar/scientific
  - `north_arrow`：required（north_arrow）@ top-right；preferred=north-arrow/compass-rose
  - `scale_bar`：required（scale_bar）@ bottom-right；preferred=scale-bar/academic
  - `attribution`：required（attribution）@ bottom-left
  - `graticule`：optional（graticule）@ none
  - `statistics_panel`：optional（statistics_panel）@ top-left
  - `map_border`：required（map_border）@ none；preferred=frame/academic
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-landscape

## composition.watershed_report（Watershed Report Map）

- 描述：流域报告版式：面统计分级 + 统计面板 + 图表 + A4 竖版，水文/流域管理报告表达。
- 版式：report；输出：interactive, png, pdf
- 兼容模型：administrative_choropleth, normalized_choropleth, diverging_choropleth, suitability_classes
- fallback：composition.statistical_report
- 标签：hydrology, watershed, report
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/report
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：required（legend, categorical_legend, continuous_colorbar）@ bottom-left；bind_scope=all_thematic；preferred=legend/report
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `statistics_panel`：recommended（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `map_border`：required（map_border）@ none；preferred=frame/report
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-portrait

## composition.rs_index_report（Remote Sensing Index Report）

- 描述：遥感指数报告版式：指数面 + colorbar + 经纬网 + 图框 + A4 横版（remote_sensing_map 的报告化扩展）。
- 版式：academic；输出：png, pdf, svg
- 兼容模型：spectral_index_surface, raster_surface
- fallback：composition.remote_sensing_map
- 标签：remote-sensing, index, report
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/academic
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：required（continuous_colorbar）@ bottom-right；bind_scope=all_thematic；preferred=colorbar/scientific
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `graticule`：optional（graticule）@ none
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `map_border`：required（map_border）@ none；preferred=frame/academic
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-landscape

## composition.rs_classification（Remote Sensing Classification）

- 描述：遥感分类版式：分级栅格 + 分类图例 + 出版版式。classified_raster 为 planned —— 模板先行登记。
- 版式：academic；输出：png, pdf
- 兼容模型：classified_raster
- fallback：composition.rs_index_report
- 标签：remote-sensing, classification, planned-model
- 槽位：
  - `title`：required（title）@ top-center
  - `legend`：required（legend, categorical_legend）@ bottom-left；bind_scope=all_thematic
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `graticule`：optional（graticule）@ none
  - `map_border`：required（map_border）@ none；preferred=frame/academic
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-landscape

## composition.sar_change_report（SAR Change Report）

- 描述：SAR 变化报告版式：发散变化面 + 发散图例 + 出版版式。sar_change_detection 为 planned —— 模板先行登记。
- 版式：academic；输出：png, pdf
- 兼容模型：sar_change_detection, sar_intensity_surface
- fallback：composition.rs_index_report
- 标签：sar, change, planned-model
- 槽位：
  - `title`：required（title）@ top-center
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：required（legend, continuous_colorbar）@ bottom-left；bind_scope=all_thematic
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `graticule`：optional（graticule）@ none
  - `map_border`：required（map_border）@ none；preferred=frame/academic
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-landscape

## composition.temporal_change_report（Temporal Change Report）

- 描述：时序变化报告版式：发散变化面（中点=无变化）+ 统计面板 + 图表 + A4 版式。
- 版式：report；输出：interactive, png, pdf
- 兼容模型：change_comparison_map, diverging_choropleth
- fallback：composition.statistical_report
- 标签：temporal, change, report
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/report
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：required（legend, continuous_colorbar）@ bottom-left；bind_scope=all_thematic；preferred=legend/report
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `statistics_panel`：recommended（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `map_border`：required（map_border）@ none；preferred=frame/report
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-portrait

## composition.risk_exposure_report（Risk Exposure Report）

- 描述：风险暴露报告版式：风险分级面 + 统计面板 + 图表 + A4 版式；不确定性披露随 context 注入（live 与导出同链）。
- 版式：report；输出：interactive, png, pdf
- 兼容模型：risk_exposure_classes, vulnerability_index, hotspot_overlay
- fallback：composition.statistical_report
- 标签：risk, exposure, report
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/report
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：required（legend）@ bottom-left；bind_scope=all_thematic；preferred=legend/report
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `uncertainty_panel`：optional（uncertainty_panel）@ bottom-right；fallback_zones=bottom-left
  - `statistics_panel`：recommended（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `map_border`：required（map_border）@ none；preferred=frame/report
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-portrait

## composition.environment_monitoring（Environment Monitoring Map）

- 描述：环境监测版式：连续场 + colorbar + 图表 + A4 横版。
- 版式：standard；输出：interactive, png, pdf
- 兼容模型：raster_surface, spectral_index_surface, terrain_analytical_surface
- fallback：composition.density_exploration
- 标签：environment, monitoring, raster
- 槽位：
  - `title`：required（title）@ top-center
  - `legend`：required（continuous_colorbar）@ bottom-right；bind_scope=all_thematic；preferred=colorbar/horizontal
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right；fallback_zones=bottom-center
  - `attribution`：required（attribution）@ bottom-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `map_border`：optional（map_border）@ none
  - `export_layout`：optional（export_layout）@ none；preferred=export-layout/A4-landscape

## composition.zoning_plan_map（Zoning / Land-use Plan Map）

- 描述：区划/用地图版式：横向分类图例 + compact 归属 + 图框，规划成果的公开表达。
- 版式：report；输出：png, pdf, svg
- 兼容模型：zoning_planning, categorical_thematic
- fallback：composition.report_map
- 标签：planning, zoning, land-use
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/government
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：required（categorical_legend）@ bottom-left；bind_scope=all_thematic；preferred=categorical-legend/horizontal
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right；preferred=scale-bar/dual-unit
  - `attribution`：required（attribution）@ bottom-left；preferred=attribution/compact
  - `map_border`：required（map_border）@ none；preferred=frame/neatline
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-portrait

## composition.equity_assessment_report（Equity Assessment Report）

- 描述：公平性评估版式：发散评估面 + 方法论披露（分母/指标口径）+ 统计面板 + A4 版式。
- 版式：report；输出：interactive, png, pdf
- 兼容模型：equity_assessment, normalized_choropleth, diverging_choropleth
- fallback：composition.statistical_report
- 标签：equity, report, disclosure
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/report
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：required（legend）@ bottom-left；bind_scope=all_thematic；preferred=legend/report
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `methodology_note`：optional（methodology_note）@ bottom-left
  - `statistics_panel`：recommended（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `map_border`：required（map_border）@ none；preferred=frame/report
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-portrait

## composition.site_selection_decision（Site Selection Decision Map）

- 描述：选址决策版式：适宜性分级 + 决策面板（候选排名）可选 + 统计 + 图表 + A4 横版。
- 版式：report；输出：interactive, png, pdf
- 兼容模型：site_selection_result, suitability_classes, mcda_score_map
- fallback：composition.statistical_report
- 标签：decision, site-selection, report
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/report
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：required（legend）@ bottom-left；bind_scope=all_thematic；preferred=legend/report
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `decision_panel`：optional（decision_panel）@ top-left；fallback_zones=top-right
  - `methodology_note`：recommended（methodology_note）@ bottom-left
  - `statistics_panel`：recommended（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-landscape

## composition.mcda_score_report（MCDA Score Report）

- 描述：MCDA 评分报告版式：评分分级面 + 方法论披露（权重来源）+ 统计 + 图表。
- 版式：report；输出：interactive, png, pdf
- 兼容模型：mcda_score_map, suitability_classes, vulnerability_index
- fallback：composition.statistical_report
- 标签：decision, mcda, disclosure
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/report
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：required（legend）@ bottom-left；bind_scope=all_thematic；preferred=legend/report
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `methodology_note`：recommended（methodology_note）@ bottom-left
  - `statistics_panel`：recommended（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `map_border`：required（map_border）@ none；preferred=frame/report
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-portrait

## composition.government_report_map（Government Report Map）

- 描述：政府成果图版式：公文标题（加宽字距）+ neatline 图框 + 双单位比例尺 + A4 竖版，正式汇报产物。
- 版式：report；输出：png, pdf
- 兼容模型：administrative_choropleth, normalized_choropleth, diverging_choropleth, zoning_planning, risk_exposure_classes, equity_assessment
- fallback：composition.report_map
- 标签：government, formal, report
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/government
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left；preferred=subtitle/report
  - `legend`：required（legend, categorical_legend）@ bottom-left；bind_scope=all_thematic；preferred=legend/report
  - `north_arrow`：required（north_arrow）@ top-right；preferred=north-arrow/monochrome
  - `scale_bar`：required（scale_bar）@ bottom-right；preferred=scale-bar/dual-unit
  - `attribution`：required（attribution）@ bottom-left；preferred=attribution/compact
  - `map_border`：required（map_border）@ none；preferred=frame/neatline
  - `statistics_panel`：optional（statistics_panel）@ top-left
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-portrait

## composition.executive_summary_map（Executive Summary Map）

- 描述：执行摘要版式：演示标题 + KPI 指标卡 + 紧凑图例，一图一页的决策摘要。
- 版式：presentation；输出：interactive, png
- 兼容模型：administrative_choropleth, aggregate_grid, visual_heatmap, proportional_symbol, site_selection_result
- fallback：composition.presentation_map
- 标签：executive, kpi, presentation
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/presentation
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：conditional（legend, categorical_legend, continuous_colorbar）@ bottom-left；bind_scope=all_thematic；preferred=legend/compact
  - `north_arrow`：optional（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left；preferred=attribution/compact
  - `statistics_panel`：recommended（statistics_panel）@ top-left；preferred=statistics-panel/kpi

## composition.public_health_report（Public Health Report Map）

- 描述：公共卫生报告版式：分级面 + 统计 + 图表 + 方法论披露可选 + A4 竖版。
- 版式：report；输出：interactive, png, pdf
- 兼容模型：administrative_choropleth, normalized_choropleth, vulnerability_index, equity_assessment, hotspot_overlay
- fallback：composition.statistical_report
- 标签：public-health, report
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/report
  - `subtitle`：optional（subtitle）@ top-center；fallback_zones=top-left
  - `legend`：required（legend）@ bottom-left；bind_scope=all_thematic；preferred=legend/report
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `methodology_note`：optional（methodology_note）@ bottom-left
  - `statistics_panel`：recommended（statistics_panel）@ top-left
  - `chart_panel`：optional（chart_panel）@ top-left；fallback_zones=top-right
  - `map_border`：required（map_border）@ none；preferred=frame/report
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-portrait

## composition.natural_resource_inventory（Natural Resource Inventory Map）

- 描述：自然资源本底版式：分类面（地类/林相/权属）+ 横向图例 + 表格面板可选（interactive）+ A4 横版。
- 版式：report；输出：interactive, png, pdf
- 兼容模型：zoning_planning, categorical_thematic
- fallback：composition.report_map
- 标签：inventory, natural-resources, categorical
- 槽位：
  - `title`：required（title）@ top-center；preferred=title/report
  - `legend`：required（categorical_legend）@ bottom-left；bind_scope=all_thematic；preferred=categorical-legend/horizontal
  - `north_arrow`：required（north_arrow）@ top-right
  - `scale_bar`：required（scale_bar）@ bottom-right
  - `attribution`：required（attribution）@ bottom-left
  - `table_panel`：optional（table_panel）@ bottom-right
  - `map_border`：required（map_border）@ none；preferred=frame/report
  - `export_layout`：required（export_layout）@ none；preferred=export-layout/A4-landscape

