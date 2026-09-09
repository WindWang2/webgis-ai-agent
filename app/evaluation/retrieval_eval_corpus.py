"""Retrieval V5 open-loop query→tool 评测语料（ADR-0118 决策 D7）。

V4 基线的缺口（Phase-0 审计 §7）：``retrieval_corpus`` 由 306 意图
golden + paraphrase 层生成，期望工具经 AlgorithmRegistry **capability
反查** —— 量的是 capability→surface projection，不是「用户口语 query
→ 正确工具」的开环检索精度；且无 curated hard-negative / 近重复对 /
歧义案例。

V5 语料（全部**人工金标**，与 lexical 索引 / capability 反查不同源，
不存在答案写死对齐索引词表的通道；查询为口语化措辞，非描述符文案；
共 329 条 = direct 100 / near_duplicate 28 / hard_negative 32 /
ambiguous 28 / follow_up 30 / contextual 30 /
workflow_continuation 30 / failure_recovery 24 / map_edit 27）：

- ``direct``：明确措辞 → 唯一期望工具；
- ``near_duplicate``：最小限定词翻转的兄弟对（期望 = 正确侧，
  ``must_not_select`` = 兄弟侧 —— 限定词必须改变选择）；
- ``hard_negative``：词面陷阱（距离邻近 vs 路网可达、点计数 vs 栅格
  分区统计、批量 vs 单条……期望 = 正确工具，``must_not_select`` =
  陷阱工具）；
- ``ambiguous``：措辞天然多解（``valid_tools`` ≥2，precision@1 按
  「top-1 ∈ 合法集」计 —— 不把歧义当错误，也不当满分对齐）；
- ``follow_up``：追问改参（沿用上一步、只改一处再跑一遍）；
- ``contextual``：上下文省略与指代短句（所指来自会话，不在句内）；
- ``workflow_continuation``：拿上一步产物直接做下一步；
- ``failure_recovery``：上一步失败、换一条路重试；
- ``map_edit``：成图阶段微调（样式 / 版面 / 组件 / 视角）。

指标（``retrieval_eval_report``）：precision@1（top-1 ∈ valid）、
recall@5/10（|top∩valid|/|valid|）、invalid_selection_rate（top-5 撞
must_not 的案例占比）、fallback_rate（空选择占比）、tier3_leak（恒期望
0）。全程确定性：真实 DynamicToolSurface.select，无 LLM、无时间戳。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

MIN_EVAL_CORPUS_SIZE = 300


@dataclass(frozen=True)
class RetrievalEvalCase:
    """一条人工金标的 query→tool 检索案例。"""

    case_id: str
    query: str
    kind: str                       # direct | near_duplicate | hard_negative | ambiguous
    expected_tools: Tuple[str, ...]
    valid_tools: Tuple[str, ...] = field(default=())
    must_not_select: Tuple[str, ...] = ()
    lang: str = "zh"

    def __post_init__(self) -> None:
        if not self.valid_tools:
            object.__setattr__(self, "valid_tools", self.expected_tools)

    def as_metrics_case(self) -> Any:
        return self


def _c(case_id: str, query: str, expected: Sequence[str], *, kind: str = "direct",
       valid: Sequence[str] = (), must_not: Sequence[str] = (),
       lang: str = "zh") -> RetrievalEvalCase:
    return RetrievalEvalCase(
        case_id=case_id, query=query, kind=kind,
        expected_tools=tuple(expected),
        valid_tools=tuple(valid) if valid else tuple(expected),
        must_not_select=tuple(must_not), lang=lang,
    )


def build_retrieval_eval_corpus() -> List[RetrievalEvalCase]:
    """确定性构建（列表顺序即稳定顺序；无随机、无时间戳）。"""
    cases: List[RetrievalEvalCase] = []
    add = cases.append

    # ── direct（30）──────────────────────────────────────────────
    add(_c("EV-D01", "给学校图层加500米缓冲区看看覆盖范围",
           ["buffer_analysis"], valid=["buffer_analysis", "multi_ring_buffer"]))
    add(_c("EV-D02", "查一下王府井周边1公里内的咖啡馆",
           ["query_local_poi"],
           valid=["query_local_poi", "query_local_osm", "search_poi_around"]))
    add(_c("EV-D03", "用三角网插值把监测点铺成连续表面", ["tin_interpolation"]))
    add(_c("EV-D04", "对这些站点做反距离加权插值", ["idw_interpolation"]))
    add(_c("EV-D05", "用克里金把土壤重金属铺成网格并给估计方差",
           ["kriging_interpolation", "block_kriging_surface",
            "variogram_model_selection"]))
    add(_c("EV-D06", "引入辅助协变量的协同克里金", ["cokriging_surface"]))
    add(_c("EV-D07", "共享单车订单有经纬度和时间，按时空密度一起分簇", ["st_dbscan"]))
    add(_c("EV-D08", "对比洪灾前后两期 NDVI 栅格找变化并给统计", ["detect_raster_change"]))
    add(_c("EV-D09", "分析哪些片区热点在增强哪些在减弱", ["emerging_hotspot_analysis"]))
    add(_c("EV-D10", "这段销售额序列哪个月发生了均值跳变", ["temporal_changepoint"]))
    add(_c("EV-D11", "看这几年绿地率的升降趋势并做显著性检验", ["temporal_trend"]))
    add(_c("EV-D12", "从 DEM 提取河网并做 Strahler 分级", ["stream_network"]))
    add(_c("EV-D13", "找出这个流域出口断面以上的全部汇水区", ["watershed_delineation"]))
    add(_c("EV-D14", "老城区的天空开阔度（SVF）分布", ["sky_view_factor_analysis"]))
    add(_c("EV-D15", "地铁站15分钟步行等时圈", ["service_area_simple"]))
    add(_c("EV-D16", "计算仓库到各门店的配送距离时间矩阵", ["distance_matrix_cn"]))
    add(_c("EV-D17", "把高德坐标系的数据批量转成 WGS84", ["transform_coordinates"]))
    add(_c("EV-D18", "在研究区范围内生成1公里见方的网格", ["fishnet_grid"]))
    add(_c("EV-D19", "把街道边界按所属区县合并", ["dissolve_layer"]))
    add(_c("EV-D20", "给每个行政区算区域内的平均海拔（栅格分区统计）", ["zonal_stats"]))
    add(_c("EV-D21", "统计每个街道办辖区内的餐饮POI数量", ["spatial_aggregate"]))
    add(_c("EV-D22", "挑一景云量最少的 Sentinel-2 影像", ["fetch_sentinel"]))
    add(_c("EV-D23", "拉取这片山区的30米高程数据", ["fetch_dem"]))
    add(_c("EV-D24", "给这份月度序列做季节分解", ["temporal_seasonal_decompose"]))
    add(_c("EV-D25", "导出一张带图例和指北针的出版级地图", ["export_thematic_map"]))
    add(_c("EV-D26", "把这份大 TIF 转成 COG 便于在线瓦片服务", ["convert_raster_to_cog"]))
    add(_c("EV-D27", "对这份数据做几何/拓扑/CRS 质量审计", ["audit_spatial_quality"]))
    add(_c("EV-D28", "把乡镇口径的人口总量按居住区面积权重切分到 finer 面", ["dasymetric_reallocation"]))
    add(_c("EV-D29", "检验土壤类型对作物产量差异的解释力 q 值", ["geodetector"]))
    add(_c("EV-D30", "Generate an isochrone of 15-minute walk from each metro station",
           ["service_area_simple"], lang="en"))

    # ── near_duplicate（6 对 = 12）───────────────────────────────
    add(_c("EV-N01a", "插值必须精确穿过原始观测点的三角网方案",
           ["tin_interpolation"], kind="near_duplicate", must_not=["trend_surface"]))
    add(_c("EV-N01b", "只要区域整体起伏大势，忽略局部细节",
           ["trend_surface"], kind="near_duplicate", must_not=["tin_interpolation"]))
    add(_c("EV-N02a", "订单带下单时间戳，时空一起聚类",
           ["st_dbscan"], kind="near_duplicate", must_not=["spatial_cluster"]))
    add(_c("EV-N02b", "只要空间分布的密度聚类，没有时间信息",
           ["spatial_cluster"], kind="near_duplicate", must_not=["st_dbscan"]))
    add(_c("EV-N03a", "两期多光谱影像逐像元变化幅度和方向角",
           ["detect_change_cva"], kind="near_duplicate", must_not=["detect_raster_change"]))
    add(_c("EV-N03b", "对比两期 NDVI 栅格输出变化图和变化统计",
           ["detect_raster_change"], kind="near_duplicate", must_not=["detect_change_cva"]))
    add(_c("EV-N04a", "找时空尺度上新增强的热点片区",
           ["emerging_hotspot_analysis"], kind="near_duplicate", must_not=["general_g"]))
    add(_c("EV-N04b", "全市单期口径的高值聚集显著性检验",
           ["general_g"], kind="near_duplicate", must_not=["emerging_hotspot_analysis"]))
    add(_c("EV-N05a", "定位序列里唯一的均值漂移发生点",
           ["temporal_changepoint"], kind="near_duplicate", must_not=["temporal_trend"]))
    add(_c("EV-N05b", "长期变化的斜率方向和显著性",
           ["temporal_trend"], kind="near_duplicate", must_not=["temporal_changepoint"]))
    add(_c("EV-N06a", "只调整缩放级别、俯仰和旋转角度",
           ["set_map_view"], kind="near_duplicate", must_not=["fly_to_location"]))
    add(_c("EV-N06b", "把相机平滑飞行到指定坐标",
           ["fly_to_location"], kind="near_duplicate", must_not=["set_map_view"]))

    # ── hard_negative（12）───────────────────────────────────────
    add(_c("EV-H01", "搜索人民广场周边2公里内的健身房",
           ["search_poi_around"], kind="hard_negative", must_not=["buffer_analysis"]))
    add(_c("EV-H02", "给门店图层生成5公里直线服务范围圈",
           ["buffer_analysis"], kind="hard_negative", must_not=["service_area_simple"]))
    add(_c("EV-H03", "沿路网骑行15分钟能覆盖哪些区域",
           ["service_area_simple"], kind="hard_negative", must_not=["buffer_analysis"]))
    add(_c("EV-H04", "对污染栅格按行政区统计平均值",
           ["zonal_stats"], kind="hard_negative", must_not=["spatial_aggregate"]))
    add(_c("EV-H05", "数一数每个区里有多少个公交站点",
           ["spatial_aggregate"], kind="hard_negative", must_not=["zonal_stats"]))
    add(_c("EV-H06", "从每个格网单元沿水流到出口累计多少米",
           ["flow_length_analysis"], kind="hard_negative", must_not=["dinf_flow_analysis"]))
    add(_c("EV-H07", "这批1000条地址批量转坐标",
           ["batch_geocode_cn"], kind="hard_negative", must_not=["geocode_cn"]))
    add(_c("EV-H08", "Geocode this single address",
           ["geocode_cn"], kind="hard_negative", must_not=["batch_geocode_cn"], lang="en"))
    add(_c("EV-H09", "上传后先摸底 bbox、数值分布和时间字段",
           ["webgis_source_profile"], kind="hard_negative", must_not=["describe_dataset"]))
    add(_c("EV-H10", "读数据集的 schema、几何类型和 SRS 元数据契约",
           ["describe_dataset"], kind="hard_negative", must_not=["webgis_source_profile"]))
    add(_c("EV-H11", "克里金建模前先在同经验变异函数上比选理论模型",
           ["variogram_model_selection"], kind="hard_negative",
           must_not=["block_kriging_surface"]))
    add(_c("EV-H12", "对影像做亮度阈值云掩膜咨询",
           ["cloud_qc_basic"], kind="hard_negative", must_not=["fetch_sentinel"]))

    # ── ambiguous（12；valid ≥2）────────────────────────────────
    add(_c("EV-A01", "把地图移到西湖看看",
           ["fly_to_location"], valid=["fly_to_location", "zoom_to_bbox"], kind="ambiguous"))
    add(_c("EV-A02", "分析这份数据的空间分布格局",
           ["spatial_cluster"],
           valid=["spatial_cluster", "standard_deviational_ellipse",
                  "central_feature", "geary_c"], kind="ambiguous"))
    add(_c("EV-A03", "这两地之间开车要多久多远",
           ["distance_matrix_cn"],
           valid=["distance_matrix_cn", "measure_distance"], kind="ambiguous"))
    add(_c("EV-A04", "给图层换个配色",
           ["update_layer_appearance"],
           valid=["update_layer_appearance", "apply_layer_style"], kind="ambiguous"))
    add(_c("EV-A05", "做因子对分布的解释力分析",
           ["geodetector"],
           valid=["geodetector", "geodetector_ecological", "geodetector_risk"],
           kind="ambiguous"))
    add(_c("EV-A06", "把观测点插值成连续面",
           ["idw_interpolation"],
           valid=["idw_interpolation", "tin_interpolation", "block_kriging_surface",
                  "trend_surface"], kind="ambiguous"))
    add(_c("EV-A07", "看看显著的高值区域都在哪",
           ["general_g"],
           valid=["general_g", "emerging_hotspot_analysis", "spatial_cluster"],
           kind="ambiguous"))
    add(_c("EV-A08", "把结果导出成图",
           ["export_thematic_map"],
           valid=["export_thematic_map", "generate_chart"], kind="ambiguous"))
    add(_c("EV-A09", "这份数据质量怎么样",
           ["audit_spatial_quality"],
           valid=["audit_spatial_quality", "webgis_source_profile", "describe_dataset"],
           kind="ambiguous"))
    add(_c("EV-A10", "两期数据对比一下有什么变化",
           ["detect_raster_change"],
           valid=["detect_raster_change", "detect_change_cva", "detect_ratio_change"],
           kind="ambiguous"))
    add(_c("EV-A11", "Zoom in a bit",
           ["set_map_view"], valid=["set_map_view", "zoom_to_bbox"],
           kind="ambiguous", lang="en"))
    add(_c("EV-A12", "这些点在空间上聚集吗",
           ["geary_c"],
           valid=["geary_c", "general_g", "spatial_cluster"], kind="ambiguous"))


    # ── direct 续篇（EV-D31..D100）：70 ──
    add(_c("EV-D31", "点图层和面图层叠加求交",
           ['overlay_analysis'], kind="direct"))
    add(_c("EV-D32", "把POI点落在哪个街道 zoning 写回属性表",
           ['spatial_join'], kind="direct"))
    add(_c("EV-D33", "按最近原则给每个快递站点划分泰森势力范围",
           ['voronoi_polygons'], kind="direct"))
    add(_c("EV-D34", "框出这批充电桩的最小凸包看看大致服务范围",
           ['convex_hull'], kind="direct"))
    add(_c("EV-D35", "拿行政区当遮罩裁剪图层，只保留范围内要素",
           ['clip_layer'], kind="direct"))
    add(_c("EV-D36", "用两个栅格逐像元相减做DEM差值",
           ['raster_calculator'], kind="direct"))
    add(_c("EV-D37", "把连续NDVI按阈值分成低中高三档植被覆盖",
           ['raster_reclassify'], kind="direct"))
    add(_c("EV-D38", "把30米DEM重采样到90米做个概览",
           ['raster_resample'], kind="direct"))
    add(_c("EV-D39", "从DEM里提取等高线",
           ['extract_contours'], kind="direct"))
    add(_c("EV-D40", "在线框个范围算NDVI给个均值和覆盖率",
           ['compute_ndvi'], kind="direct"))
    add(_c("EV-D41", "算改良归一化水体指数MNDWI找水体",
           ['compute_spectral_index'], kind="direct"))
    add(_c("EV-D42", "做亮度绿度湿度三轴的Tasseled Cap变换",
           ['tasseled_cap'], kind="direct"))
    add(_c("EV-D43", "用k-means把这片影像无监督分成几块地物",
           ['segment_image'], kind="direct"))
    add(_c("EV-D44", "单景SAR做Lee滤波去斑点噪声",
           ['sar_speckle_filter'], kind="direct"))
    add(_c("EV-D45", "把SAR的DN定标成sigma0后向散射",
           ['sar_calibrate'], kind="direct"))
    add(_c("EV-D46", "没目标光谱，把整景里离群的光谱像元筛出来",
           ['rx_anomaly'], kind="direct"))
    add(_c("EV-D47", "拿已知矿物端元光谱在场景里做丰度式检测",
           ['matched_filter'], kind="direct"))
    add(_c("EV-D48", "已知端元光谱，反演每个像元的矿物丰度占比",
           ['linear_unmixing'], kind="direct"))
    add(_c("EV-D49", "逐像元算光谱角，和植被参考光谱做匹配分类",
           ['spectral_angle_mapper'], kind="direct"))
    add(_c("EV-D50", "看各波段之间Pearson相关，诊断冗余共线",
           ['band_correlation_table'], kind="direct"))
    add(_c("EV-D51", "用Moran's I做全局空间自相关检验",
           ['moran_i'], kind="direct", lang="mixed"))
    add(_c("EV-D52", "用Gi星做热点冷点显著性检验",
           ['hotspot_analysis'], kind="direct", lang="mixed"))
    add(_c("EV-D53", "做LISA找高高聚集和低低聚集的显著区",
           ['h3_lisa'], kind="direct", valid=['h3_lisa', 'local_geary']))
    add(_c("EV-D54", "这组餐厅POI是扎堆还是随机，用R比率判断",
           ['nearest_neighbor'], kind="direct"))
    add(_c("EV-D55", "K函数随半径变化，看聚集还是均匀",
           ['ripley_k_analysis'], kind="direct"))
    add(_c("EV-D56", "样方卡方检验加方差均值比，判断偏离随机吗",
           ['quadrat_analysis'], kind="direct"))
    add(_c("EV-D57", "跑个GWR看回归系数在空间上怎么变",
           ['gwr_regression'], kind="direct"))
    add(_c("EV-D58", "OLS回归顺手做残差Moran和LM诊断",
           ['ols_regression'], kind="direct"))
    add(_c("EV-D59", "小行政区人口少发病率抖，做个贝叶斯率平滑",
           ['rate_smoothing'], kind="direct"))
    add(_c("EV-D60", "Plot an OD flow line layer from this OD matrix, top 50 flows",
           ['od_flow_edges'], kind="direct", lang="en"))
    add(_c("EV-D61", "水文分析前先把DEM里的洼地填掉",
           ['depression_fill'], kind="direct"))
    add(_c("EV-D62", "D8流向汇流，看看上游贡献像元数",
           ['flow_analysis'], kind="direct"))
    add(_c("EV-D63", "算TWI湿润指数找潜在饱和带",
           ['topographic_index'], kind="direct"))
    add(_c("EV-D64", "要USLE方程的坡长坡度LS因子栅格",
           ['ls_factor_analysis'], kind="direct"))
    add(_c("EV-D65", "双尺度TPI地貌分10类做地貌单元制图",
           ['landform_classify'], kind="direct"))
    add(_c("EV-D66", "8方位视线三元码做geomorphons形态分类",
           ['geomorphon_analysis'], kind="direct"))
    add(_c("EV-D67", "TPI TRI曲率一次算齐评估工程意义",
           ['terrain_derivatives'], kind="direct"))
    add(_c("EV-D68", "瞭望塔选址，判断哪些像元通视可见",
           ['viewshed_analysis'], kind="direct"))
    add(_c("EV-D69", "算HAND和Shreve量级做洪水易损性图层",
           ['hydrology_v4_analysis'], kind="direct"))
    add(_c("EV-D70", "pour point往上圈流域，顺手给面积周长elongation",
           ['watershed_morphometry_analysis'], kind="direct"))
    add(_c("EV-D71", "两个点之间排条驾车路线，给距离时间和路线坐标",
           ['plan_route'], kind="direct"))
    add(_c("EV-D72", "查下从公司到机场的公交地铁换乘和票价",
           ['search_transit_route'], kind="direct"))
    add(_c("EV-D73", "现在三环堵不堵，看看实时路况",
           ['get_traffic_status'], kind="direct"))
    add(_c("EV-D74", "沿真实路网算一条步行最短路径",
           ['network_shortest_path'], kind="direct"))
    add(_c("EV-D75", "多起点多终点网络成本矩阵，距离通行时间一次算完",
           ['network_od_matrix'], kind="direct"))
    add(_c("EV-D76", "一次出5/10/15分钟三档真实路网可达圈",
           ['network_service_area'], kind="direct"))
    add(_c("EV-D77", "按真实路网通行时间找最近医院",
           ['network_closest_facility'], kind="direct"))
    add(_c("EV-D78", "用Huff概率模型算各商场市场份额和份额熵",
           ['network_huff_interaction'], kind="direct"))
    add(_c("EV-D79", "E2SFCA两步浮动捕获法算15分钟生活圈覆盖率",
           ['network_accessibility'], kind="direct"))
    add(_c("EV-D80", "Find the shortest driving path along the real road network",
           ['network_shortest_path'], kind="direct", lang="en"))
    add(_c("EV-D81", "上传前先看这份数据的行数字段缺失率和坐标问题",
           ['profile_dataset'], kind="direct"))
    add(_c("EV-D82", "Perform safe remediation on this spatial dataset, keep source intact",
           ['repair_spatial_dataset'], kind="direct", lang="en"))
    add(_c("EV-D83", "地方坐标系CGCS2000转WGS84叠加底图",
           ['reproject_coordinates'], kind="direct"))
    add(_c("EV-D84", "看看权重矩阵有没有孤岛和多分量，做个结构体检",
           ['weights_diagnostics'], kind="direct"))
    add(_c("EV-D85", "权重行标准化邻居分布连通性体检一遍",
           ['weights_diagnostics'], kind="direct"))
    add(_c("EV-D86", "按时间粒度把订单按月重采样汇总",
           ['temporal_aggregate'], kind="direct"))
    add(_c("EV-D87", "只留最近7天的数据做筛选",
           ['temporal_filter'], kind="direct"))
    add(_c("EV-D88", "先看这份数据的时间字段跨度分辨率和缺失",
           ['temporal_profile'], kind="direct"))
    add(_c("EV-D89", "对比T1和T2两期快照的要素数和属性变化",
           ['temporal_change'], kind="direct"))
    add(_c("EV-D90", "List all vector datasets available in this session",
           ['list_datasets'], kind="direct", lang="en"))
    add(_c("EV-D91", "我之前生成过哪些NDVI资产，列出来回顾下",
           ['list_analysis_assets'], kind="direct"))
    add(_c("EV-D92", "按关键词和角色搜一下手头的数据资产",
           ['search_datasets'], kind="direct"))
    add(_c("EV-D93", "查下这个产物是谁生成的、被谁用过，走查血缘",
           ['get_lineage'], kind="direct"))
    add(_c("EV-D94", "Save this successful session plan as a persistent reusable workflow",
           ['save_plan_as_workflow'], kind="direct", lang="en"))
    add(_c("EV-D95", "阶段性成果固化一下，存个工作空间快照",
           ['save_workspace_snapshot'], kind="direct"))
    add(_c("EV-D96", "把对话记录和工具调用结果整理成专业分析报告",
           ['generate_analysis_report'], kind="direct"))
    add(_c("EV-D97", "把总览北部南部三张图按同样排版批量导出",
           ['export_batch_maps'], kind="direct"))
    add(_c("EV-D98", "按人口字段做分层设色专题图",
           ['create_thematic_map'], kind="direct"))
    add(_c("EV-D99", "以高度表达各区GDP总量，做个3D挤出立体图",
           ['create_3d_extrusion_map'], kind="direct"))
    add(_c("EV-D100", "按人口字段做choropleth分层设色专题图",
           ['create_thematic_map'], kind="direct", lang="mixed"))

    # ── near_duplicate 续篇（EV-N07a..N14b，8 对）：16 ──
    add(_c("EV-N07a", "隐藏部分改显示，不产生新图层",
           ['apply_layer_filter'], kind="near_duplicate", must_not=['attribute_filter']))
    add(_c("EV-N07b", "筛出一批要素存成新图层继续做分析",
           ['attribute_filter'], kind="near_duplicate", must_not=['apply_layer_filter']))
    add(_c("EV-N08a", "按房价字段分层设色做Choropleth，级别自动划分",
           ['create_thematic_map'], kind="near_duplicate", must_not=['apply_layer_style']))
    add(_c("EV-N08b", "整个图层一次性盖个单色定型区分主辅",
           ['apply_layer_style'], kind="near_duplicate", must_not=['create_thematic_map']))
    add(_c("EV-N09a", "在地图上量两个点之间的直线距离",
           ['measure_distance'], kind="near_duplicate", must_not=['distance_matrix_cn']))
    add(_c("EV-N09b", "多个仓库到多个门店的驾驶距离时间矩阵",
           ['distance_matrix_cn'], kind="near_duplicate", must_not=['measure_distance']))
    add(_c("EV-N10a", "把Paris这个英文地名转成经纬度",
           ['geocode'], kind="near_duplicate", must_not=['geocode_cn']))
    add(_c("EV-N10b", "中文地址转坐标，要准确率最高那个天地图方案",
           ['geocode_cn'], kind="near_duplicate", must_not=['geocode']))
    add(_c("EV-N11a", "查个区里全部学校的POI全量数据",
           ['query_osm_poi'], kind="near_duplicate", must_not=['search_poi_around']))
    add(_c("EV-N11b", "搜一下我坐标周围半径两公里的咖啡馆",
           ['search_poi_around'], kind="near_duplicate", must_not=['query_osm_poi']))
    add(_c("EV-N12a", "红外减红光除以两者之和算比值指数",
           ['raster_calculator'], kind="near_duplicate", must_not=['raster_reclassify']))
    add(_c("EV-N12b", "坡度按阈值映射成三类风险等级",
           ['raster_reclassify'], kind="near_duplicate", must_not=['raster_calculator']))
    add(_c("EV-N13a", "ESRI幂编码算流向和上游贡献像元数",
           ['flow_analysis'], kind="near_duplicate", must_not=['dinf_flow_analysis']))
    add(_c("EV-N13b", "弧度角输出，面内角度比例分流",
           ['dinf_flow_analysis'], kind="near_duplicate", must_not=['flow_analysis']))
    add(_c("EV-N14a", "快速看一眼人群分布热度，原生渲染就行",
           ['heatmap_data'], kind="near_duplicate", must_not=['kde_surface']))
    add(_c("EV-N14b", "要铺满全域的连续概率格网做叠加输入",
           ['kde_surface'], kind="near_duplicate", must_not=['heatmap_data']))

    # ── hard_negative 续篇（EV-H13..H32）：20 ──
    add(_c("EV-H13", "每个区县的平均NDVI，逐像素统计回写属性",
           ['zonal_stats'], kind="hard_negative", must_not=['spatial_aggregate']))
    add(_c("EV-H14", "数一数每个六边形网格里落了几个事故点",
           ['h3_binning'], kind="hard_negative", must_not=['zonal_stats']))
    add(_c("EV-H15", "500/1000/1500米三档同心环一次生成，带ring标识",
           ['multi_ring_buffer'], kind="hard_negative", must_not=['buffer_analysis']))
    add(_c("EV-H16", "给学校画个500米缓冲多边形",
           ['buffer_analysis'], kind="hard_negative", must_not=['multi_ring_buffer']))
    add(_c("EV-H17", "WGS84和GCJ02之间批量互转，叠加高德底图",
           ['transform_coordinates'], kind="hard_negative", must_not=['reproject_coordinates']))
    add(_c("EV-H18", "4490地方系转换到4326，EPSG对UTM投影",
           ['reproject_coordinates'], kind="hard_negative", must_not=['transform_coordinates']))
    add(_c("EV-H19", "看下这份数据的元数据契约，顺手验个指纹",
           ['describe_dataset'], kind="hard_negative", must_not=['profile_dataset']))
    add(_c("EV-H20", "摸底这份数据的数值分布缺失率和时间字段",
           ['profile_dataset'], kind="hard_negative", must_not=['describe_dataset']))
    add(_c("EV-H21", "起点终点之间倒公交地铁，看换乘次数和票价",
           ['search_transit_route'], kind="hard_negative", must_not=['measure_distance']))
    add(_c("EV-H22", "判断每栋楼是否在保护区内，把归属写回属性",
           ['spatial_join'], kind="hard_negative", must_not=['clip_layer']))
    add(_c("EV-H23", "拿红线面做遮罩裁剪，范围外的都不要",
           ['clip_layer'], kind="hard_negative", must_not=['spatial_join']))
    add(_c("EV-H24", "全局Moran's I算一下",
           ['moran_i'], kind="hard_negative", must_not=['geary_c']))
    add(_c("EV-H25", "Gi星热点分析，置换法给显著性",
           ['hotspot_analysis'], kind="hard_negative", must_not=['general_g']))
    add(_c("EV-H26", "OLS全局回归，系数t检验VIF和残差诊断都要",
           ['ols_regression'], kind="hard_negative", must_not=['gwr_regression']))
    add(_c("EV-H27", "误差项空间自相关显著，用SEM估计λ系数并做LR检验",
           ['sem_ml_regression'], kind="hard_negative", must_not=['sar_ml_regression']))
    add(_c("EV-H28", "本地tif已上传，算NDVI并存成分析资产",
           ['analyze_vegetation_index'], kind="hard_negative", must_not=['compute_ndvi']))
    add(_c("EV-H29", "用Knox看时空双阈值里的事件对多不多",
           ['knox_analysis'], kind="hard_negative", must_not=['mantel_test_analysis']))
    add(_c("EV-H30", "协方差看方差做主成分合成",
           ['raster_pca'], kind="hard_negative", must_not=['mnf_transform']))
    add(_c("EV-H31", "MNF按SNR排序，噪声白化后去噪",
           ['mnf_transform'], kind="hard_negative", must_not=['raster_pca']))
    add(_c("EV-H32", "已知光谱做丰度式白化投影检测",
           ['matched_filter'], kind="hard_negative", must_not=['rx_anomaly']))

    # ── ambiguous 续篇（EV-A13..A28）：16 ──
    add(_c("EV-A13", "做个热力图看看人群聚集",
           ['heatmap_data'], kind="ambiguous", valid=['heatmap_data', 'kde_contours']))
    add(_c("EV-A14", "看看这片植被长势怎么样",
           ['compute_ndvi'], kind="ambiguous", valid=['compute_ndvi', 'analyze_vegetation_index', 'compute_vegetation_index']))
    add(_c("EV-A15", "房价和地铁距离共位相关吗",
           ['bivariate_moran'], kind="ambiguous", valid=['bivariate_moran', 'bivariate_local_moran']))
    add(_c("EV-A16", "新店选址，看商圈市场份额覆盖率生活圈引力都看看",
           ['network_huff_interaction'], kind="ambiguous", valid=['network_accessibility', 'network_huff_interaction']))
    add(_c("EV-A17", "离我最近的学校在哪",
           ['search_poi_around'], kind="ambiguous", valid=['search_poi_around', 'nearest_facility', 'network_closest_facility']))
    add(_c("EV-A18", "这两地之间做路径规划，驾车返回距离时间和路线坐标",
           ['plan_route'], kind="ambiguous", valid=['plan_route', 'distance_matrix_cn', 'search_transit_route']))
    add(_c("EV-A19", "按条件筛一下图层要素，隐藏不看的",
           ['apply_layer_filter'], kind="ambiguous", valid=['apply_layer_filter', 'apply_layer_style']))
    add(_c("EV-A20", "这份数据集做聚合统计，count求和平均都要",
           ['aggregate_dataset'], kind="ambiguous", valid=['aggregate_dataset', 'spatial_stats', 'spatial_aggregate']))
    add(_c("EV-A21", "坐标系转一下，GCJ02和高德之间互转",
           ['transform_coordinates'], kind="ambiguous", valid=['transform_coordinates', 'reproject_coordinates']))
    add(_c("EV-A22", "这个文件先摸个底，剖析行数字段类型缺失率",
           ['profile_dataset'], kind="ambiguous", valid=['profile_dataset', 'get_upload_info', 'list_uploaded_data']))
    add(_c("EV-A23", "冷热点都标出来看看",
           ['hotspot_analysis'], kind="ambiguous", valid=['hotspot_analysis', 'h3_lisa', 'emerging_hotspot_analysis']))
    add(_c("EV-A24", "用Moran测一下聚集性",
           ['moran_i'], kind="ambiguous", valid=['moran_i', 'general_g']))
    add(_c("EV-A25", "Show me the terrain and DEM here",
           ['compute_terrain'], kind="ambiguous", valid=['compute_terrain', 'terrain_derivatives'], lang="en"))
    add(_c("EV-A26", "附近餐厅按坐标半径搜一下",
           ['search_poi_around'], kind="ambiguous", valid=['search_poi_around', 'query_osm_poi']))
    add(_c("EV-A27", "这几年销量走势咋样，季节周期项也分解出来",
           ['temporal_seasonal_decompose'], kind="ambiguous", valid=['temporal_seasonal_decompose', 'temporal_trend', 'generate_chart']))
    add(_c("EV-A28", "出一份报告",
           ['generate_analysis_report'], kind="ambiguous", valid=['generate_analysis_report', 'generate_monitoring_report', 'export_thematic_map']))

    # ── follow_up（EV-F01..F30：追问改参重跑）：30 ──
    add(_c("EV-F01", "缓冲距离改成800米再跑一次",
           ['buffer_analysis'], kind="follow_up", valid=['buffer_analysis', 'multi_ring_buffer']))
    add(_c("EV-F02", "刚才那版插值太糙，换克里金重铺，把估计方差也带上",
           ['kriging_interpolation'], kind="follow_up", valid=['kriging_interpolation', 'block_kriging_surface']))
    add(_c("EV-F03", "那人口密度也按这个套路来一张热力图",
           ['heatmap_data'], kind="follow_up", valid=['heatmap_data']))
    add(_c("EV-F04", "NDVI也按区县做一遍栅格分区统计",
           ['zonal_stats'], kind="follow_up"))
    add(_c("EV-F05", "等时圈改成骑行15分钟再算一遍",
           ['service_area_simple'], kind="follow_up", valid=['service_area_simple', 'isochrone_analysis']))
    add(_c("EV-F06", "把飞行目的地换成外滩，再飞一次",
           ['fly_to_location'], kind="follow_up"))
    add(_c("EV-F07", "再放大一点，我要看清这条街",
           ['set_map_view'], kind="follow_up", valid=['set_map_view']))
    add(_c("EV-F08", "那把坡度大于25度的重分类成危险等级筛出来",
           ['raster_reclassify'], kind="follow_up", valid=['raster_reclassify']))
    add(_c("EV-F09", "换反距离加权重新铺面，附带交叉验证",
           ['idw_interpolation'], kind="follow_up"))
    add(_c("EV-F10", "趋势分解里把季节项也画出来",
           ['temporal_seasonal_decompose'], kind="follow_up", valid=['temporal_seasonal_decompose']))
    add(_c("EV-F11", "刚才的热点改用LISA再验一遍",
           ['h3_lisa'], kind="follow_up", valid=['h3_lisa', 'hotspot_analysis', 'local_geary']))
    add(_c("EV-F12", "那降水栅格也做一遍分区统计",
           ['zonal_stats'], kind="follow_up"))
    add(_c("EV-F13", "坐标不变，搜周围两公里半径的POI",
           ['search_poi_around'], kind="follow_up"))
    add(_c("EV-F14", "坐标系也转成WGS84，和底图对齐",
           ['transform_coordinates'], kind="follow_up", valid=['transform_coordinates', 'reproject_coordinates']))
    add(_c("EV-F15", "Change the buffer to 800 meters and run it again",
           ['buffer_analysis'], kind="follow_up", valid=['buffer_analysis'], lang="en"))
    add(_c("EV-F16", "Try kriging instead for the interpolation, with variance",
           ['kriging_interpolation'], kind="follow_up", valid=['kriging_interpolation', 'block_kriging_surface'], lang="en"))
    add(_c("EV-F17", "刚才那份年鉴数据，再按乡镇挂一遍坐标",
           ['get_township_center'], kind="follow_up"))
    add(_c("EV-F18", "把这版专题图也导出成PDF存档",
           ['export_thematic_map'], kind="follow_up"))
    add(_c("EV-F19", "那把这份结果按指定字段做Choropleth分层设色专题图",
           ['create_thematic_map'], kind="follow_up"))
    add(_c("EV-F20", "换个变异函数模型重新比选，刚才那个欠拟合",
           ['variogram_model_selection'], kind="follow_up"))
    add(_c("EV-F21", "时间窗口改成近30天重新筛",
           ['temporal_filter'], kind="follow_up"))
    add(_c("EV-F22", "刚才隐藏的最终结果图层，显示到地图上",
           ['display_layer'], kind="follow_up", valid=['display_layer', 'set_layer_status']))
    add(_c("EV-F23", "标注点先清掉，图太乱了",
           ['clear_annotations'], kind="follow_up"))
    add(_c("EV-F24", "分组字段换成区县，把边界合并后再算一次",
           ['dissolve_layer'], kind="follow_up"))
    add(_c("EV-F25", "Also compute the MNDWI water body index from band values",
           ['compute_spectral_index'], kind="follow_up", valid=['compute_spectral_index', 'compute_ndvi'], lang="en"))
    add(_c("EV-F26", "Also do zonal stats of mean DEM elevation per district",
           ['zonal_stats'], kind="follow_up", lang="en"))
    add(_c("EV-F27", "这条OD也查条公交地铁路线，看换乘对比下",
           ['search_transit_route'], kind="follow_up", valid=['search_transit_route', 'plan_route']))
    add(_c("EV-F28", "刚才的回归加个空间滞后项重跑",
           ['slx_regression'], kind="follow_up", valid=['slx_regression']))
    add(_c("EV-F29", "热点显著性改用permutation置换法再验一遍",
           ['hotspot_analysis'], kind="follow_up"))
    add(_c("EV-F30", "混合着来：buffer 再来一圈 1km 的",
           ['buffer_analysis'], kind="follow_up", valid=['buffer_analysis', 'multi_ring_buffer'], lang="mixed"))

    # ── contextual（EV-P01..P30：指代省略短句）：30 ──
    add(_c("EV-P01", "那人口密度呢",
           ['spatial_aggregate'], kind="contextual", valid=['spatial_aggregate', 'heatmap_data', 'kde_surface']))
    add(_c("EV-P02", "把它放大到全屏看看",
           ['zoom_to_layer'], kind="contextual", valid=['zoom_to_layer']))
    add(_c("EV-P03", "这儿的坡度怎么样",
           ['compute_terrain'], kind="contextual", valid=['compute_terrain', 'terrain_derivatives']))
    add(_c("EV-P04", "这个点坐标转中文详细地址，带附近POI",
           ['reverse_geocode_cn'], kind="contextual", valid=['reverse_geocode_cn', 'reverse_geocode']))
    add(_c("EV-P05", "指定坐标周围半径一公里，搜索学校POI",
           ['search_poi_around'], kind="contextual"))
    add(_c("EV-P06", "查下这里面的实时路况，道路拥堵等级",
           ['get_traffic_status'], kind="contextual"))
    add(_c("EV-P07", "那几个区做分区栅格统计",
           ['zonal_stats'], kind="contextual"))
    add(_c("EV-P08", "替我把它钉在图上",
           ['add_marker'], kind="contextual"))
    add(_c("EV-P09", "这块地有多大",
           ['measure_area'], kind="contextual"))
    add(_c("EV-P10", "这俩点离多远",
           ['measure_distance'], kind="contextual"))
    add(_c("EV-P11", "这是哪儿",
           ['reverse_geocode_cn'], kind="contextual", valid=['reverse_geocode_cn', 'query_map_features', 'reverse_geocode']))
    add(_c("EV-P12", "把它删掉吧，看着碍事",
           ['remove_layer'], kind="contextual"))
    add(_c("EV-P13", "让它置顶，别被底图盖住",
           ['reorder_layer'], kind="contextual"))
    add(_c("EV-P14", "把图层显示状态改成不可见",
           ['set_layer_status'], kind="contextual", valid=['set_layer_status']))
    add(_c("EV-P15", "Zoom to it",
           ['zoom_to_layer'], kind="contextual", valid=['zoom_to_layer', 'zoom_to_bbox', 'fly_to_location'], lang="en"))
    add(_c("EV-P16", "帮我Mark it on the map",
           ['add_marker'], kind="contextual", lang="mixed"))
    add(_c("EV-P17", "这里地形坡度slope陡不陡",
           ['compute_terrain'], kind="contextual", valid=['compute_terrain', 'fetch_dem'], lang="mixed"))
    add(_c("EV-P18", "它们是统计显著的高值热点区吗",
           ['hotspot_analysis'], kind="contextual", valid=['hotspot_analysis', 'moran_i', 'general_g']))
    add(_c("EV-P19", "帮我起个名，叫核心保护区",
           ['alias_layer'], kind="contextual"))
    add(_c("EV-P20", "当前会话所有的地理数据图层展示一下",
           ['inventory_layers'], kind="contextual"))
    add(_c("EV-P21", "这份数据的时间跨度是多少",
           ['temporal_profile'], kind="contextual", valid=['temporal_profile']))
    add(_c("EV-P22", "把它转成UTM好算面积",
           ['reproject_coordinates'], kind="contextual"))
    add(_c("EV-P23", "从pour point圈出它的上游汇水贡献区",
           ['watershed_delineation'], kind="contextual"))
    add(_c("EV-P24", "下游沿流路到出口累计距离多少米",
           ['flow_length_analysis'], kind="contextual"))
    add(_c("EV-P25", "它在哪个区，边界拿一下",
           ['get_local_admin_boundary'], kind="contextual", valid=['get_local_admin_boundary', 'query_osm_boundary']))
    add(_c("EV-P26", "周边配一圈500米缓冲",
           ['buffer_analysis'], kind="contextual"))
    add(_c("EV-P27", "用它做遮罩裁剪POI，只保留范围内的",
           ['clip_layer'], kind="contextual"))
    add(_c("EV-P28", "给它也挂上所属街道",
           ['spatial_join'], kind="contextual"))
    add(_c("EV-P29", "这里的NDVI覆盖率咋样",
           ['compute_ndvi'], kind="contextual", valid=['compute_ndvi']))
    add(_c("EV-P30", "看下它的数据集Schema几何类型SRS元数据契约",
           ['describe_dataset'], kind="contextual", valid=['describe_dataset', 'profile_dataset']))

    # ── workflow_continuation（EV-W01..W30：上步产物续做）：30 ──
    add(_c("EV-W01", "落在上一步圈里的点位按多边形统计数量",
           ['spatial_aggregate'], kind="workflow_continuation"))
    add(_c("EV-W02", "拿这个缓冲面去和建筑图层求交",
           ['overlay_analysis'], kind="workflow_continuation", valid=['overlay_analysis']))
    add(_c("EV-W03", "刚才的热点结果按街道统计落入点数，做空间聚合",
           ['spatial_aggregate'], kind="workflow_continuation"))
    add(_c("EV-W04", "NDVI栅格按区县统计像元均值回写属性",
           ['zonal_stats'], kind="workflow_continuation"))
    add(_c("EV-W05", "从插值面提取等值线，带level属性",
           ['extract_contours'], kind="workflow_continuation", valid=['extract_contours']))
    add(_c("EV-W06", "把聚类结果做成分级设色专题图",
           ['create_thematic_map'], kind="workflow_continuation"))
    add(_c("EV-W07", "分析完了，出一份PDF报告归档",
           ['generate_analysis_report'], kind="workflow_continuation", valid=['generate_analysis_report']))
    add(_c("EV-W08", "把这份专题图导出成印刷级PDF",
           ['export_thematic_map'], kind="workflow_continuation"))
    add(_c("EV-W09", "下一步把坡度重分类成风险等级",
           ['raster_reclassify'], kind="workflow_continuation"))
    add(_c("EV-W10", "接着算TWI找内涝易发带",
           ['topographic_index'], kind="workflow_continuation"))
    add(_c("EV-W11", "汇流累积有了，接着提河网并分级",
           ['stream_network'], kind="workflow_continuation"))
    add(_c("EV-W12", "DEM填洼完成，接着算D8流向",
           ['flow_analysis'], kind="workflow_continuation"))
    add(_c("EV-W13", "接着跑克里金铺面，预测面和方差面一起出",
           ['kriging_interpolation'], kind="workflow_continuation"))
    add(_c("EV-W14", "点聚合到H3网格了，接着做LISA",
           ['h3_lisa'], kind="workflow_continuation"))
    add(_c("EV-W15", "把OD画成flow弧线图层，线宽按权重",
           ['od_flow_edges'], kind="workflow_continuation"))
    add(_c("EV-W16", "路网成本算完，接着跑Huff份额",
           ['network_huff_interaction'], kind="workflow_continuation"))
    add(_c("EV-W17", "边界拿到了，按它裁剪POI",
           ['clip_layer'], kind="workflow_continuation"))
    add(_c("EV-W18", "按新字段做Choropleth分层设色专题图",
           ['create_thematic_map'], kind="workflow_continuation"))
    add(_c("EV-W19", "统计值有了，生成柱状图展示",
           ['generate_chart'], kind="workflow_continuation"))
    add(_c("EV-W20", "Continue with zonal stats on the NDVI result per county",
           ['zonal_stats'], kind="workflow_continuation", lang="en"))
    add(_c("EV-W21", "接着把残差做个Moran检验",
           ['moran_i'], kind="workflow_continuation", valid=['moran_i']))
    add(_c("EV-W22", "GWR跑完，把局部R2也画成面",
           ['gwr_regression'], kind="workflow_continuation"))
    add(_c("EV-W23", "端元提完了，接着反演丰度",
           ['linear_unmixing'], kind="workflow_continuation"))
    add(_c("EV-W24", "定标完成，接着做地形辐射校正",
           ['sar_radiometric_terrain_correction'], kind="workflow_continuation"))
    add(_c("EV-W25", "去斑完成，接着按dB域统一量纲",
           ['sar_log_scale'], kind="workflow_continuation"))
    add(_c("EV-W26", "PCA降完维，用前三个分量合成图",
           ['raster_pca'], kind="workflow_continuation"))
    add(_c("EV-W27", "快照存好了，接着恢复验证一遍",
           ['restore_workspace_snapshot'], kind="workflow_continuation", valid=['restore_workspace_snapshot', 'list_workspace_snapshots']))
    add(_c("EV-W28", "中间产物固化存档，存工作空间快照",
           ['save_workspace_snapshot'], kind="workflow_continuation"))
    add(_c("EV-W29", "方案跑完，把本轮最终图层收口展示",
           ['finalize_display'], kind="workflow_continuation", valid=['finalize_display', 'display_layer']))
    add(_c("EV-W30", "对海拔栅格做分区统计，求各多边形均值",
           ['zonal_stats'], kind="workflow_continuation"))

    # ── failure_recovery（EV-R01..R24：失败换路重试）：24 ──
    add(_c("EV-R01", "刚才克里金失败了，换反距离加权重试",
           ['idw_interpolation'], kind="failure_recovery"))
    add(_c("EV-R02", "在线边界拉不到，改查本地行政库",
           ['get_local_admin_boundary'], kind="failure_recovery", valid=['get_local_admin_boundary', 'get_local_child_districts']))
    add(_c("EV-R03", "高德查不到这个POI，换OSM全量搜试试",
           ['query_osm_poi'], kind="failure_recovery", valid=['query_osm_poi', 'query_local_osm']))
    add(_c("EV-R04", "在线NDVI挂了，用已上传tif发异步任务算指数存成资产",
           ['analyze_vegetation_index'], kind="failure_recovery"))
    add(_c("EV-R05", "退回全局多项式拟合趋势面，看大尺度空间趋势",
           ['trend_surface'], kind="failure_recovery"))
    add(_c("EV-R06", "换多向流按角度比例分流，弧度角输出",
           ['dinf_flow_analysis'], kind="failure_recovery"))
    add(_c("EV-R07", "WGS84底图错位300米，重转一遍GCJ02",
           ['transform_coordinates'], kind="failure_recovery"))
    add(_c("EV-R08", "刚才的投递顺序跑崩了，查下是哪一步失败",
           ['get_plan_status'], kind="failure_recovery", valid=['get_plan_status']))
    add(_c("EV-R09", "工作空间丢了，从快照恢复回来",
           ['restore_workspace_snapshot'], kind="failure_recovery"))
    add(_c("EV-R10", "上次导出花了，换A4横版300DPI重出",
           ['export_thematic_map'], kind="failure_recovery"))
    add(_c("EV-R11", "中文地址解析失败，试试输入联想纠错",
           ['input_tips'], kind="failure_recovery", valid=['input_tips']))
    add(_c("EV-R12", "批量转坐标一半超时，缩小批次重跑",
           ['batch_geocode_cn'], kind="failure_recovery"))
    add(_c("EV-R13", "Fall back to IDW interpolation for this job",
           ['idw_interpolation'], kind="failure_recovery", lang="en"))
    add(_c("EV-R14", "误差项空间自相关，拟合SEM空间误差模型估计λ",
           ['sem_ml_regression'], kind="failure_recovery", valid=['sem_ml_regression', 'sar_ml_regression']))
    add(_c("EV-R15", "退回OLS全局回归，看系数t检验VIF和残差诊断",
           ['ols_regression'], kind="failure_recovery"))
    add(_c("EV-R16", "单景去斑糊了，改多时相强度栈抑制",
           ['sar_multitemporal_speckle'], kind="failure_recovery"))
    add(_c("EV-R17", "先做DN到sigma0辐射定标，入射角给定标常数",
           ['sar_calibrate'], kind="failure_recovery"))
    add(_c("EV-R18", "云太多NDVI全是空，换中值时序合成拼底图",
           ['medoid_composite'], kind="failure_recovery"))
    add(_c("EV-R19", "先画直线缓冲多边形顶一下，距离圈",
           ['buffer_analysis'], kind="failure_recovery"))
    add(_c("EV-R20", "没路网就在线规划一条驾车路线，给距离时间",
           ['plan_route'], kind="failure_recovery"))
    add(_c("EV-R21", "重新执行MapSpec编译，产出style.json和报告",
           ['webgis_compile_maplibre'], kind="failure_recovery", valid=['webgis_compile_maplibre', 'webgis_validate']))
    add(_c("EV-R22", "改崩了，回滚到上一个快照点",
           ['webgis_rollback'], kind="failure_recovery"))
    add(_c("EV-R23", "属性筛选筛多了，改查询表达式重筛成新要素集",
           ['attribute_filter'], kind="failure_recovery", valid=['attribute_filter', 'apply_layer_filter']))
    add(_c("EV-R24", "统计口径错了，按多边形重数点数空间聚合",
           ['spatial_aggregate'], kind="failure_recovery"))

    # ── map_edit（EV-M01..M30 缺 M26/M27/M29：成图微调）：27 ──
    add(_c("EV-M01", "图层颜色太素，换个颜色顺手调下点大小",
           ['update_layer_appearance'], kind="map_edit", valid=['update_layer_appearance']))
    add(_c("EV-M02", "hide这层，显示状态set成不可见",
           ['set_layer_status'], kind="map_edit", valid=['set_layer_status', 'apply_layer_style'], lang="mixed"))
    add(_c("EV-M03", "色带按YlOrRd分层设色做专题图，自动分级",
           ['create_thematic_map'], kind="map_edit", valid=['create_thematic_map', 'update_layer_appearance']))
    add(_c("EV-M04", "改版面配置，图例位置放左下角",
           ['webgis_layout_set'], kind="map_edit", valid=['webgis_layout_set']))
    add(_c("EV-M05", "换一个指南针样式的指北针",
           ['webgis_component_update'], kind="map_edit"))
    add(_c("EV-M06", "把比例尺放到右下角",
           ['webgis_component_update'], kind="map_edit", valid=['webgis_component_update']))
    add(_c("EV-M07", "底图换成深色款",
           ['switch_base_layer'], kind="map_edit"))
    add(_c("EV-M08", "修改图层视觉样式，点大小调小",
           ['update_layer_appearance'], kind="map_edit"))
    add(_c("EV-M09", "线改成红色虚线，线宽2",
           ['update_layer_appearance'], kind="map_edit"))
    add(_c("EV-M10", "套用画廊里那套深蓝制图模板",
           ['apply_template'], kind="map_edit", valid=['apply_template', 'list_templates']))
    add(_c("EV-M11", "先看看有哪些版式模板可选",
           ['list_templates'], kind="map_edit"))
    add(_c("EV-M12", "把分析结果图层置顶显示",
           ['reorder_layer'], kind="map_edit"))
    add(_c("EV-M13", "关掉中间过程图层，只留最终结果",
           ['display_layer'], kind="map_edit", valid=['display_layer', 'set_layer_status']))
    add(_c("EV-M14", "Switch to the dark basemap",
           ['switch_base_layer'], kind="map_edit", lang="en"))
    add(_c("EV-M15", "把points调小，颜色改成blue",
           ['update_layer_appearance'], kind="map_edit", lang="mixed"))
    add(_c("EV-M16", "Set layout config: move legend to bottom left",
           ['webgis_layout_set'], kind="map_edit", valid=['webgis_layout_set', 'webgis_component_update'], lang="en"))
    add(_c("EV-M17", "把浮动图表挪到右下角别挡图例",
           ['control_floating_chart'], kind="map_edit"))
    add(_c("EV-M18", "柱状图换成饼图展示",
           ['control_floating_chart'], kind="map_edit", valid=['control_floating_chart', 'generate_chart']))
    add(_c("EV-M19", "当前地图都挂了哪些组件，列出来看看",
           ['webgis_component_catalog'], kind="map_edit"))
    add(_c("EV-M20", "视图俯仰调成45度做3D效果",
           ['webgis_view_set'], kind="map_edit", valid=['webgis_view_set', 'set_map_view']))
    add(_c("EV-M21", "回到全国默认视角",
           ['reset_map_view'], kind="map_edit"))
    add(_c("EV-M22", "把地图缩放到分析结果的全貌",
           ['zoom_to_layer'], kind="map_edit", valid=['zoom_to_layer', 'zoom_to_bbox']))
    add(_c("EV-M23", "删掉这个多余的分析图层",
           ['remove_layer'], kind="map_edit", valid=['remove_layer']))
    add(_c("EV-M24", "标注下王府井这个地标",
           ['add_marker'], kind="map_edit"))
    add(_c("EV-M25", "标题署名补上，再合成一版主题",
           ['combine_map_theme'], kind="map_edit", valid=['combine_map_theme']))
    add(_c("EV-M28", "校验下MapSpec规范再编译",
           ['webgis_validate'], kind="map_edit", valid=['webgis_validate']))
    add(_c("EV-M30", "把图例换个位置",
           ['webgis_layout_set'], kind="map_edit", valid=['webgis_layout_set', 'webgis_component_update']))
    return cases


_CORPUS: List[RetrievalEvalCase] = []


def get_retrieval_eval_corpus() -> List[RetrievalEvalCase]:
    """进程级缓存（与 retrieval_corpus.get_retrieval_corpus 同门）。"""
    global _CORPUS
    if not _CORPUS:
        _CORPUS = build_retrieval_eval_corpus()
    return list(_CORPUS)


@dataclass(frozen=True)
class RetrievalEvalReport:
    """开环 query→tool 评测指标（确定性）。"""

    cases: int
    precision_at_1: float
    recall_at_5: float
    recall_at_10: float
    invalid_selection_rate: float
    fallback_rate: float
    tier3_leak: int
    by_kind: Dict[str, Dict[str, float]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cases": self.cases,
            "precision_at_1": self.precision_at_1,
            "recall_at_5": self.recall_at_5,
            "recall_at_10": self.recall_at_10,
            "invalid_selection_rate": self.invalid_selection_rate,
            "fallback_rate": self.fallback_rate,
            "tier3_leak": self.tier3_leak,
            "by_kind": {k: dict(v) for k, v in self.by_kind.items()},
        }


def retrieval_eval_report(
    registry: Any,
    cases: Sequence[RetrievalEvalCase],
    *,
    k_max: int = 30,
) -> RetrievalEvalReport:
    """对人工金标案例跑真实 surface 选择并统计开环指标。

    **开环口径**：只给 ``user_message``（不给 active_capabilities 提示
    —— 生产里该信号来自 planner 的意图归类；本评测刻意剥离，量纯检索）。
    """
    from app.services.chat.tool_surface_v3 import (
        DynamicToolSurface,
        ToolSelectionContext,
    )

    from app.services.chat.tool_surface_v3 import CORE_TOOL_NAMES

    core_names = set(CORE_TOOL_NAMES)
    surface = DynamicToolSurface(registry)
    p1_hits = 0
    r5_vals: List[float] = []
    r10_vals: List[float] = []
    invalid = 0
    must_not_total = 0
    fallback = 0
    tier3_leak = 0
    by_kind: Dict[str, List[Dict[str, float]]] = {}

    for case in cases:
        ctx = ToolSelectionContext(
            user_message=case.query,
            k_max=k_max,
        )
        sel = surface.select(ctx)
        names = list(sel.names)
        # 开环 precision 口径在**检索排序段**上：CORE 常驻管道工具
        # （intent/product/status/list）无条件前置、与 query 无关，若计入
        # top-1 则恒为 core —— 剔除后才是 query→tool 检索质量。
        ranked = [n for n in names if n not in core_names]
        if not ranked:
            fallback += 1
        valid = set(case.valid_tools)
        must_not = set(case.must_not_select)
        if ranked and ranked[0] in valid:
            p1_hits += 1
        for name in ranked[:5]:
            if name in must_not:
                invalid += 1
                break
        if must_not:
            must_not_total += 1
        for k, acc in ((5, r5_vals), (10, r10_vals)):
            top = set(ranked[:k])
            acc.append(len(top & valid) / len(valid) if valid else 1.0)
        for name in names:
            try:
                desc = registry.descriptor(name)
            except KeyError:
                continue
            if int(desc.tier) >= 3 or desc.effective_security_tier >= 3:
                tier3_leak += 1

        kind_stats = by_kind.setdefault(case.kind, [])
        kind_stats.append({
            "p1": 1.0 if ranked and ranked[0] in valid else 0.0,
            "invalid": 1.0 if any(n in must_not for n in ranked[:5]) else 0.0,
        })

    n = max(1, len(cases))
    kind_summary: Dict[str, Dict[str, float]] = {}
    for kind, stats in by_kind.items():
        m = max(1, len(stats))
        kind_summary[kind] = {
            "cases": float(len(stats)),
            "precision_at_1": round(sum(x["p1"] for x in stats) / m, 4),
            "invalid_selection_rate": round(
                sum(x["invalid"] for x in stats) / m, 4),
        }
    return RetrievalEvalReport(
        cases=len(cases),
        precision_at_1=round(p1_hits / n, 4),
        recall_at_5=round(sum(r5_vals) / max(1, len(r5_vals)), 4),
        recall_at_10=round(sum(r10_vals) / max(1, len(r10_vals)), 4),
        invalid_selection_rate=round(invalid / max(1, must_not_total), 4),
        fallback_rate=round(fallback / n, 4),
        tier3_leak=tier3_leak,
        by_kind=kind_summary,
    )


__all__ = [
    "RetrievalEvalCase",
    "RetrievalEvalReport",
    "MIN_EVAL_CORPUS_SIZE",
    "build_retrieval_eval_corpus",
    "get_retrieval_eval_corpus",
    "retrieval_eval_report",
]
