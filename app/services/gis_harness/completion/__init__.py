"""GIS Map Product Completion Runtime — ADR-0081 / ADR-0091（Runtime V4 §33 包拆分）。

确定性回答一个问题：**最终地图产品是否真的完成了？**

在此之前，系统的"完成"信号止步于行状态：所有能力行 complete（DAG
complete）即被认为任务可以结束（turn 在 ``agent_settled`` 收尾，对地图
产品零检查）。本包把「DAG 完成」与「地图成品完成」拆开：

    mandatory DAG complete
            ↓
    Map Product Finalizer（本包）
            ↓  artifact / layer / viewport / component / layout validators
    bounded repair（≤ MAX_FINALIZATION_PASSES 轮，只做确定性 desired-state 修复）
            ↓
    PASS → gis_chapter["map_product"] 披露 + 投影一行

边界（刻意收窄，全部为派生运行时逻辑）：
- 不 fork Pi、不建第二 agent loop —— 触发点在 harness（bridge 工具结果
  后 / turn settle），Pi 只看投影里的一行有界披露；
- 不新建第二 MapSpec / SessionPlan / runtime-layer truth —— 只读既有
  真相（章节扁平行 / MapSpec / session artifact descriptors），结果写回
  章节的 additive ``map_product`` 键；
- repair 绝不重跑 GIS 算法 —— 需要重新执行的发现以 ``needs_execution``
  披露，交还 DAG/重试语义裁决；
- 用户显式决策优先（user-wins）：结果层的显示修复走 GISMutationBatch
  的既有 owner 守卫，被拒即如实上报。

性能契约：普通地图 validation 毫秒级 —— 图层/组件校验 O(N)、布局校验
O(C²)（C = chrome 组件数，个位数量级）、bbox 全部来自既有 ref descriptor
元数据，不复制 GeoJSON、不逐 feature 扫描。

包拆分（原 ``app/services.gis_harness.map_completion`` 单体模块，见
ADR-0091 / Runtime V4 §33）：

- ``contracts``：契约常量 + finding/result dataclasses + 共享图层投影 helper；
- ``inputs``：输入聚合（一次读齐，validators 全部纯函数）；
- ``validators``：per-facet 校验器（execution / artifacts / layers /
  components / semantics / layout / viewport_export）；
- ``repairs``：确定性 desired-state 修复（``apply_repairs``，旧名
  ``_apply_repairs`` 仍是同一函数对象的别名）；
- ``pipeline``：编排（validate → repair → revalidate，≤ MAX_FINALIZATION_PASSES）
  与章节持久化 / SSE 披露。

本 ``__init__`` 镜像旧模块的完整顶层 API（public + 常量）；旧导入路径
``app.services.gis_harness.map_completion`` 由 compat shim 继续承接。
"""
from __future__ import annotations

from .contracts import (
    F_ARTIFACT_EXPIRED,
    F_ARTIFACT_MISSING,
    F_COMPONENT_DISABLED,
    F_COMPONENT_MISSING,
    F_CRS_NOT_WGS84,
    F_EMPTY_RESULT,
    F_EXECUTION_BLOCKED,
    F_LAYER_HIDDEN,
    F_LAYER_MISSING,
    F_LAYOUT_CONFLICT,
    F_NEEDS_EXECUTION,
    F_NO_RESULT_LAYER,
    F_ORPHAN_BINDING,
    F_RENDER_COMPONENT_MISSING,
    F_RENDER_ERROR,
    F_RENDER_LAYER_MISSING,
    F_RENDER_REVISION_STALE,
    F_RENDER_SOURCE_MISSING,
    F_RENDER_UNVERIFIED,
    F_SEMANTIC_LEGEND_MISSING,
    F_SEMANTIC_LEGEND_MISMATCH,
    F_SOURCE_MISSING,
    F_TITLE_MISSING_REPORT,
    F_VIEWPORT_NO_BBOX,
    MAX_DISCLOSED_REPAIRS,
    MAX_FINALIZATION_PASSES,
    MAX_FINDING_DETAIL,
    MAX_FINDINGS,
    MAX_REPAIR_MEMORY,
    R_ADD_COMPONENT,
    R_ENABLE_COMPONENT,
    R_SHOW_LAYER,
    RENDER_ISSUES,
    RENDER_NOT_APPLICABLE,
    RENDER_STALE,
    RENDER_UNKNOWN,
    RENDER_VERIFIED,
    RESULT_LAYER_ROLES,
    RUNTIME_RENDER_CODES,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_NEEDS_REPAIR,
    STATUS_PENDING,
    MapCompletionFinding,
    MapCompletionResult,
    _COMPONENT_DEFAULT_IDS,
    _SINGLETON_TYPES,
)
from .inputs import gather_completion_inputs
from .repairs import _apply_repairs, apply_repairs
from .validators import (
    assess_export_parity,
    derive_result_bbox,
    validate_artifacts,
    validate_components,
    validate_execution,
    validate_layers,
    validate_layout,
    validate_semantics,
)
from .pipeline import (
    current_mapspec_for_disclosure,
    finalization_sse_payload,
    map_product_block,
    maybe_finalize_map_product,
    read_stored_map_product,
    run_map_finalization,
)

__all__ = [
    # constants
    "MAX_FINALIZATION_PASSES",
    "MAX_FINDINGS",
    "MAX_FINDING_DETAIL",
    "MAX_DISCLOSED_REPAIRS",
    "MAX_REPAIR_MEMORY",
    "RESULT_LAYER_ROLES",
    "STATUS_PENDING",
    "STATUS_NEEDS_REPAIR",
    "STATUS_COMPLETE",
    "STATUS_FAILED",
    "F_NEEDS_EXECUTION",
    "F_EXECUTION_BLOCKED",
    "F_ARTIFACT_MISSING",
    "F_ARTIFACT_EXPIRED",
    "F_EMPTY_RESULT",
    "F_NO_RESULT_LAYER",
    "F_LAYER_MISSING",
    "F_SOURCE_MISSING",
    "F_LAYER_HIDDEN",
    "F_COMPONENT_MISSING",
    "F_COMPONENT_DISABLED",
    "F_LAYOUT_CONFLICT",
    "F_ORPHAN_BINDING",
    "F_VIEWPORT_NO_BBOX",
    "F_RENDER_UNVERIFIED",
    "F_RENDER_REVISION_STALE",
    "F_RENDER_LAYER_MISSING",
    "F_RENDER_SOURCE_MISSING",
    "F_RENDER_COMPONENT_MISSING",
    "F_RENDER_ERROR",
    "F_SEMANTIC_LEGEND_MISSING",
    "F_SEMANTIC_LEGEND_MISMATCH",
    "F_TITLE_MISSING_REPORT",
    "F_CRS_NOT_WGS84",
    "RUNTIME_RENDER_CODES",
    "RENDER_VERIFIED",
    "RENDER_ISSUES",
    "RENDER_STALE",
    "RENDER_UNKNOWN",
    "RENDER_NOT_APPLICABLE",
    "R_ADD_COMPONENT",
    "R_ENABLE_COMPONENT",
    "R_SHOW_LAYER",
    "_COMPONENT_DEFAULT_IDS",
    "_SINGLETON_TYPES",
    # dataclasses
    "MapCompletionFinding",
    "MapCompletionResult",
    # inputs / validators
    "gather_completion_inputs",
    "validate_execution",
    "validate_artifacts",
    "validate_layers",
    "validate_components",
    "validate_semantics",
    "validate_layout",
    "derive_result_bbox",
    "assess_export_parity",
    # repairs
    "apply_repairs",
    "_apply_repairs",
    # pipeline / orchestration
    "run_map_finalization",
    "maybe_finalize_map_product",
    "read_stored_map_product",
    "finalization_sse_payload",
    "current_mapspec_for_disclosure",
    "map_product_block",
]
