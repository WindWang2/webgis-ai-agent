"""Semantic Tool Retrieval V6（ADR-0119 决策 D1/D2）—— hybrid 检索 + 置信度。

V5 基线（`.agent-work/harness-v6/00-baseline.md` G1）：
- `tool_surface_v3._SEMANTIC_RETRIEVER_SPEC` hook 存在但无默认实现（生产恒词法）；
- 检索信号为朴素加法（lexical + capability + 画像域），无方法论证据通道；
- 无置信度/弃权 —— 低置信度时照常返回 top-k（静默乱选）；
- 开环金标 p@1=0.6515 / invalid=0.3333（hard_negative 内 0.5）。

V6 hybrid 四路信号（全部确定性、零模型依赖、additive —— 不替代词法 baseline）：

1. **lexical**（既有 `tool_retrieval.rank_tools`，baseline 恒在）；
2. **semantic-lexical**：双语同义/近义扩展词表（口语↔术语桥：克里金↔kriging、
   等时圈↔isochrone……）。扩展词独立跑一次词法打分后**降权融合**
   （``_EXPANSION_FUSION``）—— base 分不被扩展词污染，桥接只补召回；
3. **capability graph**：口语短语 → capability id 精确反查（139 能力词表的
   别名子集，紧词表防过匹配）→ capability_tool_map 加成；
4. **methodology evidence**：query 与 workflow_v4 MethodologyRegistry 12 方法族
   的双语路由词命中 → 族内候选方法的 capabilities/algorithm_ids → 工具候选
   加成（**只作检索信号**，不产生计划/第二 planner —— 方法族裁决仍归
   workflow 编译器）。

可选第 5 路 **embedding**：``TOOL_RETRIEVAL_SEMANTIC`` 默认指向本模块
``embedding_retriever``（faiss/RAG 同款 sentence-transformers，registry 指纹
缓存索引，懒加载 + 有界失败降级词法）；``TOOL_RETRIEVAL_EMBEDDING=0`` 关停。
无模型环境**零影响**（词法+2/3/4 路完整工作）。

置信度与弃权（D2）：排序后由 (margin(top1,top2), 绝对分数水平, 证据通道数)
校准出 ``confidence ∈ [0,1]``；低于钉死阈值 → ``abstained=True`` + 弃权理由
披露（selection.selection_trace）。弃权语义 = **不假装确定**：面照常投影
（消费方决定收缩/保留），生产 seam（pi_native_surface）在弃权时不注入动态面
并披露，保留 list_available_tools 两跳通道。绝不静默乱选。
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)

#: V6 kill switch：置 0 → hybrid/置信度全部停用，行为与 V5 逐位一致。
def v6_retrieval_enabled() -> bool:
    return os.getenv("GIS_TOOL_RETRIEVAL_V6", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


# ---------------------------------------------------------------------------
# 通道 2：双语同义扩展词表（口语 ↔ registry 术语桥）。
# 匹配规则：zh 键按子串包含（与 methodology 路由词同策略 —— 紧词表防过
# 匹配）；ASCII 键按词边界小写词命中。值 = 扩展进二次词法检索的术语
# （registry 工具名/tags/descriptions 的实际词汇，绝不虚构工具名）。
# ---------------------------------------------------------------------------
_SYNONYM_PAIRS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    # ── 空间插值 / 地统计 ──
    ("插值", ("interpolation", "surface", "kriging", "idw")),
    ("铺成面", ("interpolation", "surface")),
    ("连续表面", ("interpolation", "surface")),
    ("克里金", ("kriging", "variogram")),
    ("kriging", ("克里金", "variogram")),
    ("协同克里金", ("cokriging",)),
    ("协克里金", ("cokriging",)),
    ("协变量", ("covariate", "cokriging", "regression_kriging")),
    ("三角网", ("tin", "triangulation")),
    ("tin", ("三角网", "triangulation")),
    ("反距离", ("idw", "interpolation")),
    ("idw", ("反距离", "interpolation")),
    ("趋势面", ("trend_surface",)),
    ("方差", ("variance", "kriging")),
    ("变异函数", ("variogram",)),
    ("variogram", ("变异函数",)),
    # ── 聚类 / 热点 / 空间自相关 ──
    ("聚类", ("cluster", "dbscan")),
    ("cluster", ("聚类",)),
    ("分簇", ("cluster", "dbscan")),
    ("聚集", ("cluster", "geary", "moran")),
    ("密度聚类", ("dbscan", "spatial_cluster")),
    ("分布格局", ("distribution", "pattern", "cluster")),
    ("热点", ("hotspot", "getis")),
    ("hotspot", ("热点",)),
    ("冷点", ("hotspot", "getis")),
    ("空间自相关", ("moran", "geary", "autocorrelation")),
    ("自相关", ("moran", "autocorrelation")),
    ("moran", ("自相关", "moran")),
    ("显著性", ("significance", "p_value")),
    ("时空", ("spatiotemporal", "space_time")),
    ("时空密度", ("st_dbscan", "space_time")),
    # ── 缓冲 / 邻近 / 可达 ──
    ("缓冲区", ("buffer",)),
    ("buffer", ("缓冲区",)),
    ("服务范围", ("buffer", "service_area")),
    ("等时圈", ("isochrone", "service_area")),
    ("isochrone", ("等时圈",)),
    ("可达", ("accessibility", "isochrone", "service_area")),
    ("步行", ("walk", "service_area")),
    ("骑行", ("bike", "service_area")),
    ("路网", ("network", "road")),
    ("周边", ("around", "nearby", "neighborhood")),
    ("附近", ("around", "nearby")),
    # ── 地形 / 水文 ──
    ("坡度", ("slope", "terrain")),
    ("坡向", ("aspect", "terrain")),
    ("地形", ("terrain", "dem")),
    ("高程", ("dem", "elevation")),
    ("dem", ("高程", "elevation")),
    ("河网", ("stream", "river")),
    ("流域", ("watershed", "basin")),
    ("汇水", ("watershed", "flow")),
    ("填洼", ("depression", "fill")),
    ("流向", ("flow_direction", "flow")),
    ("可视域", ("viewshed",)),
    ("viewshed", ("可视域",)),
    ("天际线", ("skyline", "openness")),
    ("天空开阔度", ("sky_view_factor", "openness")),
    # ── 遥感 / 影像 ──
    ("影像", ("image", "imagery", "remote_sensing")),
    ("卫星", ("sentinel", "satellite")),
    ("两期", ("change_detection", "multi_temporal")),
    ("两季", ("change_detection", "multi_temporal")),
    ("前后对比", ("change_detection",)),
    ("ndvi", ("植被指数", "vegetation")),
    ("植被指数", ("ndvi", "vegetation_index")),
    ("变化检测", ("change_detection",)),
    ("云", ("cloud", "qc")),
    ("掩膜", ("mask",)),
    ("纹理", ("texture", "glcm")),
    ("speckle", ("斑点", "滤波")),
    ("分类", ("classification", "segment")),
    ("分割", ("segment", "classification")),
    ("光谱", ("spectral",)),
    # ── 矢量操作 ──
    ("裁剪", ("clip",)),
    ("clip", ("裁剪",)),
    ("合并", ("merge", "dissolve", "union")),
    ("融合", ("dissolve",)),
    ("相交", ("intersect", "overlay")),
    ("叠加", ("overlay",)),
    ("交汇", ("overlay", "join")),
    ("连接", ("join",)),
    ("质心", ("centroid",)),
    ("中心点", ("centroid",)),
    ("泰森", ("voronoi", "thiessen")),
    ("voronoi", ("泰森",)),
    ("凸包", ("convex_hull",)),
    ("渔网", ("fishnet", "grid")),
    ("网格", ("grid", "fishnet")),
    ("投影", ("project", "reproject", "crs")),
    ("坐标系", ("crs", "coordinate")),
    ("wgs84", ("坐标系", "crs")),
    ("坐标系转换", ("transform_coordinates", "reproject")),
    # ── 统计 / 时序 / 分区 ──
    ("分区统计", ("zonal",)),
    ("zonal", ("分区统计",)),
    ("汇总", ("aggregate", "summary")),
    ("聚合", ("aggregate",)),
    ("趋势", ("trend",)),
    ("斜率", ("slope", "trend")),
    ("突变", ("changepoint",)),
    ("跳变", ("changepoint",)),
    ("季节", ("seasonal",)),
    ("分解", ("decompose",)),
    ("回归", ("regression", "gwr")),
    ("地理加权", ("gwr", "regression")),
    ("因子", ("factor", "geodetector")),
    ("解释力", ("geodetector", "q_statistic")),
    ("地理探测器", ("geodetector",)),
    ("异常值", ("outlier", "anomaly")),
    ("离群", ("outlier",)),
    ("标准差椭圆", ("standard_deviational_ellipse", "directional")),
    ("方向分布", ("standard_deviational_ellipse",)),
    ("标准距离", ("standard_distance",)),
    # ── 数据获取 / 管理 ──
    ("POI", ("poi", "interest")),
    ("兴趣点", ("poi",)),
    ("咖啡馆", ("poi",)),
    ("地理编码", ("geocode",)),
    ("geocode", ("地理编码", "地址")),
    ("地址", ("geocode", "address")),
    ("逆地理", ("reverse_geocode",)),
    ("上传", ("upload", "ingest")),
    ("批量", ("batch", "bulk")),
    ("数据源", ("data_source", "dataset")),
    ("摸底", ("profile", "overview")),
    ("画像", ("profile",)),
    ("元数据", ("metadata", "schema", "describe")),
    ("schema", ("元数据", "结构")),
    ("质量", ("quality", "audit")),
    ("质检", ("quality", "audit")),
    ("拓扑", ("topology", "geometry")),
    ("瓦片", ("tile", "cog")),
    ("cog", ("瓦片", "cloud_optimized")),
    # ── 制图 / 出图 ──
    ("出图", ("export", "map", "thematic")),
    ("导出", ("export",)),
    ("专题图", ("thematic", "map")),
    ("图例", ("legend", "layout")),
    ("指北针", ("north_arrow", "layout")),
    ("配色", ("style", "color", "symbology")),
    ("样式", ("style", "symbology")),
    ("底图", ("base_layer", "basemap")),
    ("图层", ("layer",)),
    ("图表", ("chart",)),
    ("报表", ("report",)),
    ("监测报告", ("monitoring", "report")),
    ("快照", ("snapshot", "checkpoint")),
    ("回滚", ("rollback",)),
    ("3d", ("三维", "extrusion")),
    ("三维", ("3d", "extrusion")),
    ("拉伸", ("extrusion",)),
    ("等值线", ("contour",)),
    ("contour", ("等值线",)),
    ("热力图", ("heatmap", "kde")),
    ("密度图", ("kde", "heatmap", "density")),
    ("密度", ("density", "kde")),
    ("核密度", ("kde", "density")),
    ("kde", ("核密度",)),
    # ── 区划 / 行政区 ──
    ("行政区", ("admin", "district", "boundary")),
    ("区县", ("district", "admin")),
    ("街道", ("sub_district", "town", "admin")),
    ("边界", ("boundary",)),
    ("人口", ("population", "stats")),
    # ── 网络 / 区位 ──
    ("最近设施", ("closest_facility",)),
    ("选址", ("location_allocation",)),
    ("配送", ("distance_matrix", "route")),
    (" OD", ("origin_destination", "od")),
    ("od", ("origin_destination",)),
    ("路径", ("route", "path")),
    ("导航", ("route", "navigation")),
    ("公交", ("transit",)),
    ("地铁", ("metro", "transit")),
    # ── 工作流 / 执行 ──
    ("工作流", ("workflow",)),
    ("工作流语义", ("workflow", "compile")),
    ("执行计划", ("execution_plan",)),
    ("重跑", ("rerun",)),
    ("取消", ("cancel",)),
    ("模拟", ("simulate", "what_if")),
    ("情景", ("scenario",)),
    ("决策", ("decision",)),
    ("多准则", ("multi_criteria", "decision")),
    ("适宜性", ("suitability",)),
    # --- V6 失败分析补充：zh 领域词 → en 名词片段（ASCII 名直命中）---
    ("产物", ("artifact",)),
    ("血缘", ("lineage",)),
    ("溯源", ("lineage", "provenance")),
    ("清单", ("inventory", "list")),
    ("都有哪些", ("list", "inventory")),
    ("模板", ("template",)),
    ("批次", ("execution", "run")),
    ("重新执行", ("rerun",)),
    ("重试", ("rerun", "retry")),
    ("初始化", ("init", "project")),
    ("校验", ("validate",)),
    ("合法性校验", ("validate", "validate_execution_plan")),
    ("角色", ("role",)),
    ("元信息", ("describe", "artifact")),
    ("目录", ("catalog",)),
    ("连通性", ("inspect", "connect")),
    ("接入", ("connect", "data_source")),
    ("六边形", ("h3", "hexagon")),
    ("包迹线", ("envelope", "ripley")),
    ("传染", ("knox", "space_time")),
    ("竞争", ("voronoi", "thiessen")),
    ("视线", ("viewshed",)),
    ("主成分", ("pca", "principal")),
    ("面向对象", ("segment", "object")),
    ("重采样", ("resample", "resolution")),
    ("定标", ("calibrate",)),
    ("抑斑", ("speckle",)),
    ("斑点噪声", ("speckle",)),
    ("相干性", ("coherence",)),
    ("等效视数", ("enl",)),
    ("纹理熵", ("glcm", "texture")),
    ("极化比值", ("vh", "ratio")),
    ("叠掩", ("layover", "shadow")),
    ("热噪声", ("thermal", "noise")),
    ("取对数", ("log", "db")),
    ("多视", ("multilook", "speckle")),
    ("检查点", ("checkpoint", "rollback")),
    ("存档", ("snapshot", "checkpoint")),
    ("快照存档", ("snapshot",)),
    ("变化曲线", ("profile", "curve")),
    ("逐日变化", ("profile", "daily")),
    ("时间聚合", ("temporal", "aggregate")),
    ("聚合成季度", ("temporal", "quarter")),
    ("时间筛选", ("temporal", "filter")),
    ("时间窗口", ("temporal", "window")),
    ("汛期", ("season", "filter")),
    ("贝叶斯", ("smoothing", "rate")),
    ("收缩估计", ("smoothing", "rate")),
    ("样本量太少", ("sample", "smoothing")),
    ("村庄", ("village", "township")),
    ("乡镇", ("township",)),
    ("所在地", ("center", "centroid")),
    ("边界面", ("boundary", "polygon")),
    ("下载边界", ("sub_district", "boundary")),
    ("flip视角拉远", ("zoom", "view")),
    ("俯仰", ("pitch", "view")),
    ("朝向", ("heading", "bearing", "view")),
    ("锁定相机", ("set_map_view", "view")),
    ("冻结视角", ("set_map_view",)),
)

#: zh 子串键与 ASCII 词键分离（匹配语义不同）。
_ZH_SYNONYMS: Tuple[Tuple[str, Tuple[str, ...]], ...] = tuple(
    (k, v) for k, v in _SYNONYM_PAIRS if re.search(r"[\u4e00-\u9fff]", k)
)
_ASCII_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    k.lower(): v for k, v in _SYNONYM_PAIRS
    if not re.search(r"[\u4e00-\u9fff]", k)
}

#: 扩展词上限（bounded everything；超长查询不产生无界扩展）。
_MAX_EXPANDED_TERMS = 24

_ASCII_WORD_RE = re.compile(r"[a-zA-Z0-9_]{2,}")


def expand_query_terms(query: str) -> Tuple[str, ...]:
    """查询 → 有序去重的扩展术语表（确定性；空查询 → 空）。

    zh 键子串包含、ASCII 键词边界命中；扩展值不回环再扩展（一层桥接，
    防词表互相引爆）；总量 ≤ _MAX_EXPANDED_TERMS。
    """
    q = (query or "").strip()
    if not q:
        return ()
    q_l = q.lower()
    words = {m.group(0).lower() for m in _ASCII_WORD_RE.finditer(q_l)}
    out: List[str] = []
    seen: set = set()

    def _add(term: str) -> None:
        t = term.strip().lower()
        if t and t not in seen and t not in q_l:
            seen.add(t)
            out.append(t)

    for key, expansions in _ZH_SYNONYMS:
        if key in q:
            for e in expansions:
                _add(e)
    for w in sorted(words):  # 排序 → 确定性
        for e in _ASCII_SYNONYMS.get(w, ()):
            _add(e)
    return tuple(out[:_MAX_EXPANDED_TERMS])


# ---------------------------------------------------------------------------
# 否定感知反证（negation anti-evidence）：口语常用「没有X / 无X / 不需要X」
# 排除语义 —— X 指向的工具族应**降权**而非照常召回。确定性规则：否定线索
# + 其后紧邻名词短语（zh ≤8 字 / ASCII 下一词）→ 反证词。反证词在
# select() 中对 name/tags/descriptors 语料命中的候选减分（只降不剔）。
# ---------------------------------------------------------------------------
_NEGATION_CUES: Tuple[str, ...] = (
    "没有", "不带", "不需要", "无需", "不含", "排除", "无", "非",
)
_NEGATION_WINDOW = 8     # zh 名词短语最大长度
#: 否定短语 → 检索域词（**仅反证通道**使用；进正向扩展表会污染带「时间」
#: 的正向查询 —— 实测 EV-N02a/EV-H09 退化）。
_NEGATION_PHRASE_EXPANSIONS: Dict[str, Tuple[str, ...]] = {
    "时间": ("time", "temporal", "时空", "spatiotemporal"),
    "时间戳": ("timestamp", "time", "时空"),
    "坐标": ("crs", "coordinate"),
    "网络": ("network", "road"),
}
_MAX_NEGATION_TERMS = 6
_NEGATION_PENALTY = 3.0


def negation_anti_terms(query: str) -> Tuple[str, ...]:
    """查询 → 反证词表（确定性；无否定线索 → 空）。

    「只要X，没有Y」→ ("y",)；「无时间戳的订单聚类」→ ("时间",)。
    短语边界：zh 取线索后紧邻汉字串（≤8 字，遇标点/空格断）；ASCII 取
    线索后下一个词。映射为检索域词：zh 短语原样 + 若在扩展表中命中键则
    并入其英文扩展（「时间」→ time/spatiotemporal）。
    """
    q = (query or "").strip()
    if not q:
        return ()
    out: List[str] = []
    seen: set = set()
    for cue in _NEGATION_CUES:
        start = 0
        while True:
            idx = q.find(cue, start)
            if idx < 0:
                break
            start = idx + len(cue)
            rest = q[start:]
            if not rest:
                break
            ch = rest[0]
            if re.match(r"[\u4e00-\u9fff]", ch):
                m = re.match(r"[\u4e00-\u9fff]{1,%d}" % _NEGATION_WINDOW, rest)
                phrase = m.group(0) if m else ""
                if phrase and phrase not in seen:
                    seen.add(phrase)
                    out.append(phrase)
                    en_ex: Tuple[str, ...] = ()
                    for key, exp in _NEGATION_PHRASE_EXPANSIONS.items():
                        if key in phrase:
                            en_ex = exp
                            break
                    for en in en_ex:
                        if en not in seen:
                            seen.add(en)
                            out.append(en)
                break  # 一线索一短语（首个）
            m = _ASCII_WORD_RE.match(rest.lstrip())
            if m and m.group(0).lower() not in seen:
                w = m.group(0).lower()
                seen.add(w)
                out.append(w)
            break
        if len(out) >= _MAX_NEGATION_TERMS:
            break
    return tuple(out[:_MAX_NEGATION_TERMS])


# ---------------------------------------------------------------------------
# 通道 3：capability 别名（口语短语 → capability id 精确反查）。
# 紧词表：只收高频口语桥（capability 全集 139 个由 active_capabilities
# 既有通道承载；本表只补「用户口语里直接命中能力」的桥）。
# ---------------------------------------------------------------------------
_CAPABILITY_ALIASES: Tuple[Tuple[str, str], ...] = (
    ("缓冲区", "geometry_buffer"),
    ("服务范围圈", "proximity_buffer"),
    ("直线距离范围", "proximity_buffer"),
    ("多环缓冲", "multi_ring_buffer"),
    ("直线服务范围", "geometry_buffer"),
    ("周边搜索", "poi_query"),
    ("周边设施", "poi_query"),
    ("质量审计", "dataset_profiling_quality"),
    ("数据质量", "dataset_profiling_quality"),
    ("按行政区统计", "zonal_statistics"),
    ("按区县统计", "zonal_statistics"),
    ("栅格分区", "zonal_statistics"),
    ("区内计数", "admin_aggregation"),
    ("每个区里有多少", "admin_aggregation"),
    ("每个街道", "admin_aggregation"),
    ("导出成图", "map_export_publishing"),
    ("导出地图", "map_export_publishing"),
    ("出版级", "map_export_publishing"),
    ("插值", "spatial_interpolation"),
    ("克里金", "block_kriging"),
    ("密度面", "density_surface"),
    ("核密度", "kde_density"),
    ("热点分析", "getis_ord_gi_star"),
    ("时空热点", "emerging_hotspot_analysis"),
    ("空间自相关", "global_morans_i"),
    ("局部自相关", "local_morans_i"),
    ("时空聚类", "spatiotemporal_clustering"),
    ("分区统计", "zonal_statistics"),
    ("地理探测器", "geographical_detector"),
    ("变化检测", "change_detection"),
    ("等时圈", "accessibility"),
    ("可达性", "accessibility"),
    ("选址", "location_allocation"),
    ("最近设施", "closest_facility"),
    ("配送矩阵", "od_matrix"),
    ("河网提取", "terrain_hydrology"),
    ("流域划分", "terrain_hydrology"),
    ("坡度坡向", "terrain_derivatives"),
    ("地形因子", "terrain_derivatives"),
    ("可视域", "terrain_viewshed"),
    ("坐标转换", "crs_transformation"),
    ("地理编码", "geocoding"),
    ("批量地址", "geocoding"),
    ("植被指数", "ndvi"),
    (" NDVI", "ndvi"),
    ("ndvi", "ndvi"),
    ("影像分割", "image_segmentation"),
    ("叠加分析", "geometry_overlay"),
    ("裁剪", "geometry_clip"),
    ("合并行政区", "geometry_dissolve"),
    ("渔网格网", "grid_binning"),
    ("生成网格", "grid_binning"),
    ("空间连接", "spatial_join"),
    ("面积插值", "areal_interpolation"),
    ("人口栅格化", "areal_interpolation"),
    ("变异函数", "variogram_analysis"),
    ("地统计模拟", "geostatistical_simulation"),
    ("标准差椭圆", "directional_distribution_analysis"),
    # --- V6 失败分析补充 ---
    ("局部空间自相关", "local_morans_i"),
    ("六边形自相关", "local_morans_i"),
    ("是不是随机", "point_pattern_analysis"),
    ("空间分布随机", "point_pattern_analysis"),
    ("包迹线检验", "nearest_neighbor_functions"),
    ("时空传染", "space_time_interaction"),
    ("先后关系", "space_time_interaction"),
    ("水文分析流程", "terrain_hydrology_advanced"),
    ("能看到多远", "terrain_viewshed"),
    ("通视", "terrain_viewshed"),
    ("天空开阔度", "terrain_sky_view"),
    ("遮蔽角", "terrain_sky_view"),
    ("主成分变换", "raster_dimensionality_reduction"),
    ("波段压缩", "raster_dimensionality_reduction"),
    ("面向对象分类", "image_segmentation"),
    ("切成同质对象", "image_segmentation"),
    ("分割成对象", "image_segmentation"),
    ("辐射定标", "sar_radiometric_calibration"),
    ("斑点滤波", "sar_speckle_filtering"),
    ("多视处理", "sar_speckle_filtering"),
    ("抑斑", "sar_speckle_filtering"),
    ("相干性图", "sar_coherence"),
    ("纹理提取", "sar_texture"),
    ("叠掩阴影", "sar_terrain_geometry_correction"),
    ("SAR时序", "temporal_composite"),
    ("无云合成", "temporal_composite"),
    ("时间聚合", "temporal_aggregate"),
    ("聚合成季度", "temporal_aggregate"),
    ("时间筛选", "temporal_filtering"),
    ("只保留某段时间", "temporal_filtering"),
    ("变化曲线", "temporal_profile"),
    ("逐日曲线", "temporal_profile"),
    ("网格内平均", "zonal_statistics"),
    ("网格内统计", "zonal_statistics"),
    ("下辖街道", "admin_boundary_query"),
    ("下面有哪些街道", "admin_boundary_query"),
    ("乡镇边界", "admin_boundary_query"),
    ("挂接数据源", "data_source_pipeline"),
    ("接入数据源", "data_source_pipeline"),
    ("工作流模板", "plan_workflow_orchestration"),
    ("工作流语义编译", "plan_workflow_orchestration"),
    ("重新执行批次", "plan_workflow_orchestration"),
    ("执行批次", "plan_workflow_orchestration"),
    ("计划合法性", "plan_workflow_orchestration"),
    ("空间插值方案比选", "interpolation_model_selection"),
    ("地理加权", "gwr"),
    ("GWR", "gwr"),
    ("gwr", "gwr"),
    ("rates收缩", "rate_smoothing"),
    ("发病粗率", "rate_smoothing"),
    (" MAD", "raster_change_detection"),
    ("mad", "raster_change_detection"),
    ("质量筛查", "cloud_qc_advisory"),
    ("影像质检", "cloud_qc_advisory"),
    ("云掩膜", "cloud_qc_advisory"),
    ("单期质检", "cloud_qc_advisory"),
    ("选片", "raster_source"),
    ("挑一景", "raster_source"),
    ("下载影像", "raster_source"),
)

#: capability 别名命中加成（与既有 _CAPABILITY_HIT_SCORE 同量级、略低 ——
#: 别名是口语桥，弱于显式 capability id）。
_CAPABILITY_ALIAS_BOOST = 4.0
_MAX_CAPABILITY_ALIASES = 12


# ---------------------------------------------------------------------------
# 通道 4：methodology evidence（12 方法族路由词 → 候选方法 → 工具）。
# 只读消费 workflow_v4 MethodologyRegistry（审定表；本模块不建第二词表）。
# ---------------------------------------------------------------------------
_METHODOLOGY_FAMILY_BOOST = 3.0
_METHODOLOGY_RUNNER_UP_BOOST = 1.0
_MAX_METHODOLOGY_TOOLS = 12


def _ascii_words(text: str) -> set:
    return {m.group(0).lower() for m in _ASCII_WORD_RE.finditer(text or "")}


def methodology_signal_tools(query: str) -> Tuple[str, Dict[str, float]]:
    """query → (命中族, 工具→加成分)（确定性；零命中 → ("", {})）。

    路由词打分与 ``methodology._family_keyword_score`` 同策略（长短语
    权重更高 = 命中词长度和；zh 子串、en 词边界）。只取第一名族（同分
    按词表序）+ 亚军弱加成 —— 不做多族扩散，防检索面被稀释。
    **方法优先级加权**：族内候选方法按声明 priority（越小越优先）排序，
    首选方法 ×1.0、次选 ×0.6、其余 ×0.4 —— 方法论 boost 尊重审定表的
    专业首选序，不把全族工具无差别抬升。
    """
    q = (query or "").strip()
    if not q:
        return "", {}
    q_l = q.lower()
    words = _ascii_words(q_l)
    try:
        from app.services.gis_harness.workflow_v4.methodology import (
            get_methodology_registry,
        )

        families = get_methodology_registry().families()
    except Exception:  # noqa: BLE001 — 方法族缺席 → 零贡献（不阻断）
        return "", {}
    scored: List[Tuple[int, int, Any]] = []
    for idx, fam in enumerate(families):
        score = 0
        for kw in fam.keywords_zh:
            if kw and kw in q_l:
                score += len(kw)
        for kw in fam.keywords_en:
            k = kw.strip().lower()
            if not k:
                continue
            if " " in k or "_" in k:
                if k in q_l:
                    score += len(k)
            elif k in words:
                score += len(k)
        if score > 0:
            scored.append((score, -idx, fam))
    if not scored:
        return "", {}
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    primary = scored[0][2]
    runner_up = scored[1][2] if len(scored) > 1 else None

    boosts: Dict[str, float] = {}

    def _collect(fam: Any, family_weight: float) -> None:
        try:
            from app.lib.gis.algorithm_registry import get_algorithm_registry

            algo_reg = get_algorithm_registry()
            cap_tools = capability_tool_map_cached(algo_reg)
        except Exception:  # noqa: BLE001
            return
        methods = sorted(
            fam.candidate_methods,
            key=lambda m: (int(getattr(m, "priority", 50)),
                           str(getattr(m, "method_id", ""))),
        )
        for rank, method in enumerate(methods):
            w = family_weight * (1.0 if rank == 0 else (0.6 if rank == 1 else 0.4))
            if w <= 0.0:
                continue
            sources: List[List[str]] = []
            for cap in method.capabilities:
                sources.append(list(cap_tools.get(cap, ())))
            for aid in method.algorithm_ids:
                algo = algo_reg.get(aid)
                if algo is not None:
                    sources.append(list(algo.tool_candidates))
            for tool in [t for src in sources for t in src]:
                if not tool:
                    continue
                if w > boosts.get(tool, 0.0):
                    boosts[tool] = round(min(w, _METHODOLOGY_FAMILY_BOOST), 4)

    _collect(primary, _METHODOLOGY_FAMILY_BOOST)
    if runner_up is not None:
        _collect(runner_up, _METHODOLOGY_RUNNER_UP_BOOST)
    ordered = dict(sorted(boosts.items(), key=lambda kv: (-kv[1], kv[0]))
                   [:_MAX_METHODOLOGY_TOOLS])
    return primary.family_id, ordered


# ---------------------------------------------------------------------------
# Hybrid 装配：扩展词二次词法分（降权融合）+ 别名/方法论 boosts。
# ---------------------------------------------------------------------------
#: 扩展词词法分融合系数（base 分不被扩展词污染；桥接只补召回）。
_EXPANSION_FUSION = 0.5


@dataclass
class HybridSignals:
    """一次 hybrid 检索的全部确定性中间产物（可解释/可评测）。"""

    expanded_terms: Tuple[str, ...] = ()
    #: 否定反证词（「没有X」→ X 域词；select() 对语料命中候选减分）
    anti_terms: Tuple[str, ...] = ()
    capability_alias_hits: Tuple[str, ...] = ()
    methodology_family: str = ""
    #: 工具 → 通道证据（capability_alias / methodology / expansion_top）
    evidence: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    #: 直接加成分（alias/methodology；扩展词分走二次词法融合，不在此表）
    boosts: Dict[str, float] = field(default_factory=dict)


def hybrid_signals(registry: Any, query: str) -> HybridSignals:
    """计算通道 3/4 的确定性信号（通道 2 由调用方二次词法融合）。

    任何子通道失败 → 该通道零贡献（绝不阻断检索）。
    """
    sig = HybridSignals()
    q = (query or "").strip()
    if not q:
        return sig
    sig.expanded_terms = expand_query_terms(q)
    sig.anti_terms = negation_anti_terms(q)

    # --- 通道 3：capability 别名 ---
    alias_hits: List[str] = []
    boosts: Dict[str, float] = {}
    try:
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        cap_tools = capability_tool_map_cached(get_algorithm_registry())
    except Exception:  # noqa: BLE001
        cap_tools = {}
    for phrase, cap in _CAPABILITY_ALIASES:
        if len(alias_hits) >= _MAX_CAPABILITY_ALIASES:
            break
        hit = phrase.strip()
        matched = (hit in q) if re.search(r"[\u4e00-\u9fff]", hit) else (
            hit.lower() in _ascii_words(q))
        if not matched:
            continue
        alias_hits.append(cap)
        for tool in cap_tools.get(cap, ()):
            boosts[tool] = boosts.get(tool, 0.0) + _CAPABILITY_ALIAS_BOOST
            ev = sig.evidence.setdefault(tool, ())
            if "capability_alias" not in ev:
                sig.evidence[tool] = tuple(ev) + ("capability_alias",)
    sig.capability_alias_hits = tuple(dict.fromkeys(alias_hits))

    # --- 通道 4：methodology evidence ---
    fam_id, method_boosts = methodology_signal_tools(q)
    sig.methodology_family = fam_id
    for tool, weight in method_boosts.items():
        boosts[tool] = boosts.get(tool, 0.0) + weight
        ev = sig.evidence.setdefault(tool, ())
        if "methodology" not in ev:
            sig.evidence[tool] = tuple(ev) + ("methodology",)
    sig.boosts = boosts
    return sig


# ---------------------------------------------------------------------------
# 置信度 / 弃权（D2）。校准常数在语料上测量后钉死（见 retrieval 评测门）。
# ---------------------------------------------------------------------------
#: 置信度权重：绝对分数水平 / top1-top2 margin / 证据通道覆盖。
_CONF_LEVEL_W = 0.5
_CONF_MARGIN_W = 0.35
_CONF_CHANNEL_W = 0.15
#: 分数水平归一参考（词法分有界 ~[0,30]；8.0 ≈ 「多通道实质证据」水平）。
_CONF_LEVEL_REF = 8.0
#: 钉死弃权阈值：低于此置信度 → abstained（评测门验证误弃率上界）。
ABSTAIN_THRESHOLD = 0.35
#: 证据通道计数上限（level/margin 之外的第三分量归一）。
_MAX_CONF_CHANNELS = 3


def compute_confidence(
    ranked: Sequence[Tuple[str, float]],
    *,
    channels_for_top: int,
) -> Tuple[float, bool, str]:
    """排序段 [(name, score)] → (confidence, abstained, reason)。

    - 空排序段 → (0.0, True, "no_candidates")；
    - margin 对 top1 归一（|s1-s2|/max(|s1|,ε)）；
    - confidence = Σ 权重分量，各分量 clamp [0,1]；
    - confidence < ABSTAIN_THRESHOLD → abstained（低置信度诚实弃权）。
    """
    if not ranked:
        return 0.0, True, "no_candidates"
    s1 = ranked[0][1]
    # 单候选时 margin=0（唯一低分命中正是应弃权形态 —— 审查 R1 M-minor-2：
    # 缺省 s2=0 会把 margin 膨胀到 1.0 使 lone 弱候选永不弃权）
    s2 = ranked[1][1] if len(ranked) > 1 else s1
    level = max(0.0, min(1.0, s1 / _CONF_LEVEL_REF))
    margin = max(0.0, min(1.0, abs(s1 - s2) / max(abs(s1), 1e-6)))
    coverage = max(0.0, min(1.0, channels_for_top / _MAX_CONF_CHANNELS))
    conf = (
        _CONF_LEVEL_W * level
        + _CONF_MARGIN_W * margin
        + _CONF_CHANNEL_W * coverage
    )
    conf = max(0.0, min(1.0, conf))
    if conf < ABSTAIN_THRESHOLD:
        return (
            round(conf, 4), True,
            f"low_confidence(level={level:.2f},margin={margin:.2f},"
            f"channels={channels_for_top})",
        )
    return round(conf, 4), False, ""


# ---------------------------------------------------------------------------
# 可选第 5 路：embedding 检索器（默认 TOOL_RETRIEVAL_SEMANTIC 实现）。
# 进程级缓存：索引按全量 descriptor 指纹失效；模型加载失败记忆化（进程
# 生命周期内不重复尝试 —— 防 O(n) 次重试拖垮调用线程）。
# ---------------------------------------------------------------------------
_EMBEDDING_SPEC = f"{__name__}:embedding_retriever"

#: capability_tool_map 进程内 memo（键 = registry 身份 + 算法数；算法注册
#: 仅发生在启动/测试注册期，数量变化即失效 —— 不引入 TTL 假失效）。
_cap_map_memo: Dict[Tuple[int, int], Dict[str, List[str]]] = {}


def capability_tool_map_cached(algo_reg: Any) -> Dict[str, List[str]]:
    n_ids = getattr(algo_reg, "all_ids", 0)
    n_ids = len(n_ids()) if callable(n_ids) else len(n_ids or ())
    key = (id(algo_reg), n_ids)
    cached = _cap_map_memo.get(key)
    if cached is None:
        cached = algo_reg.capability_tool_map()
        _cap_map_memo.clear()  # 单 registry 语义：换实例即弃旧
        _cap_map_memo[key] = cached
    return cached


def default_semantic_spec() -> str:
    """默认语义检索注入 spec（tool_surface_v3 消费）。"""
    if os.getenv("TOOL_RETRIEVAL_EMBEDDING", "1").strip().lower() in (
        "0", "false", "no", "off",
    ):
        return ""
    return _EMBEDDING_SPEC


_embed_state: Dict[str, Any] = {"index": None, "fingerprint": None,
                                "model_failed": False}


def _descriptor_corpus_text(desc: Any) -> str:
    parts = [
        desc.name.replace("_", " "), desc.name,
        " ".join(desc.tags), " ".join(desc.domains),
        str(desc.summary or ""), str(desc.description or ""),
        " ".join(desc.examples or []),
    ]
    return " ".join(p for p in parts if p)[:2048]


def embedding_retriever(
    registry: Any, query: str, top_k: int = 30,
) -> Sequence[Any]:
    """``(registry, query, top_k) -> [RetrievalHit]`` 语义检索实现。

    - 模型/索引不可得 → 抛 RuntimeError（tool_surface_v3 契约：调用方
      降级词法并留痕 —— 本函数不做静默半结果）；
    - 索引按全量 descriptor 指纹缓存（描述/标签编辑自动重建）；
    - 得分归一到词法量级（×_SEMANTIC_SCALE）后返回 RetrievalHit。
    """
    from app.services.chat.tool_retrieval import RetrievalHit

    if not (query or "").strip() or top_k <= 0:
        return []
    if _embed_state["model_failed"]:
        raise RuntimeError("embedding model unavailable (memoized failure)")
    try:
        import numpy as np

        from app.tools.descriptor import manifest_fingerprint
        from app.services.rag.faiss_store import FaissVectorStore

        # registry → [(name, descriptor_fingerprint)] 全量指纹
        # （ToolRegistry 非 iterable —— 与 ToolRetrievalIndex._index_key 同款）
        try:
            fps = registry.fingerprints()
            fingerprint = manifest_fingerprint(
                [(n, fp[1]) for n, fp in fps.items()])
        except Exception:  # noqa: BLE001 — 指纹面故障按稳定哨兵退化（审查
            # R2 m-1：时间戳哨兵会让索引每查询全量重编码）
            fingerprint = "degraded"
        model = _embed_state.get("model")
        if model is None:
            store = FaissVectorStore()
            model = store._get_embedding_model()  # noqa: SLF001 — 复用其
            # 懒加载/offline 有界失败配置；模型本体进程级缓存（审查 R2 M-1：
            # 实例级缓存会每查询重载 SentenceTransformer，成为 turn 延迟
            # 主导项）
            _embed_state["model"] = model
    except Exception:  # noqa: BLE001 — 模型加载失败记忆化（指纹失败不毒化）
        _embed_state["model_failed"] = True
        raise RuntimeError("embedding model load failed") from None

    if _embed_state["fingerprint"] != fingerprint or _embed_state["index"] is None:
        names = sorted(registry.list_tools())
        texts = []
        kept: List[str] = []
        for name in names:
            try:
                texts.append(_descriptor_corpus_text(registry.descriptor(name)))
                kept.append(name)
            except KeyError:
                continue
        if not kept:
            raise RuntimeError("empty descriptor corpus")
        vectors = model.encode(texts, normalize_embeddings=True)
        _embed_state["index"] = (np.asarray(vectors, dtype="float32"), kept)
        _embed_state["fingerprint"] = fingerprint

    vectors, kept = _embed_state["index"]
    q_vec = model.encode([query.strip()], normalize_embeddings=True)
    q_vec = np.asarray(q_vec, dtype="float32")[0]
    sims = vectors @ q_vec
    order = sorted(range(len(kept)), key=lambda i: (-float(sims[i]), kept[i]))
    hits: List[RetrievalHit] = []
    for i in order[:max(1, int(top_k))]:
        score = max(0.0, float(sims[i])) * 10.0  # 归一到词法量级
        if score <= 0.0:
            continue
        hits.append(RetrievalHit(name=kept[i], score=score,
                                 matched=("embedding",)))
    return hits


__all__ = [
    "v6_retrieval_enabled",
    "expand_query_terms",
    "negation_anti_terms",
    "hybrid_signals",
    "HybridSignals",
    "methodology_signal_tools",
    "compute_confidence",
    "ABSTAIN_THRESHOLD",
    "embedding_retriever",
    "default_semantic_spec",
    "_EXPANSION_FUSION",
]
