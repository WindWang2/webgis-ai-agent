"""平台能力包（Quality W2b）—— GIS 分析域之外的工具面能力。

背景：capability 词表此前只覆盖 GIS 分析域（buffer/kriging/hotspot…）
与少数 data-access 域；地图视口、图层显示、制图导出、地理编码、
计划编排等 **平台工具面** 在能力语义上无归位 —— 62 个工具的
capabilities 只能留空。

本包把这些域登记为 **status="planned"** 的能力：工具面真实存在
（每个能力的 tool 候选都是已注册工具，绑定契约由 conformance 测试
钉住），但算法级 producer 契约（parameter_contract / 科学元数据 /
分析语义）尚未建立 —— planned 诚实反映这一中间态，且规划/解析器
（resolver 对非 native 能力一律 unavailable）不会把 planned 能力当
可执行分析派发。

新能力在各自域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor


def _cap(cap_id: str, name: str, description: str, *,
         deterministic: bool = True) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=cap_id, name=name, category="platform", domain="platform",
        description=description, status="planned", deterministic=deterministic,
    )


CAPABILITIES: List[CapabilityDescriptor] = [
    _cap(
        "map_viewport_control", "地图视口控制",
        "地图相机/视口控制：坐标飞行定位、bbox/图层缩放适配、zoom/pitch/bearing "
        "调整与全国视图复位（含 MapSpec 视图参数）。",
    ),
    _cap(
        "layer_display_control", "图层显示控制",
        "会话图层的显示生命周期：显隐/置顶排序/别名/清单盘点、基础图切换、"
        "外观样式提示、按属性条件动态过滤与收尾显示收口。",
    ),
    _cap(
        "map_annotation_measurement", "地图标注与量测",
        "地图交互标注与量测：pin 标注增删、球面距离/面积量算（Haversine/球面多边形公式）、"
        "坐标位置要素探查。",
    ),
    _cap(
        "thematic_cartography", "专题制图与样式",
        "专题制图工具面：分层设色/3D 挤出专题图、单色样式注入、制图模板套用与组合、"
        "MapSpec 图层/版面/组件的编写与局部突变、浮动图表控制。",
    ),
    _cap(
        "map_export_publishing", "地图导出发布",
        "制图产出：PNG/PDF/SVG 单张与批量导出、MapSpec 编译为 style.json/index.html、"
        "编译前规范校验、headless 运行时验收与 desired↔runtime 收敛状态查询。",
    ),
    _cap(
        "geocoding", "地理编码",
        "地址/地名 ↔ 坐标互转：在线地理编码与逆地理编码（Nominatim / 高德 / 百度 / 天地图），"
        "单点与批量形态。",
        deterministic=False,
    ),
    _cap(
        "local_data_query", "本地数据目录与查询",
        "本地化数据资产的目录检视与要素/统计查询：本地 OSM 主题要素（道路/建筑/水系等）、"
        "统计年鉴与县域面板指标、高德 POI 库可用性总览。",
    ),
    _cap(
        "data_source_pipeline", "数据源管道",
        "外部数据源连接与查询管道：连接注册（SSRF 防护）、健康诊断、目录检索、"
        "下推查询/聚合、查询计划 explain、物化入会话、元数据刷新与分析资产维护。",
    ),
    _cap(
        "plan_workflow_orchestration", "计划与工作流编排",
        "多步分析计划与持久工作流的编排面：计划提议/执行/状态查询、执行计划校验与"
        "波次执行 run 管理（取消/证据查询）、计划固化与工作流重跑。",
    ),
    _cap(
        "scenario_simulation", "情景推演",
        "基于真实空间数据与规则库的情景推演面：what-if 指标影响模拟、数据驱动的空间决策"
        "评估与多方案对比、规划规则的可解释推理。",
        deterministic=False,
    ),
    _cap(
        "meta_tool_surface", "元工具面",
        "Agent 自省与扩展面：工具清单查询、子代理委派、技能脚本开发部署与注册表刷新、"
        "外部数据深度探索、公网检索。",
        deterministic=False,
    ),
    _cap(
        "report_charting", "报告与图表",
        "会话产出的报告与可视化：统计图表生成（可附着地图浮动面板）、PDF/HTML/Markdown "
        "分析报告、自然资源监测标准化报告。",
    ),
    _cap(
        "dataset_profiling_quality", "数据画像与质量",
        "会话数据的画像与质量面：行数/几何/字段画像、字段语义角色推断、质量审计与"
        "安全修复（非破坏、新 ref 落账）、分析模式匹配与前置条件建议。",
    ),
    _cap(
        "crs_transformation", "坐标系转换",
        "几何坐标系转换：通用 EPSG 重投影（GeoJSON 图层）与中国坐标偏移互转"
        "（WGS84↔GCJ-02↔BD-09）。",
    ),
    _cap(
        "temporal_filtering", "时间筛选",
        "按时间点/区间/相对窗口（如『最近 7 天』）对 GIS 数据做精准时间筛选"
        "（过滤，非聚合/趋势）。",
    ),
    _cap(
        "directional_distribution_analysis", "方向分布分析",
        "探索性空间统计的方向分布度量：标准离差椭圆（SDE）刻画要素集合的"
        "中心趋势、离散度与方向性（旋转角/长短轴/扁率）。",
    ),
]
