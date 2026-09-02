                stat_method in ("sum", "mean") and effective_method == "count"
                and isinstance(payload, dict)
            ):
                payload["correction_hint"] = (
                    f"stat_method={stat_method} 需要同时传 stat_field（数值列名）；"
                    f"本次已降级为 count 统计。"
                )
            if isinstance(out_geojson, dict):
                # Single canonical graduated-spec builder (ADR-0078): runs the
                # one classification algorithm, resolves palette colors through
                # one path (midpoint sampling, matching create_thematic_map), and
                # filters NaN/Inf once. Replaces the verbatim palette truncation
                # that diverged from every other graduated emitter.
                from app.lib.cartography.thematic_spec import build_graduated_spec
                spec = build_graduated_spec(
                    out_geojson, stat_field_name, method="quantiles", k=5, palette="YlOrRd"
                )
                if spec is not None and isinstance(payload, dict):
                    payload["legend_spec"] = spec
        except Exception as e:  # noqa: BLE001 — legend failure never blocks tool result
            import logging
            logging.getLogger(__name__).warning(f"[h3_binning] legend_spec construction failed: {e}")

        if isinstance(payload, dict) and isinstance(payload.get("data"), dict) and payload["data"].get("type") == "FeatureCollection":
            payload["data"] = trim_features(payload["data"])
        return payload

    @tool(registry, tier=2, domains=["statistics"], name="dissolve_layer",
           description=(
               "矢量融合 (Dissolve)：把相邻同属性的多边形/线合并为单一几何，可选按字段分组。"
               "\n何时用：(1) 把街道边界合并为区县轮廓；"
               "(2) 把同类用地（如『住宅』『商业』）的相邻地块合并；"
               "(3) overlay/intersect 后清理碎片；"
               "(4) 生成清洁的母图层用于 clip_layer。"
               "\n何时不用：(1) 只想统计每个多边形的属性 — 用 spatial_aggregate；"
               "(2) 要联合两个不同图层 — 用 overlay_analysis(how='union')；"
               "(3) 单纯按属性筛选 — 用 attribute_filter。"
               "\n关键约束：未给 field 时会把整个图层融合成 1 个要素；"
               "给定 field 后会按字段值分组，每组一个融合结果。"
           ),
           param_descriptions={
               "geojson": "输入图层 GeoJSON 或引用(ref:xxx)，几何类型应一致（全部 polygon 或全部 line）",
               "field": "可选属性字段名。若提供，按该字段的不同值分组分别融合；不提供则整体融合为单一要素",
           })
    def dissolve_layer(geojson: Any, field: Optional[str] = None) -> dict:
        from app.lib.geo_processor.geometry import dissolve_smart
        res = dissolve_smart(geojson, field=field)
        return res.to_llm_response()

    @tool(registry, tier=2, domains=["network"], name="nearest_facility",
           description=(
               "最近设施匹配：对每个源点找出目标集合中距离最近的目标，并标注距离（米）。"
               "\n何时用：『每户居民最近的医院/学校』『100 个 POI 最近的地铁站』『每个公交站最近的商圈』 — "
               "**双集合最近邻匹配的唯一工具**。"
               "\n何时不用：(1) 同一集合内的最近邻距离/聚集度 — 用 nearest_neighbor (单集合统计)；"
               "(2) 服务区/可达性 — 用 isochrone_analysis 或 service_area_simple；"
               "(3) 沿路网最近 — 当前是欧氏距离，沿路网路网最短路径暂不支持。"
               "\n返回：每个源点的副本，properties 新增最近目标标识与 distance_m；目标含 id/name/fid 字段时标识为 nearest_target_id（取其值），否则为 nearest_target_index（目标行号）。"
           ),
           param_descriptions={
               "source_points": "源点要素集 (GeoJSON 或 ref:xxx) — 每个点会找一个最近目标",
               "target_points": "目标点要素集 (GeoJSON 或 ref:xxx) — 候选设施集合",
           })
    def nearest_facility(source_points: Any, target_points: Any) -> dict:
        from app.lib.geo_analysis.network import nearest_neighbor_features
        res = nearest_neighbor_features(source_points, target_points)
        return res.to_llm_response()

    @tool(registry, name="raster_reclassify",
           description=(
               "栅格重分类：将连续栅格值按方案映射为离散类别。"
               "\n何时用：『把 NDVI 连续值分成低/中/高植被覆盖等级』；"
               "把高程分成平原/丘陵/山地；把坡度分成安全/中等/危险等级。"
               "\n何时不用：(1) 只是查看统计值 — 用 zonal_stats；"
               "(2) 两个栅格做运算 — 用 raster_calculator。"
               "\n关键约束：scheme 按 min→max 排序，首个匹配 wins；未匹配像素变 nodata。"
           ),
           tier=2, domains=["raster"],
           args_model=RasterReclassifyArgs)
    def raster_reclassify(raster_path: str, scheme: List[dict], nodata: Optional[float] = None) -> dict:
        res = SpatialAnalyzer.raster_reclassify(raster_path, scheme, nodata)
        return res.to_llm_response()

    @tool(registry, name="raster_calculator",
           description=(
               "栅格计算器：对两个栅格做像素级数学运算（A+B, A-B, (A-B)/(A+B) 等）。"
               "\n何时用：『NDVI = (NIR-Red)/(NIR+Red)』『DEM 差值 = A-B』『比值指数』；"
               "任意两个栅格的逐像素运算。"
               "\n何时不用：(1) 单栅格重分类 — 用 raster_reclassify；"
               "(2) 双时相变化检测（差值+阈值分类一步到位）— 用 detect_raster_change；"
               "(3) 需要地理加权（如 focal）— 未实现，先用 focal_stats。"
               "\n关键约束：expression 用 A/B 指代栅格；支持 numexpr 语法；自动 nodata 掩膜。"
               "A 是基准网格：分辨率/CRS 不一致的 B 会自动虚拟重投影对齐到 A（连续量 bilinear），"
               "对齐事实进 quality_evidence。"
           ),
           tier=2, domains=["raster"],
           args_model=RasterCalculatorArgs)
    def raster_calculator(raster_a: str, raster_b: Optional[str] = None, expression: str = "A + B", constant: Optional[float] = None, nodata: Optional[float] = None, resampling: Optional[str] = None) -> dict:
        res = SpatialAnalyzer.raster_calculator(raster_a, raster_b, expression, constant, nodata, resampling)
        return res.to_llm_response()

    @tool(registry, name="detect_raster_change",
           description=(
               "双时相栅格变化检测：对本地两个栅格工件（如两期 NDVI/分类/DEM 产物）"
               "做像元级变化检测，输出变化栅格 + 统计 + 质量证据。"
               "\n何时用：『对比这两个时期的 NDVI 栅格哪里变了』『两期分类图差异』；"
               "已上传/已生成两个 .tif 工件的变化分析。"
               "\n何时不用：(1) 无本地栅格、只有 bbox+日期 — 用 detect_vegetation_change（在线 STAC）；"
               "(2) 只要差值统计不要栅格 — temporal_raster；"
               "(3) 两个栅格做任意表达式运算 — raster_calculator。"
               "\n关键约束：raster_a（T1）是基准网格；B 分辨率/CRS 不一致时自动虚拟对齐"
               "（对齐/重采样/裁剪事实进 quality_evidence）；method 可选 "
               "difference/absolute_difference/normalized_difference；threshold 给定时"
               "输出二分类变化栅格（1=变化，0=稳定，255=nodata）；分类图输入时"
               "传 resampling=nearest（默认 bilinear 仅适用连续量）。"
           ),
           tier=2, domains=["raster"])
    def detect_raster_change(raster_a: str, raster_b: str, method: str = "difference", threshold: Optional[float] = None, band: int = 1, resampling: Optional[str] = None) -> dict:
        res = SpatialAnalyzer.raster_change(raster_a, raster_b, method=method, threshold=threshold, band=band, resampling=resampling)
        return res.to_llm_response()

    @tool(registry, name="raster_resample",
           description=(
               "栅格重采样：改变像元大小和/或 CRS。"
               "\n何时用：『把 30m DEM 重采样到 90m 做概览』『把 WGS84 栅格转到 UTM 做面积计算』；"
               "不同分辨率/投影的栅格对齐前预处理。"
               "\n何时不用：(1) 只改元数据 — 用 gdal_translate 更快；"
               "(2) 已经同分辨率同 CRS — 不需要重采样。"
               "\n关键约束：resampling 可选 bilinear/nearest/cubic/mode/average。"
           ),
           tier=2, domains=["raster"],
           args_model=RasterResampleArgs)
    def raster_resample(raster_path: str, target_resolution: float, target_crs: Optional[str] = None, resampling: str = "bilinear") -> dict:
        res = SpatialAnalyzer.raster_resample(raster_path, target_resolution, target_crs, resampling)
        return res.to_llm_response()
