"""网络分析 能力包（ADR-0099 §34 domain packs）。

描述符逐字迁自 capability_registry._SEED_CAPS（2026-09 split）。
新能力在各自域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="proximity_buffer", name="邻近缓冲", category="analysis",
            description="距离缓冲区生成。",
            input_artifact_types=["poi_feature_set", "point_feature_set",
                                  "line_feature_set", "polygon_feature_set"],
            output_artifact_types=["proximity_zone"],
            compatible_map_models=["proximity_overlay"],
            purpose_template="邻近缓冲",
        ),

        CapabilityDescriptor(
            id="service_area", name="网络服务区", category="network",
            domain="network",
            description="等时圈/网络可达服务区。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["service_area"],
            compatible_map_models=["proximity_overlay"],
            purpose_template="网络服务区",
        ),

        CapabilityDescriptor(
            id="shortest_path", name="最短路径", category="network",
            domain="network", description="网络最短路径。",
            output_artifact_types=["line_feature_set"],
            purpose_template="最短路径",
        ),

        CapabilityDescriptor(
            id="closest_facility", name="最近设施", category="network",
            domain="network", description="从需求点到设施集合的 top-K 最近路径。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["line_feature_set"],
            purpose_template="最近设施分析",
        ),

        CapabilityDescriptor(
            id="accessibility", name="网络可达性", category="network",
            domain="network", description="需求点对设施集合的可达性指标计算（15 分钟生活圈等）。",
            input_artifact_types=["point_feature_set"],
            output_artifact_types=["service_area", "stats_table"],
            compatible_map_models=["proximity_overlay"],
            purpose_template="网络可达性分析",
        ),

        CapabilityDescriptor(
            id="od_matrix", name="OD 成本矩阵", category="network",
            domain="network", description="多起点×终点网络成本矩阵。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["od_matrix"],
            compatible_map_models=["flow_od_arc"],
            purpose_template="起讫点（OD）矩阵",
        ),

        CapabilityDescriptor(
            # ADR-0092 D：OD 边 → 带权流向线要素（flow_od_arc 渲染输入）。
            id="od_flow_mapping", name="OD 流向图", category="network",
            domain="network", description="把 OD 对（坐标+权重）构建为有界流向线要素层。",
            input_artifact_types=["od_matrix", "od_table", "line_feature_set"],
            output_artifact_types=["line_feature_set"],
            compatible_map_models=["flow_od_arc"],
            purpose_template="OD 流向表达",
        ),

        CapabilityDescriptor(
            id="location_allocation", name="区位配置", category="network",
            domain="network", description="设施选址-分配优化（tier-3 门控）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["point_feature_set", "stats_table"],
            purpose_template="区位配置优化",
        ),

        CapabilityDescriptor(
            id="route_optimization", name="路线优化", category="network",
            domain="network", description="多站点访问顺序优化（VRP，tier-3 门控）。",
            input_artifact_types=["point_feature_set"],
            output_artifact_types=["line_feature_set"],
            purpose_template="访问路线优化",
        ),

        # ── VNext（ADR-0099）：外部服务商网络服务绑定（消除 network_tool_orphan）──
        # 这三个能力是外部 API 客户端语义（高德/百度），不是本地路网分析：
        # deterministic=False（服务商侧实时数据，逐次调用不可复现），
        # 外部依赖（API Key / 配额）在算法 limitations 中披露。
        CapabilityDescriptor(
            id="external_route_planning", name="外部路径规划", category="network",
            domain="network",
            description="经外部服务商（高德/百度）API 的点对点路径规划（驾车/步行/骑行/公交），"
                        "返回距离、耗时与路线坐标；依赖服务商 API Key。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["line_feature_set"],
            deterministic=False,
            purpose_template="外部路径规划",
        ),

        CapabilityDescriptor(
            id="transit_routing", name="公交路径规划", category="network",
            domain="network",
            description="起终点间公交/地铁换乘方案查询（步行段+乘车段，含换乘次数、总耗时、票价；"
                        "外部服务商 API，当前仅高德）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["line_feature_set"],
            deterministic=False,
            purpose_template="公交换乘方案查询",
        ),

        CapabilityDescriptor(
            id="traffic_status", name="实时路况", category="network",
            domain="network",
            description="指定矩形/圆形范围内的实时道路拥堵状态查询（拥堵等级+路段长度；"
                        "外部服务商 API，实时语义、结果不缓存）。",
            output_artifact_types=["stats_table"],
            deterministic=False,
            purpose_template="实时路况查询",
        ),
]
