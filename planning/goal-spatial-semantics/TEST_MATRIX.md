# TEST_MATRIX — 12+ 端到端场景 失败/预期矩阵（M0 冻结）

每行：场景 | 输入 | 当前 master 行为 | 本任务预期（preflight 判定 + 理由码）。
判定词表：pass / pass_with_disclosure / degraded_with_disclosure /
requires_clarification / blocked。理由码词表见 vocabulary.py（SSC_*）。

| # | 场景 | 输入要点 | master@6c5e71c2c 现状 | 预期（新） |
|---|---|---|---|---|
| S1 | 成都小学 500m 缓冲（声明 4326） | 度坐标 + crs member | 可跑，evidence 有 working_crs | pass；契约记 source_crs=EPSG:4326(declared)、working_crs=EPSG:32648、distance 500m 换算链、geodesic_or_projected=projected_local |
| S2 | 同上但无 crs member（度样坐标） | 无声明、坐标度样（含小数、|v|≤180/≤90） | 静默按 4326（仅越界才 warn） | pass_with_disclosure：crs_source=rfc7946_default + 理由码 CRS_RFC7946_DEFAULT（规范缺省，非猜测） |
| S3 | 无 crs member 且坐标为投影米（成都 UTM x≈5e5,y≈3.6e6） | 无声明、非度样 | **warn 后静默当 4326**（G 通路） | **blocked**：CRS_UNDECLARED_NON_DEGREE + 修复建议 declare_crs（显式授权才继续）；工具返回 typed error 不改写成功 |
| S4 | 声明 EPSG:3857（成都纬度）缓冲 500m | 已投影非局部度量 | 直接在 3857 米上缓冲（失真 ~1.57×） | degraded_with_disclosure：WEB_MERCATOR_SCALE_DISTORTION + 失真估计；确定性安全路径（3857→UTM，双方 EPSG）建议 transform，需显式授权执行 |
| S5 | distance=0.5 + unit=km（=500m） | 单位归一 | 归一靠工具自身 | pass_with_disclosure：换算链 distance=0.5km→500m 显式记入契约 |
| S6 | 各区每 km² 小学密度 | polygon 分母 + count | rate_unit=per_m2（正确但口径未升格） | pass：stat_denominator=area、denominator_unit=km²、density_unit=所/km² 进 legend_spec.unit 与 MapSpec 证据；零面积区 rate=None 披露 |
| S7 | 密度请求 per_km² 但分母绑人口字段 | 量纲错配 | 字段级闸有 UNIT_DIMENSION_MISMATCH（规划期） | 执行前统一判定：DE nominator_KIND_MISMATCH（denominator=population ≠ 请求 per_area）+ 修复建议二选一 |
| S8 | BD-09 声明点位 × WGS84 边界 join | 双层异帧声明 | 归一正确但证据只进 log | pass_with_disclosure：契约记 datum_normalization=[bd09→gcj02→wgs84]，join 结果 evidence 带归一事实 |
| S9 | 未声明（实为 GCJ-02）点位 × WGS84 边界 join（中国范围） | 偏移帧未声明 | 无检测，join 谓词静默错配 | pass_with_disclosure→CHINA_OFFSET_FRAME_RISK 非阻塞警示（AOI⊆中国 bbox 且 crs 未显式声明为 wgs84/gcj02/bd09）；绝不自动归一未声明数据 |
| S10 | 斐济跨 180° 范围缓冲 | bbox 跨 AM | 选带 rewrap（core.py:519）但无 typed 记账 | pass_with_disclosure：ANTIMERIDIAN_CROSSED + working_crs 选带披露 + 与测地基准的面积/半径对照断言 |
| S11 | AOI 声明 4326 但数据声明 3857（范围矛盾） | scope CRS ≠ data CRS | 无对账（AOI 无 CRS 载体） | requires_clarification：SCOPE_DATA_CRS_MISMATCH + 修复建议（声明其一或 transform） |
| S12 | zonal_stats：AOI 空 bbox / min>max / 乱序 | 无效范围 | 部分下游才炸 | blocked：INVALID_SCOPE（前置、确定性） |
| S13 | 高纬 >84°N（斯瓦尔巴）度量请求 | 极区 | recommend_metric_crs 已给 3413 | pass：working_crs=EPSG:3413、geodesic_or_projected=projected_polar 披露 |
| S14 | KDE density（bandwidth_m=1000） | 隐式 per_m² 分母 | 无单位标注（G6） | pass_with_disclosure：DENSITY_UNIT_IMPLICIT + 输出 density_unit=per_km²（换算链）进契约 |
| S15 | voronoi/convex_hull 面积输出（非米投影输入） | 输入已投影英尺系 | area/1e6 硬编码（错 10.76×） | 修复：输出单位按 crs_unit_factor 换算 + AREA_UNIT_CONVERSION 披露（G3） |
| S16 | 50k feature 输入 preflight 开销 | 大数据 | 无 preflight | 契约只评估 descriptor 级（crs/bbox/有界坐标采样 ≤200），无全量几何进 prompt；性能预算对照（M7） |

8 类典型错误映射（验收 Oracle #3）：4326vs投影=S3/S4；mvs度=S3/S5/S12；
km²vscount=S6/S7；轴序=S2（axis_order_guessed 进契约，对 G9 留
AXIS_ORDER_SUSPECT 码）；跨180°=S10；多带=S4/S10（失真披露）；GCJ/BD=S8/S9；
范围矛盾/无效=S11/S12。
