        # lists) so declared `crs` members reach gdf_from_features (#599).
        res = SpatialAnalyzer.spatial_join(
            data_left,
            data_right,
            join_type=join_type,
            predicate=predicate
        )
        return res.to_llm_response()

    @tool(registry, name="clip_layer",
           description="裁剪图层：仅保留位于指定遮罩图层（通常是行政边界）范围内的要素。适合解决『搜索结果超出了行政区范围』的问题，实现精准区域分析。",
           param_descriptions={
               "target_layer": "待裁剪的图层（点、线、面）GeoJSON 或引用(ref:xxx)",
               "mask_layer": "裁剪遮罩（通常是一个行政区面）GeoJSON 或引用(ref:xxx)",
           })
    def clip_layer(target_layer: Any, mask_layer: Any) -> dict:
        target = safe_parse_geojson(target_layer)
        mask = safe_parse_geojson(mask_layer)
        # #765: forward BOTH layers as parsed FeatureCollections — the
        # target was previously stripped to its features list (dropping its
        # declared `crs`) while the mask kept its FC (asymmetric).
        res = SpatialAnalyzer.clip(target, mask)
        return res.to_llm_response()

    @tool(registry, name="spatial_aggregate",
           description=(
               "空间聚合：统计落在每个多边形（如行政区）内的点位（如 POI）数量。"
               "✅ 用于：矢量点要素的计数聚合，返回带统计结果的多边形图层。"
               "\n❌ 不要用于：多边形内的栅格统计（人口/降雨/海拔）— 用 zonal_stats。"
           ),
           param_descriptions={
               "points": "点要素集 GeoJSON 或引用(ref:xxx)",
               "polygons": "多边形要素集（如行政区）GeoJSON 或引用(ref:xxx)",
               "count_field": "存储统计数量的字段名，默认 'point_count'",
           })
    def spatial_aggregate(points: Any, polygons: Any, count_field: str = "point_count") -> dict:
        pts = safe_parse_geojson(points)
        polys = safe_parse_geojson(polygons)
        # #764: count_field names the OUTPUT count column, it is not a point
        # attribute selector — forwarding it as value_field was inert for
        # stats=['count'] (and would silently change the aggregated metric if
        # the points carried a same-named field). Request only the count and
        # rename the output column afterwards.
        # #765: pass the parsed FeatureCollections (not bare features
        # lists) so declared `crs` members reach to_utm_gdf.
        res = SpatialAnalyzer.aggregate(
            pts,
            polys,
            stats=['count'],
        )
        if res.success and count_field and count_field != "count":
            for feat in ((res.data or {}).get("features", []) if isinstance(res.data, dict) else []):
                props = feat.setdefault("properties", {})
                if "count" in props:
                    props[count_field] = props.pop("count")
        return res.to_llm_response()

    @tool(registry, name="isochrone_network",
           description="等时线分析（路网模式）：基于路网计算从设施点出发在指定时间内可达的范围。需要输入路网要素。",
           tier=2, domains=["network"],
           args_model=IsochroneAnalysisArgs)
    def isochrone_network(network_layer: Any, facilities: Any, travel_time: float = 15, mode: str = "walking") -> dict:
        net = safe_parse_geojson(network_layer)
        facs = safe_parse_geojson(facilities)
        # #765: forward the parsed FeatureCollections so declared `crs`
        # members reach to_utm_gdf (calculate_isochrones).
        res = SpatialAnalyzer.isochrone_network(net, facs, travel_time, mode)
        return res.to_llm_response()

    @tool(registry, tier=2, domains=["statistics"], name="fishnet_grid",
           description=(
               "鱼网格网生成：在 bbox 内生成正方形或六边形覆盖网格 (空 cell，无属性)。"
               "\n何时用：作为 spatial_aggregate / spatial_join 的底图做空间统计；"
               "需要规则网格做密度可视化但不想用 H3 索引（如要导出兼容 ArcGIS 的 shp）。"
               "\n何时不用：(1) 仅需点的网格聚合 — 直接用 h3_binning（自带 H3 索引、性能更好）；"
               "(2) 要平滑等值面 — 用 kde_contours / idw_interpolation。"
               "\n关键约束：bounds=[west,south,east,north] WGS84；cell_size 单位米；"
               "大 bbox + 小 cell_size 会爆内存（>10⁶ 格警告）。"
           ),
           args_model=FishnetGridArgs)
    def fishnet_grid(bounds: List[float], cell_size: float, type: str = "square") -> dict:
        from app.lib.geo_analysis.aggregation import generate_fishnet
        res = generate_fishnet(bounds, cell_size, type)
        return res.to_llm_response()

    @tool(registry, tier=2, domains=["statistics"], name="central_feature",
           description="中心分析：寻找点集的中心位置。支持计算平均中心(mean_center)或寻找距离所有点最近的中心要素(central_feature)。",
           param_descriptions={
               "geojson": "点要素集 GeoJSON 或引用(ref:xxx)",
               "method": "方法: 'mean_center'(平均中心) 或 'central_feature'(中心要素)",
           })
    def central_feature(geojson: Any, method: str = "mean_center") -> dict:
        data = safe_parse_geojson(geojson)
        # #1110: forward the parsed FeatureCollection (not the bare features
        # list) so a declared `crs` member reaches to_utm_gdf —
        # mirrors #765 / GIS-682.
        res = SpatialAnalyzer.central_feature(data, method)
        return res.to_llm_response()

    @tool(registry, name="service_area_simple",
           description=(
               "简单服务区分析：按出行模式和时间生成可达范围。"
               "✅ 用于：沿出行速度估算的行程时间/距离可达范围（等时圈），"
               "如『某设施 15 分钟步行圈』。"
               "\n❌ 不要用于：简单直线半径缓冲 — 用 buffer_analysis。"
           ),
           tier=2, domains=["network"],
           param_descriptions={
               "geojson": "设施点要素集 GeoJSON 或引用(ref:xxx)",
               "travel_time_min": "出行时间（分钟），默认 15",
               "mode": "出行方式: 'walking'(默认, 5km/h), 'cycling'(15km/h), 'driving'(40km/h)",
               "dissolve": "是否合并所有点的服务区，默认 True",
           })
    def service_area_simple(geojson: Any, travel_time_min: float = 15, mode: str = "walking", dissolve: bool = True) -> dict:
        speeds = {"walking": 5.0, "cycling": 15.0, "driving": 40.0}
        speed = speeds.get(mode.lower(), 5.0)
        distance_m = (speed * 1000) * (travel_time_min / 60.0)
        data = safe_parse_geojson(geojson)
        # #765: forward the parsed FeatureCollection so a declared `crs`
        # member reaches buffer_smart's gdf_from_features.
        res = SpatialAnalyzer.buffer(data, distance=distance_m, unit="m", dissolve=dissolve)
        return res.to_llm_response()

    @tool(registry, name="h3_binning",
           description=(
               "H3 六边形网格聚合：把点数据聚合到指定分辨率的 H3 网格（代替传统鱼网）。"
               "✅ 用于：需要每个网格的统计值（计数/求和/均值）做数据驱动渲染，"
               "或作为 h3_lisa 空间聚类检验的前置步骤。"
               "\n❌ 不要用于：(1) 只想快速看分布趋势 — 用 heatmap_data(render_type='native')；"
               "(2) 需要平滑的连续密度面 — 用 kde_surface。"
           ),
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "点要素集 GeoJSON 或引用(ref:xxx)",
               "resolution": "H3分辨率（通常 6 到 9 之间，越大网格越小），例如 8",
               "stat_field": "可选：参与统计的字段名",
               "stat_method": "统计方法，如 'count'（默认）, 'sum', 'mean'",
           })
    @cached_tool(ttl=3600)
    def h3_binning(geojson: Any, resolution: int = 8, stat_field: str = None, stat_method: str = 'count') -> dict:
        from app.lib.geo_analysis.aggregation import h3_binning as _h3_binning
        data = safe_parse_geojson(geojson)
        res = _h3_binning(data, resolution, stat_field, stat_method)
        payload = res.to_llm_response()

        try:
            out_geojson = payload.get("data") if isinstance(payload, dict) else None
            # G-9（#873）：lib 层 sum/mean 缺有效 stat_field 时降级 count 并在
            # data 上标记 stat_method_effective —— 列名推断以它为准（此前
            # 按 stat_method 取列名会拿到不存在的列，legend_spec 静默缺失）。
            effective_method = "count"
            if isinstance(out_geojson, dict) and out_geojson.get("stat_method_effective"):
                effective_method = out_geojson["stat_method_effective"]
            elif stat_field and stat_method in ("sum", "mean"):
                effective_method = stat_method
            if effective_method != "count":
                stat_field_name = effective_method
            else:
                stat_field_name = "count"
            if (
