"""工作台生命周期能力包（G22 接入补丁；域模块注册 —— 勿回填中央文件）。

补齐 G10/G16/G17/G19 工具面**已声明**（tool descriptor `capabilities=[...]`）
但漏注册进 CapabilityRegistry 的能力 id —— 2026-11-11 合入批次后
`validate_runtime_manifest_strict` 在启动期对 dangling capability id
fail-fast，整个 app 无法 boot。本包按 G15 frame_product 同款惯例登记
（platform 控制面、status=planned 表示「非分析 producer」，工具面真实
存在）；owner 方向（G10 refresh / G16 labels / G17 import / G19 field
compute）后续可按各自域语义迁移或扩充 —— id 与工具绑定关系为事实，
描述以对应工具 descriptor 为准。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [
    # ── G19：类型化字段计算 ─────────────────────────────────────────────
    CapabilityDescriptor(
        id="field_compute", name="类型化字段计算",
        category="platform", status="planned",
        description=(
            "安全表达式类型化字段计算：函数目录、预览与完整派生、成果回读"
            "（field_compute_catalog/preview/apply/result 工具）。"),
    ),
    # ── G17：staged 数据导入 ──────────────────────────────────────────
    CapabilityDescriptor(
        id="dataset_preparation", name="导入计划与批次预览",
        category="platform", status="planned",
        description=(
            "staged 多文件导入的计划配置与批次预览（import_plan_configure/"
            "import_batch_preview）。"),
    ),
    CapabilityDescriptor(
        id="dataset_ingestion", name="导入批次提交",
        category="platform", status="planned",
        description="staged 导入批次提交为多容器图层与表格（import_batch_commit）。",
    ),
    # ── G10：项目数据刷新 ──────────────────────────────────────────────
    CapabilityDescriptor(
        id="project_source_version_inventory", name="项目源版本盘点",
        category="platform", status="planned",
        description="检查项目数据源版本变化（webgis_check_project_source_updates）。",
    ),
    CapabilityDescriptor(
        id="refresh_impact_explanation", name="过期地图解释",
        category="platform", status="planned",
        description="解释地图产品相对新数据版本的过期原因（webgis_explain_stale_map_product）。",
    ),
    CapabilityDescriptor(
        id="refresh_impact_preview", name="刷新影响预览",
        category="platform", status="planned",
        description="增量刷新影响预览（webgis_preview_project_data_refresh）。",
    ),
    CapabilityDescriptor(
        id="refresh_candidate_execution", name="刷新候选执行",
        category="platform", status="planned",
        description="隔离执行刷新候选（webgis_execute_project_data_refresh）。",
    ),
    CapabilityDescriptor(
        id="refresh_atomic_adoption", name="刷新原子采纳",
        category="platform", status="planned",
        description="原子采纳刷新候选（webgis_adopt_project_data_refresh）。",
    ),
    CapabilityDescriptor(
        id="refresh_candidate_discard", name="刷新候选丢弃",
        category="platform", status="planned",
        description="丢弃刷新候选（webgis_discard_project_data_refresh）。",
    ),
    CapabilityDescriptor(
        id="refresh_version_rollback", name="刷新版本回退",
        category="platform", status="planned",
        description="回退到采纳前数据版本（webgis_rollback_project_data_refresh）。",
    ),
    CapabilityDescriptor(
        id="refresh_status_query", name="刷新状态查询",
        category="platform", status="planned",
        description="项目刷新状态查询（webgis_project_refresh_status）。",
    ),
    # ── G16：要素级标注人工控制 ────────────────────────────────────────
    CapabilityDescriptor(
        id="map_label_authoring", name="要素级标注人工控制",
        category="platform", status="planned",
        description=(
            "要素级标注覆写/固定/移位/文字/恢复自动的检视与授权"
            "（webgis_label_inspect/webgis_label_author）。"),
    ),
]
