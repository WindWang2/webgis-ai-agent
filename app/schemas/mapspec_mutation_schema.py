"""User-origin MapSpec mutation 契约模型（V9 契约基石，ADR-0138）。

从 `app/api/routes/mapspec_mutations.py` 内联迁出（verbatim，行为不变）。
14 个 intent 模型组成 discriminated union，路由作为请求体。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from app.services.gis_harness.components import ComponentPlacement


class PatchLayerPresentationBody(BaseModel):
    intent: Literal["patch_layer_presentation"]
    expected_revision: int = Field(ge=0)
    layer_id: str = Field(min_length=1, max_length=200)
    visible: Optional[bool] = None
    opacity: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class PatchLayerStyleBody(BaseModel):
    """#1077：spec 承载层的持久样式突变（用户样式面板 durable 通道）。

    paint 顶层键合并（部分更新）；族谓词与 presentation patch 一致。
    """

    intent: Literal["patch_layer_style"]
    expected_revision: int = Field(ge=0)
    layer_id: str = Field(min_length=1, max_length=200)
    paint: dict[str, Any] = Field(min_length=1)


class PatchComponentBody(BaseModel):
    """组件局部突变（UI 拖拽/缩放/折叠收尾提交）。

    placement 结构在边界即校验（非法布局 422 而非事务回滚）；
    enabled 支持『隐藏组件』；collapse 走 placement.collapsed。
    """

    intent: Literal["patch_component"]
    expected_revision: int = Field(ge=0)
    component_id: str = Field(min_length=1, max_length=128)
    enabled: Optional[bool] = None
    position: Optional[str] = None
    placement: Optional[ComponentPlacement] = None
    variant: Optional[str] = Field(None, max_length=64)
    style: Optional[dict[str, Any]] = None
    options: Optional[dict[str, Any]] = None
    upsert: bool = False


class SetViewBody(BaseModel):
    intent: Literal["set_view"]
    expected_revision: int = Field(ge=0)
    center: Optional[list[float]] = None
    zoom: Optional[float] = None
    pitch: Optional[float] = None
    bearing: Optional[float] = None


class RemoveLayerBody(BaseModel):
    intent: Literal["remove_layer"]
    expected_revision: int = Field(ge=0)
    layer_id: str = Field(min_length=1, max_length=200)


class RemoveComponentBody(BaseModel):
    """Component Lifecycle V3（Runtime V4 §18）：组件真删除（用户侧入口）。"""

    intent: Literal["remove_component"]
    expected_revision: int = Field(ge=0)
    component_id: str = Field(min_length=1, max_length=128)


class DuplicateComponentBody(BaseModel):
    """Component Lifecycle V3（§19）：复制多实例组件。"""

    intent: Literal["duplicate_component"]
    expected_revision: int = Field(ge=0)
    component_id: str = Field(min_length=1, max_length=128)
    new_id: Optional[str] = Field(None, min_length=1, max_length=128)


class RebindComponentBody(BaseModel):
    """Component Lifecycle V3（§19）：重绑定（chartRef/tableRef/layerId）。

    绑定字段在纯函数层按类型白名单校验；目标存在性由引擎事务内的
    pre-commit 守卫复核（layer 在场 / artifact ref 活性）。
    """

    intent: Literal["rebind_component"]
    expected_revision: int = Field(ge=0)
    component_id: str = Field(min_length=1, max_length=128)
    chart_ref: Optional[str] = Field(None, min_length=1, max_length=200)
    table_ref: Optional[str] = Field(None, min_length=1, max_length=200)
    layer_id: Optional[str] = Field(None, min_length=1, max_length=200)


class ReorderLayersBody(BaseModel):
    intent: Literal["reorder_layers"]
    expected_revision: int = Field(ge=0)
    layer_ids: list[str] = Field(min_length=1, max_length=128)


class SetLayoutBody(BaseModel):
    intent: Literal["set_layout"]
    expected_revision: int = Field(ge=0)
    legend: Optional[dict[str, Any]] = None
    controls: Optional[list[dict[str, Any]]] = None
    margins: Optional[dict[str, Any]] = None
    # CartographyComponent 列表（id/type/enabled/position/priority/style/options）
    components: Optional[list[dict[str, Any]]] = None


class SetTimeBody(BaseModel):
    intent: Literal["set_time"]
    expected_revision: int = Field(ge=0)
    enabled: Optional[bool] = None
    field: Optional[str] = None
    type: Optional[str] = None
    extent: Optional[list[Any]] = None
    current: Optional[Any] = None
    window: Optional[Any] = None
    playback: Optional[dict[str, Any]] = None
    step: Optional[float] = None
    speed: Optional[float] = None


class InitProjectBody(BaseModel):
    intent: Literal["init_project"]
    expected_revision: int = Field(ge=0)
    view: Optional[dict[str, Any]] = None


class SetWorkbenchStateBody(BaseModel):
    """Workbench V5 组织态持久化（分组树/成员/锁/模式；引擎内结构+256KB 校验）。

    V6：base_workbench_revision —— workbench 级 CAS（引擎比对存储 doc 的
    `_rev` 盖章）；提供且不一致 → 409 superseded（回灌当前 doc）。缺省 =
    V5 语义。
    """

    intent: Literal["patch_workbench_state"]
    expected_revision: int = Field(ge=0)
    doc: dict[str, Any]
    base_workbench_revision: Optional[int] = Field(default=None, ge=0)


class PatchWorkbenchDeltaBody(BaseModel):
    """Workbench V6 组织态**增量**补丁（绝对值语义；引擎内管线应用+全量校验）。

    见 app/services/collab/delta.py：setGroups（部分字段 create/patch）/
    removeGroupIds（级联）/ membershipSet / membershipClear / locksAdd /
    locksRemove。mode 不在 delta 域。patch ≤64KB、各列表 ≤2000。
    """

    intent: Literal["patch_workbench_delta"]
    expected_revision: int = Field(ge=0)
    delta: dict[str, Any]


UserMapSpecMutationRequest = Annotated[
    Union[
        PatchLayerPresentationBody,
        PatchLayerStyleBody,
        PatchComponentBody,
        SetViewBody,
        RemoveLayerBody,
        RemoveComponentBody,
        DuplicateComponentBody,
        RebindComponentBody,
        ReorderLayersBody,
        SetLayoutBody,
        SetTimeBody,
        InitProjectBody,
        SetWorkbenchStateBody,
        PatchWorkbenchDeltaBody,
    ],
    Field(discriminator="intent"),
]


#: 历史别名（V9 前路由内定义的名字保持不变）
MapSpecMutationUnion = UserMapSpecMutationRequest


# ── 响应模型（P2 新增）────────────────────────────────────────────────


class MutationApplyResponse(BaseModel):
    """POST /chat/sessions/{session_id}/mapspec/mutations 响应。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"session_id": "sess-123", "revision": 7}]
        }
    )

    session_id: str
    revision: int


class WorkbenchStateResponse(BaseModel):
    """GET /chat/sessions/{session_id}/workbench/state 响应。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"session_id": "sess-123", "revision": 7, "doc": {}}]
        },
        extra="allow",
    )

    session_id: str
    revision: int
    doc: Optional[dict[str, Any]] = None


class WorkbenchArtifactStatusResponse(BaseModel):
    """GET /chat/sessions/{session_id}/workbench/artifact-status 响应。

    派生投影（V6 Must-have H，零新真相），形状由
    app/services/collab/artifact_status.py 决定 —— 开放对象。
    """

    model_config = ConfigDict(extra="allow")
