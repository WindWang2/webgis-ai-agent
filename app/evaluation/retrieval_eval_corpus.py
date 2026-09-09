"""Retrieval V5 open-loop query→tool 评测语料（ADR-0118 决策 D7）。

V4 基线的缺口（Phase-0 审计 §7）：``retrieval_corpus`` 由 306 意图
golden + paraphrase 层生成，期望工具经 AlgorithmRegistry **capability
反查** —— 量的是 capability→surface projection，不是「用户口语 query
→ 正确工具」的开环检索精度；且无 curated hard-negative / 近重复对 /
歧义案例。

V5 语料（全部**人工金标**，与 lexical 索引 / capability 反查不同源，
不存在答案写死对齐索引词表的通道；查询为口语化措辞，非描述符文案）：

- ``direct``：明确措辞 → 唯一期望工具；
- ``near_duplicate``：最小限定词翻转的兄弟对（期望 = 正确侧，
  ``must_not_select`` = 兄弟侧 —— 限定词必须改变选择）；
- ``hard_negative``：词面陷阱（距离邻近 vs 路网可达、点计数 vs 栅格
  分区统计、批量 vs 单条……期望 = 正确工具，``must_not_select`` =
  陷阱工具）；
- ``ambiguous``：措辞天然多解（``valid_tools`` ≥2，precision@1 按
  「top-1 ∈ 合法集」计 —— 不把歧义当错误，也不当满分对齐）；
- ``paraphrase``（EV-P）：direct 意图的口语换述（与 EV-D 同工具不同
  措辞，词法可过 —— 含工具标签关键词；``must_not`` 为空，不计
  invalid）。

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
    kind: str                       # direct | near_duplicate | hard_negative | ambiguous | paraphrase
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

    # ── paraphrase（240；EV-P；口语换述，词法可过，must_not 为空）───
    # 几何 / 缓冲 / 叠加 / 格网
    add(_c("EV-P001", "学校周边画个500米缓冲区看看覆盖范围",
           ["buffer_analysis"], valid=["buffer_analysis", "multi_ring_buffer"],
           kind="paraphrase"))
    add(_c("EV-P002", "按500、1000、1500米画三圈多环缓冲带",
           ["multi_ring_buffer"], kind="paraphrase"))
    add(_c("EV-P003", "绿地和建成区两个图层叠加求交看看重叠部分",
           ["overlay_analysis"], kind="paraphrase"))
    add(_c("EV-P004", "拿区县边界把这景影像裁一块出来",
           ["clip_layer"], kind="paraphrase"))
    add(_c("EV-P005", "同属一个区的街道碎斑合并成整块",
           ["dissolve_layer"], kind="paraphrase"))
    add(_c("EV-P006", "给这堆离散采样点套个最小凸包",
           ["convex_hull"], kind="paraphrase"))
    add(_c("EV-P007", "按现有商场位置划分泰森多边形商圈",
           ["voronoi_polygons"], kind="paraphrase"))
    add(_c("EV-P008", "研究区内铺500米见方的渔网格做统计底图",
           ["fishnet_grid"], kind="paraphrase"))
    add(_c("EV-P009", "订单按H3六边形格子聚合看密度分布",
           ["h3_binning"], kind="paraphrase"))
    add(_c("EV-P010", "把POI点的所属街道属性挂接上去",
           ["spatial_join"], kind="paraphrase"))
    add(_c("EV-P011", "建成区擦除水域剩下的可开发用地",
           ["overlay_analysis"], kind="paraphrase"))
    add(_c("EV-P012", "用流域边界裁出上游那块DEM",
           ["clip_layer"], kind="paraphrase"))
    # POI / OSM / 地理编码 / 行政区划
    add(_c("EV-P013", "搜人民广场周边两公里内的健身房",
           ["search_poi_around"], kind="paraphrase"))
    add(_c("EV-P014", "本地库查中关村附近的咖啡馆POI",
           ["query_local_poi"], kind="paraphrase"))
    add(_c("EV-P015", "本地OSM库捞这片的道路和建筑数据",
           ["query_local_osm"], kind="paraphrase"))
    add(_c("EV-P016", "全市范围搜一下加油站POI",
           ["search_poi"], kind="paraphrase"))
    add(_c("EV-P017", "这个圈定多边形里面的药店有哪些",
           ["search_poi_polygon"], kind="paraphrase"))
    add(_c("EV-P018", "在线OSM查这片的学校POI",
           ["query_osm_poi"], kind="paraphrase"))
    add(_c("EV-P019", "OSM上把这片建筑物轮廓拉下来",
           ["query_osm_buildings"], kind="paraphrase"))
    add(_c("EV-P020", "下载二环里面的OSM道路网",
           ["query_osm_roads"], kind="paraphrase"))
    add(_c("EV-P021", "OSM查一下这个区的行政边界线",
           ["query_osm_boundary"], kind="paraphrase"))
    add(_c("EV-P022", "把商圈里的餐饮POI搜集抽取落库",
           ["search_and_extract_poi"], kind="paraphrase"))
    add(_c("EV-P023", "北京市朝阳区建国路88号转成经纬度",
           ["geocode_cn"], kind="paraphrase"))
    add(_c("EV-P024", "这800条门店地址一起批量转坐标",
           ["batch_geocode_cn"], kind="paraphrase"))
    add(_c("EV-P025", "这个经纬度反查中文地址是哪儿",
           ["reverse_geocode_cn"], kind="paraphrase"))
    add(_c("EV-P026", "这个坐标点反查对应的地址信息",
           ["reverse_geocode"], valid=["reverse_geocode", "reverse_geocode_cn"],
           kind="paraphrase"))
    add(_c("EV-P027", "这条英文地址转成经纬度坐标",
           ["geocode"], kind="paraphrase"))
    add(_c("EV-P028", "查这地方的行政区划归属",
           ["get_admin_division"], kind="paraphrase"))
    add(_c("EV-P029", "查一下这个区的区划信息",
           ["get_district"], kind="paraphrase"))
    add(_c("EV-P030", "列出该市下属所有区县",
           ["get_child_districts"], kind="paraphrase"))
    add(_c("EV-P031", "拿下属街道的多边形边界",
           ["get_sub_districts_polygons"], kind="paraphrase"))
    add(_c("EV-P032", "查这个乡镇的中心点位置",
           ["get_township_center"], kind="paraphrase"))
    add(_c("EV-P033", "本地库取行政区划边界",
           ["get_local_admin_boundary"], kind="paraphrase"))
    add(_c("EV-P034", "本地库列下级区划清单",
           ["get_local_child_districts"], kind="paraphrase"))
    add(_c("EV-P035", "本地统计年鉴查常住人口数据",
           ["query_local_yearbook"], kind="paraphrase"))
    add(_c("EV-P036", "看看本地OSM目录里有哪些类别",
           ["get_local_osm_catalog"], kind="paraphrase"))
    add(_c("EV-P037", "本地统计目录查指标口径",
           ["get_local_stats_catalog"], kind="paraphrase"))
    # 数据管理 / 摄取 / 目录
    add(_c("EV-P038", "按条件查这份数据集里的记录",
           ["query_dataset"], kind="paraphrase"))
    add(_c("EV-P039", "联邦跨库查一下这批数据",
           ["query_federated_data"], kind="paraphrase"))
    add(_c("EV-P040", "联邦链式查询关联多个源",
           ["query_federated_chain"], kind="paraphrase"))
    add(_c("EV-P041", "搜个历史人口数据集用用",
           ["search_datasets"], kind="paraphrase"))
    add(_c("EV-P042", "空间数据目录里检索影像",
           ["search_spatial_catalog"], kind="paraphrase"))
    add(_c("EV-P043", "列出现有数据集清单",
           ["list_datasets"], kind="paraphrase"))
    add(_c("EV-P044", "我之前上传过哪些数据",
           ["list_uploaded_data"], kind="paraphrase"))
    add(_c("EV-P045", "把这份CSV上传入库",
           ["ingest_dataset"], kind="paraphrase"))
    add(_c("EV-P046", "接一个新的外部数据源进来",
           ["connect_data_source"], kind="paraphrase"))
    add(_c("EV-P047", "把查询结果物化存下来",
           ["materialize_dataset"], kind="paraphrase"))
    add(_c("EV-P048", "给这份数据做个画像摸底",
           ["profile_dataset"], kind="paraphrase"))
    add(_c("EV-P049", "读一下schema几何类型和坐标系元数据",
           ["describe_dataset"], kind="paraphrase"))
    add(_c("EV-P050", "上传后先看bbox数值分布和时间字段",
           ["webgis_source_profile"], kind="paraphrase"))
    add(_c("EV-P051", "当前工作区里都有啥东西",
           ["describe_workspace"], kind="paraphrase"))
    add(_c("EV-P052", "盘点一下地图上所有图层",
           ["inventory_layers"], kind="paraphrase"))
    add(_c("EV-P053", "把这个数据源刷新到最新",
           ["refresh_data_source"], kind="paraphrase"))
    add(_c("EV-P054", "修一下几何拓扑和坐标系问题",
           ["repair_spatial_dataset"], kind="paraphrase"))
    add(_c("EV-P055", "几何拓扑CRS质量审计一遍",
           ["audit_spatial_quality"], kind="paraphrase"))
    add(_c("EV-P056", "查一下上传任务的信息",
           ["get_upload_info"], kind="paraphrase"))
    add(_c("EV-P057", "点选查一下图上要素的属性",
           ["query_map_features"], kind="paraphrase"))
    add(_c("EV-P058", "数据集按分组聚合汇总",
           ["aggregate_dataset"], kind="paraphrase"))
    add(_c("EV-P059", "工作区快照都有哪些",
           ["list_workspace_snapshots"], kind="paraphrase"))
    add(_c("EV-P060", "推荐个合适的分析套路",
           ["suggest_analysis_patterns"], kind="paraphrase"))
    # 插值
    add(_c("EV-P061", "雨量站反距离加权插成降水面",
           ["idw_interpolation"], kind="paraphrase"))
    add(_c("EV-P062", "普通克里金做土壤重金属分布面",
           ["kriging_interpolation"], kind="paraphrase"))
    add(_c("EV-P063", "三角网插值必须穿过原始观测点",
           ["tin_interpolation"], kind="paraphrase"))
    add(_c("EV-P064", "拟合个趋势面看区域整体起伏大势",
           ["trend_surface"], kind="paraphrase"))
    add(_c("EV-P065", "带高程协变量的协同克里金插值",
           ["cokriging_surface"], kind="paraphrase"))
    add(_c("EV-P066", "回归克里金先回归再对残差插值",
           ["regression_kriging"], kind="paraphrase"))
    add(_c("EV-P067", "径向基函数插值铺连续表面",
           ["rbf_interpolation"], kind="paraphrase"))
    add(_c("EV-P068", "自然邻域插值生成地形表面",
           ["natural_neighbor_surface"], kind="paraphrase"))
    add(_c("EV-P069", "指示克里金算重金属超标概率面",
           ["indicator_kriging_surface"], kind="paraphrase"))
    add(_c("EV-P070", "块克里金估计地块平均品位",
           ["block_kriging_surface"], kind="paraphrase"))
    add(_c("EV-P071", "时空克里金做PM2.5动态场",
           ["st_kriging_surface"], kind="paraphrase"))
    add(_c("EV-P072", "交叉验证比比哪种插值模型更准",
           ["interpolation_model_compare"], kind="paraphrase"))
    add(_c("EV-P073", "最近邻快速铺个面先看大概",
           ["nearest_neighbor_surface"], kind="paraphrase"))
    add(_c("EV-P074", "线性协同区域化LMC协同克里金",
           ["cokriging_lmc_surface"], kind="paraphrase"))
    # 空间统计 / 点格局 / 聚类
    add(_c("EV-P075", "全局Moran's I检验聚集离散还是随机",
           ["moran_i"], kind="paraphrase"))
    add(_c("EV-P076", "高低值聚类Getis检验显著性",
           ["general_g"], kind="paraphrase"))
    add(_c("EV-P077", "Geary's C空间自相关检验",
           ["geary_c"], kind="paraphrase"))
    add(_c("EV-P078", "Gi*热点分析找显著热点和冷点",
           ["hotspot_analysis"], kind="paraphrase"))
    add(_c("EV-P079", "门店位置做空间密度聚类分群",
           ["spatial_cluster"], kind="paraphrase"))
    add(_c("EV-P080", "订单带时间戳做时空联合聚类",
           ["st_dbscan"], kind="paraphrase"))
    add(_c("EV-P081", "局部Geary看各单元空间关联类型",
           ["local_geary"], kind="paraphrase"))
    add(_c("EV-P082", "双变量Moran看房价与绿地空间关联",
           ["bivariate_moran"], kind="paraphrase"))
    add(_c("EV-P083", "双变量LISA集聚图画一下",
           ["bivariate_local_moran"], kind="paraphrase"))
    add(_c("EV-P084", "双变量Join Count邻接检验",
           ["bivariate_join_count"], kind="paraphrase"))
    add(_c("EV-P085", "Join Count检验二值分布聚集性",
           ["join_count"], kind="paraphrase"))
    add(_c("EV-P086", "局部Join Count找聚集热点",
           ["local_join_count"], kind="paraphrase"))
    add(_c("EV-P087", "样方计数检验点的分布模式",
           ["quadrat_analysis"], kind="paraphrase"))
    add(_c("EV-P088", "最近邻指数判断聚集还是随机",
           ["nearest_neighbor"], kind="paraphrase"))
    add(_c("EV-P089", "Ripley K多尺度分析聚集尺度",
           ["ripley_k_analysis"], kind="paraphrase"))
    add(_c("EV-P090", "Ripley K包络线显著性检验",
           ["ripley_k_envelope_analysis"], kind="paraphrase"))
    add(_c("EV-P091", "点对相关函数看聚集强度随距离变化",
           ["pcf_analysis"], kind="paraphrase"))
    add(_c("EV-P092", "交叉PCF看两类点吸引还是排斥",
           ["cross_pcf_analysis"], kind="paraphrase"))
    add(_c("EV-P093", "交叉K函数检验两类设施共生关系",
           ["cross_k_analysis"], kind="paraphrase"))
    add(_c("EV-P094", "Knox检验病例时空交互聚集",
           ["knox_analysis"], kind="paraphrase"))
    add(_c("EV-P095", "时空K函数看疫情扩散过程",
           ["space_time_k_analysis"], kind="paraphrase"))
    add(_c("EV-P096", "找分布最居中的代表性要素",
           ["central_feature"], kind="paraphrase"))
    add(_c("EV-P097", "标准差椭圆看分布方向和离散程度",
           ["standard_deviational_ellipse"], kind="paraphrase"))
    add(_c("EV-P098", "G/F/J函数综合判断点格局类型",
           ["g_f_j_analysis"], kind="paraphrase"))
    add(_c("EV-P099", "跑个空间统计看整体分布格局",
           ["spatial_stats"], kind="paraphrase"))
    add(_c("EV-P100", "时空热点看哪些片区新增增强",
           ["emerging_hotspot_analysis"], kind="paraphrase"))
    add(_c("EV-P101", "H3格子上做LISA聚类",
           ["h3_lisa"], kind="paraphrase"))
    # 回归 / 地理探测器 / 权重
    add(_c("EV-P102", "最小二乘回归带VIF和空间诊断",
           ["ols_regression"], kind="paraphrase"))
    add(_c("EV-P103", "地理加权回归看系数空间分异",
           ["gwr_regression"], kind="paraphrase"))
    add(_c("EV-P104", "多尺度GWR各自带宽做回归",
           ["mgwr_regression"], kind="paraphrase"))
    add(_c("EV-P105", "SLX空间滞后解释变量回归",
           ["slx_regression"], kind="paraphrase"))
    add(_c("EV-P106", "地理探测器q值测因子解释力",
           ["geodetector"], kind="paraphrase"))
    add(_c("EV-P107", "生态探测器比较因子间差异",
           ["geodetector_ecological"], kind="paraphrase"))
    add(_c("EV-P108", "风险探测器做分区差异检验",
           ["geodetector_risk"], kind="paraphrase"))
    add(_c("EV-P109", "小样本发病率平滑处理一下",
           ["rate_smoothing"], kind="paraphrase"))
    add(_c("EV-P110", "空间权重矩阵做个诊断",
           ["weights_diagnostics"], kind="paraphrase"))
    add(_c("EV-P111", "换权重方案做敏感性检验",
           ["weights_sensitivity"], kind="paraphrase"))
    add(_c("EV-P112", "Mantel检验两距离矩阵相关性",
           ["mantel_test_analysis"], kind="paraphrase"))
    add(_c("EV-P113", "分方向变异函数看各向异性",
           ["directional_variogram_analysis"], kind="paraphrase"))
    add(_c("EV-P114", "变异函数理论模型比选",
           ["variogram_model_selection"], kind="paraphrase"))
    add(_c("EV-P115", "序贯高斯模拟生成多个实现",
           ["sgs_simulation"], kind="paraphrase"))
    add(_c("EV-P116", "空间决策综合评价打分",
           ["spatial_decision_v3"], kind="paraphrase"))
    add(_c("EV-P117", "乡镇人口按居住区面积权重重分配",
           ["dasymetric_reallocation"], kind="paraphrase"))
    add(_c("EV-P118", "稳健标准化去掉量纲影响",
           ["robust_normalize"], kind="paraphrase"))
    add(_c("EV-P119", "给个分析方案建议",
           ["propose_plan"], kind="paraphrase"))
    add(_c("EV-P120", "按方案把分析执行起来",
           ["execute_plan"], kind="paraphrase"))
    # 水文地形
    add(_c("EV-P121", "算一下坡度坡向和山体阴影",
           ["compute_terrain"], kind="paraphrase"))
    add(_c("EV-P122", "DEM提取河网做Strahler分级",
           ["stream_network"], kind="paraphrase"))
    add(_c("EV-P123", "圈出断面上游汇水流域范围",
           ["watershed_delineation"], kind="paraphrase"))
    add(_c("EV-P124", "DEM洼地填充做水文预处理",
           ["depression_fill"], kind="paraphrase"))
    add(_c("EV-P125", "Dinf多流向分析跑一遍",
           ["dinf_flow_analysis"], kind="paraphrase"))
    add(_c("EV-P126", "算算到出口的汇流路径长度",
           ["flow_length_analysis"], kind="paraphrase"))
    add(_c("EV-P127", "流向流量栅格分析一遍",
           ["flow_analysis"], kind="paraphrase"))
    add(_c("EV-P128", "水文综合分析来一套",
           ["hydrology_v4_analysis"], kind="paraphrase"))
    add(_c("EV-P129", "地形湿度指数TWI计算",
           ["topographic_index"], kind="paraphrase"))
    add(_c("EV-P130", "坡长坡度LS因子提取",
           ["ls_factor_analysis"], kind="paraphrase"))
    add(_c("EV-P131", "流域形态计量特征算一下",
           ["watershed_morphometry_analysis"], kind="paraphrase"))
    add(_c("EV-P132", "老城区天空开阔度SVF分布",
           ["sky_view_factor_analysis"], kind="paraphrase"))
    add(_c("EV-P133", "瞭望塔视域通视范围分析",
           ["viewshed_analysis"], kind="paraphrase"))
    add(_c("EV-P134", "地平线高度角遮蔽分析",
           ["horizon_angle_analysis"], kind="paraphrase"))
    add(_c("EV-P135", "地形开阔度正负向分析",
           ["terrain_openness_analysis"], kind="paraphrase"))
    add(_c("EV-P136", "地形曲率粗糙度衍生因子",
           ["terrain_derivatives"], kind="paraphrase"))
    add(_c("EV-P137", "geomorphon地貌形态分类",
           ["geomorphon_analysis"], kind="paraphrase"))
    add(_c("EV-P138", "地貌类型划分分区",
           ["landform_classify"], kind="paraphrase"))
    add(_c("EV-P139", "DEM提取等高线成图",
           ["extract_contours"], kind="paraphrase"))
    add(_c("EV-P140", "多方位山体阴影增强地形立体感",
           ["multiazimuth_hillshade"], kind="paraphrase"))
    # 栅格 / 植被指数 / 变化检测
    add(_c("EV-P141", "栅格计算器像元运算求NDVI",
           ["raster_calculator"], kind="paraphrase"))
    add(_c("EV-P142", "算NDVI看看植被长势",
           ["compute_ndvi"], kind="paraphrase"))
    add(_c("EV-P143", "算植被指数成图看看",
           ["compute_vegetation_index"], kind="paraphrase"))
    add(_c("EV-P144", "算光谱指数突出水体信息",
           ["compute_spectral_index"], kind="paraphrase"))
    add(_c("EV-P145", "植被指数时空分析",
           ["analyze_vegetation_index"], kind="paraphrase"))
    add(_c("EV-P146", "缨帽变换提绿度亮度湿度分量",
           ["tasseled_cap"], kind="paraphrase"))
    add(_c("EV-P147", "栅格主成分降维压缩波段",
           ["raster_pca"], kind="paraphrase"))
    add(_c("EV-P148", "MNF变换压噪声提信号",
           ["mnf_transform"], kind="paraphrase"))
    add(_c("EV-P149", "ICA独立成分分离",
           ["ica_transform"], kind="paraphrase"))
    add(_c("EV-P150", "栅格重分类分成五级",
           ["raster_reclassify"], kind="paraphrase"))
    add(_c("EV-P151", "栅格重采样统一到30米",
           ["raster_resample"], kind="paraphrase"))
    add(_c("EV-P152", "两期NDVI栅格变化检测",
           ["detect_raster_change"], kind="paraphrase"))
    add(_c("EV-P153", "变化向量CVA幅度和方向角",
           ["detect_change_cva"], kind="paraphrase"))
    add(_c("EV-P154", "比值法做变化检测",
           ["detect_ratio_change"], kind="paraphrase"))
    add(_c("EV-P155", "MAD多变量自动变化检测",
           ["mad_change"], kind="paraphrase"))
    add(_c("EV-P156", "植被覆盖变化检测",
           ["detect_vegetation_change"], kind="paraphrase"))
    add(_c("EV-P157", "污染栅格按行政区分区统计均值",
           ["zonal_stats"], kind="paraphrase"))
    add(_c("EV-P158", "数数每个街道里餐饮POI数量",
           ["spatial_aggregate"], kind="paraphrase"))
    add(_c("EV-P159", "高斯核密度铺连续密度面",
           ["kde_surface"], kind="paraphrase"))
    add(_c("EV-P160", "核密度等值线圈定高密度区",
           ["kde_contours"], kind="paraphrase"))
    add(_c("EV-P161", "生成热力图渲染数据",
           ["heatmap_data"], kind="paraphrase"))
    add(_c("EV-P162", "波段间相关系数表算一下",
           ["band_correlation_table"], kind="paraphrase"))
    # 高光谱 / 分割 / 合成 / SAR
    add(_c("EV-P163", "VCA提取纯净端元光谱",
           ["extract_endmembers_vca"], kind="paraphrase"))
    add(_c("EV-P164", "线性解混算组分丰度",
           ["linear_unmixing"], kind="paraphrase"))
    add(_c("EV-P165", "光谱角SAM监督分类",
           ["spectral_angle_mapper"], kind="paraphrase"))
    add(_c("EV-P166", "光谱信息散度分类",
           ["spectral_information_divergence"], kind="paraphrase"))
    add(_c("EV-P167", "匹配滤波找特定矿物地物",
           ["matched_filter"], kind="paraphrase"))
    add(_c("EV-P168", "RX异常探测找异常像元",
           ["rx_anomaly"], kind="paraphrase"))
    add(_c("EV-P169", "影像分割成均质对象",
           ["segment_image"], kind="paraphrase"))
    add(_c("EV-P170", "多期中值合成去云拼图",
           ["medoid_composite"], kind="paraphrase"))
    add(_c("EV-P171", "亮度阈值云掩膜质检",
           ["cloud_qc_basic"], kind="paraphrase"))
    add(_c("EV-P172", "大TIF转COG上瓦片服务",
           ["convert_raster_to_cog"], kind="paraphrase"))
    add(_c("EV-P173", "挑一景低云量的Sentinel-2影像",
           ["fetch_sentinel"], kind="paraphrase"))
    add(_c("EV-P174", "拉一份30米高程DEM",
           ["fetch_dem"], kind="paraphrase"))
    add(_c("EV-P175", "SAR辐射定标转后向散射系数",
           ["sar_calibrate"], kind="paraphrase"))
    add(_c("EV-P176", "SAR斑点噪声滤波处理",
           ["sar_speckle_filter"], kind="paraphrase"))
    add(_c("EV-P177", "干涉相干性估计",
           ["sar_coherence_estimate"], kind="paraphrase"))
    add(_c("EV-P178", "SAR灰度共生矩阵纹理",
           ["sar_glcm_texture"], kind="paraphrase"))
    add(_c("EV-P179", "SAR多时相统计合成",
           ["sar_temporal_stats"], kind="paraphrase"))
    add(_c("EV-P180", "VH极化比值看作物长势",
           ["sar_vh_ratio"], kind="paraphrase"))
    # 时间序列
    add(_c("EV-P181", "绿地率升降趋势Mann-Kendall检验",
           ["temporal_trend"], kind="paraphrase"))
    add(_c("EV-P182", "找销售额均值跳变那个月份",
           ["temporal_changepoint"], kind="paraphrase"))
    add(_c("EV-P183", "月度序列做季节分解",
           ["temporal_seasonal_decompose"], kind="paraphrase"))
    add(_c("EV-P184", "时序按月聚合求平均",
           ["temporal_aggregate"], kind="paraphrase"))
    add(_c("EV-P185", "时序滑动平均滤波平滑一下",
           ["temporal_filter"], kind="paraphrase"))
    add(_c("EV-P186", "看这个像元的时间剖面曲线",
           ["temporal_profile"], kind="paraphrase"))
    add(_c("EV-P187", "提取时序峰值谷值特征",
           ["temporal_features"], kind="paraphrase"))
    add(_c("EV-P188", "时序突变变化检测",
           ["temporal_change"], kind="paraphrase"))
    # 路网 / 可达性 / 出行
    add(_c("EV-P189", "地铁站15分钟步行等时圈",
           ["service_area_simple"], kind="paraphrase"))
    add(_c("EV-P190", "沿路网服务区覆盖分析",
           ["network_service_area"], kind="paraphrase"))
    add(_c("EV-P191", "路网最短路径步行驾车导航",
           ["network_shortest_path"], kind="paraphrase"))
    add(_c("EV-P192", "多起讫点OD成本矩阵",
           ["network_od_matrix"], kind="paraphrase"))
    add(_c("EV-P193", "OD流线边可视化",
           ["od_flow_edges"], kind="paraphrase"))
    add(_c("EV-P194", "最近设施分配就医距离",
           ["network_closest_facility"], kind="paraphrase"))
    add(_c("EV-P195", "离我最近的设施点在哪",
           ["nearest_facility"], kind="paraphrase"))
    add(_c("EV-P196", "路网中心性找关键节点",
           ["network_centrality"], kind="paraphrase"))
    add(_c("EV-P197", "小区路网可达性评价",
           ["network_accessibility"], kind="paraphrase"))
    add(_c("EV-P198", "重力模型算岗位可达性",
           ["network_gravity_access"], kind="paraphrase"))
    add(_c("EV-P199", "Huff模型算商场吸引概率",
           ["network_huff_interaction"], kind="paraphrase"))
    add(_c("EV-P200", "仓库到门店配送距离时间矩阵",
           ["distance_matrix_cn"], kind="paraphrase"))
    add(_c("EV-P201", "这两地开车过去多远",
           ["measure_distance"],
           valid=["measure_distance", "distance_matrix_cn"], kind="paraphrase"))
    add(_c("EV-P202", "量一下这地块面积多大",
           ["measure_area"], kind="paraphrase"))
    add(_c("EV-P203", "等时线看15分钟生活圈",
           ["isochrone_analysis"], kind="paraphrase"))
    add(_c("EV-P204", "路网等时圈分析",
           ["isochrone_network"], kind="paraphrase"))
    add(_c("EV-P205", "规划条骑行路线",
           ["plan_route"], kind="paraphrase"))
    add(_c("EV-P206", "公交换乘路线查一下",
           ["search_transit_route"], kind="paraphrase"))
    add(_c("EV-P207", "查下实时路况拥堵情况",
           ["get_traffic_status"], kind="paraphrase"))
    add(_c("EV-P208", "高德坐标批量转WGS84",
           ["transform_coordinates"], kind="paraphrase"))
    add(_c("EV-P209", "数据重投影换个坐标系",
           ["reproject_coordinates"], kind="paraphrase"))
    # 制图 / 可视化 / 地图交互
    add(_c("EV-P210", "导出带图例指北针的出版级地图",
           ["export_thematic_map"], kind="paraphrase"))
    add(_c("EV-P211", "做张人口专题图",
           ["create_thematic_map"], kind="paraphrase"))
    add(_c("EV-P212", "楼块3D拉伸效果图",
           ["create_3d_extrusion_map"], kind="paraphrase"))
    add(_c("EV-P213", "画个柱状统计图",
           ["generate_chart"], kind="paraphrase"))
    add(_c("EV-P214", "图层换个配色看看",
           ["update_layer_appearance"], kind="paraphrase"))
    add(_c("EV-P215", "给图层套个预设样式",
           ["apply_layer_style"], kind="paraphrase"))
    add(_c("EV-P216", "把这个图层显示出来",
           ["display_layer"], kind="paraphrase"))
    add(_c("EV-P217", "把临时图层删了",
           ["remove_layer"], kind="paraphrase"))
    add(_c("EV-P218", "图层起个别名好认",
           ["alias_layer"], kind="paraphrase"))
    add(_c("EV-P219", "图层顺序调一下",
           ["reorder_layer"], kind="paraphrase"))
    add(_c("EV-P220", "关掉底图图层的显隐",
           ["set_layer_status"], kind="paraphrase"))
    add(_c("EV-P221", "图层按属性过滤只显住宅",
           ["apply_layer_filter"], kind="paraphrase"))
    add(_c("EV-P222", "属性条件筛出人口超万的街道",
           ["attribute_filter"], kind="paraphrase"))
    add(_c("EV-P223", "地图上标个记",
           ["add_marker"], kind="paraphrase"))
    add(_c("EV-P224", "清空地图上的标注",
           ["clear_annotations"], kind="paraphrase"))
    add(_c("EV-P225", "底图切换成影像",
           ["switch_base_layer"], kind="paraphrase"))
    add(_c("EV-P226", "只调缩放俯仰旋转角度",
           ["set_map_view"], kind="paraphrase"))
    add(_c("EV-P227", "相机飞到西湖上空看看",
           ["fly_to_location"], kind="paraphrase"))
    add(_c("EV-P228", "缩放到这个矩形范围",
           ["zoom_to_bbox"], kind="paraphrase"))
    add(_c("EV-P229", "缩放到图层全幅",
           ["zoom_to_layer"], kind="paraphrase"))
    add(_c("EV-P230", "地图视角复位",
           ["reset_map_view"], kind="paraphrase"))
    add(_c("EV-P231", "拼个多专题组合图",
           ["combine_map_theme"], kind="paraphrase"))
    add(_c("EV-P232", "套用制图模板一键排版",
           ["apply_template"], kind="paraphrase"))
    add(_c("EV-P233", "有哪些制图模板可用",
           ["list_templates"], kind="paraphrase"))
    add(_c("EV-P234", "批量导出系列地图",
           ["export_batch_maps"], kind="paraphrase"))
    add(_c("EV-P235", "图层数据更新上架",
           ["webgis_layer_upsert"], kind="paraphrase"))
    add(_c("EV-P236", "多图拼合一张总图",
           ["webgis_map_combine"], kind="paraphrase"))
    # 方案 / 报告 / 工作流
    add(_c("EV-P237", "查方案执行到哪一步了",
           ["get_plan_status"], kind="paraphrase"))
    add(_c("EV-P238", "生成一份分析报告",
           ["generate_analysis_report"], kind="paraphrase"))
    add(_c("EV-P239", "方案存成工作流以后复用",
           ["save_plan_as_workflow"], kind="paraphrase"))
    add(_c("EV-P240", "按数据查询规划先查数",
           ["plan_data_query"], kind="paraphrase"))

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
