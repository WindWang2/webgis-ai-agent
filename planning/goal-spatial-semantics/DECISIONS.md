# DECISIONS — GOAL 01

D1 契约定位：实例层（数据×操作×执行），组合不重叠 —— MethodUseContract（算法静态目录 v1，不改）、
measurement.py（字段级画像，不改）、crs_safety.py（CRS 分类，不改）、spatial_receipt（inspect 收据，不改）。
SpatialSemanticContract 只持有运行时绑定证据（working_crs/换算链/分母/归一事实）。
D2 词表纪律：crs_source 四态复用 data_preparation（declared/source/assumed/rfc7946_default）+ unknown；
单位词表引用 measurement.CANONICAL_UNITS，不建第二注册表；理由码 SSC_* 封闭。
D3 无 CRS 缺省：度样坐标（|v|≤180/90 且含非整数）→ rfc7946_default 披露放行（规范缺省非猜测）；
非度样（投影样/整数网格/越界）→ blocked CRS_UNDECLARED_NON_DEGREE + declare_crs 修复。
D4 修复建议分级：explicit_authorization_required（默认）vs deterministic_safe_transform（EPSG↔EPSG
pyproj 可逆 + 轴序确定，仅当双方显式声明时自动执行）。
D5 未声明 GCJ 风险：不自动归一（潜在危险转换）；AOI⊆中国 bbox 且未显式声明帧 → 非阻塞
CHINA_OFFSET_FRAME_RISK 警示。
D6 3857 已投影输入：不再静默直接度量 —— degraded_with_disclosure（失真估计 1/cos(lat)）+
建议确定性安全 transform 到 UTM。
D7 preflight 纯函数 + descriptor 级：坐标采样 ≤200 点判结构，全量几何绝不进契约/prompt。
D8 工具适配不改签名：适配层在工具入口调用 build_spatial_preflight，blocked → 既有 typed error 通道；
pass/degraded → 证据并入结果 evidence dict（spatial_semantics 键）。
