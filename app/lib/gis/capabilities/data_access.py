"""数据获取 能力包（ADR-0099 §34 domain packs）。

描述符逐字迁自 capability_registry._SEED_CAPS（2026-09 split）。
新能力在各自域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="poi_query", name="POI 要素获取", category="data_access",
            description="按范围/类别获取点要素（本地优先，在线兜底）。",
            output_artifact_types=["poi_feature_set"],
            geometry_requirements=["point"],
            preferred_execution="local_first",
            compatible_map_models=["visual_heatmap", "point_overlay", "simple_point_map",
                                   "proportional_symbol", "categorical_thematic"],
            purpose_template="{subject} 要素获取",
            version="1.0",
        ),

        CapabilityDescriptor(
            id="admin_boundary_query", name="行政区边界获取", category="data_access",
            description="获取行政区边界面（本地 SHP 优先）。",
            # V3：算法面 admin.boundary_lookup（buffer/裁剪变体）输出一般
            # 多边形要素集，与 admin_boundary_set 并列为合法输出。
            output_artifact_types=["admin_boundary_set", "polygon_feature_set"],
            geometry_requirements=["polygon"],
            compatible_map_models=["administrative_aggregation"],
            purpose_template="行政边界/区划面获取",
        ),

        # ── 数据控制平面（Data Control Plane V4 / GeoCompute V5）──────────
        # 会话数据资产摄取/目录/联邦查询/工作空间快照能力面（工具直连，
        # 算法面无对应实现者 —— 与 poi_query 同为数据获取类能力）。

        CapabilityDescriptor(
            id="dataset_ingest", name="数据集摄入", category="data_access",
            description="内联 GeoJSON FeatureCollection 摄入会话：指纹去重、"
                        "有界画像、质量诊断、产物登记（有界返回，不含数据本体）。",
            output_artifact_types=["feature_collection"],
            preferred_execution="async",
            purpose_template="{subject} 数据集摄入",
        ),

        CapabilityDescriptor(
            id="federated_dataset_query", name="联邦数据集查询", category="data_access",
            description="N 源（2..4）有界左深链式联邦查询：属性/空间连接与"
                        "聚合逐跳串联，成本排序、最小投影、半连接约减、"
                        "逐跳预算 fail-fast。",
            output_artifact_types=["stats_table"],
            preferred_execution="celery",
            purpose_template="{subject} 联邦查询",
        ),

        CapabilityDescriptor(
            id="workspace_state_inspection", name="工作空间状态检视",
            category="data_access",
            description="只读检视会话工作空间（产物账本、图层引用、快照清单）。",
            preferred_execution="local_first",
            deterministic=True,
            purpose_template="{subject} 工作空间检视",
        ),

        CapabilityDescriptor(
            id="workspace_snapshot", name="工作空间快照", category="data_access",
            description="工作空间快照保存/恢复：产物账本 + 图层引用 + 视图设置，"
                        "可选物化存活载荷到持久内容库。",
            preferred_execution="async",
            deterministic=False,
            purpose_template="{subject} 工作空间快照",
        ),
]
