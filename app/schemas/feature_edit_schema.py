"""Feature-edit REST 契约（/goal 03）。extra=forbid —— 未知键显式拒绝。"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EditOpModel(BaseModel):
    """编辑操作（discriminated by `op`；词表见 feature_edit.contracts.EDIT_OPS）。"""

    op: Literal[
        "set_fields", "rename_field", "repair_geometry", "move_by", "snap_to_ref",
        # G14 结构性操作
        "create_features", "delete_features", "replace_geometry", "split_feature", "merge_features",
        # 共享拓扑编辑（goal-gis-topology-authoring / ADR-0300）
        "move_shared_vertex", "insert_shared_vertex", "delete_shared_vertex",
        "reshape_shared_edge", "connect_endpoint",
    ]
    fields: Optional[Dict[str, Any]] = None           # set_fields
    fixes: Optional[List[str]] = None                 # repair_geometry
    dx_m: Optional[float] = None                      # move_by（米，必填）
    dy_m: Optional[float] = None                      # move_by（米，必填）
    ref_id: Optional[str] = None                      # snap_to_ref（skip 解引用名单键）
    tolerance_m: Optional[float] = None               # snap_to_ref / 共享 op 容差（米）
    # rename_field 的 from/to（`from` 是 Python 保留字 → 别名；`to` 与共享 op
    # 的目标坐标键冲突 → validator 按 op 分流，见 _route_from_to）。
    from_field: Optional[str] = None
    to_field: Optional[str] = None
    # ── G14 结构性操作参数 ──
    features: Optional[List[Dict[str, Any]]] = None   # create_features：[{geometry, properties?}]
    geometry: Optional[Dict[str, Any]] = None         # replace_geometry：GeoJSON 几何
    wfid: Optional[str] = None                        # split_feature：目标稳定身份
    line: Optional[Dict[str, Any]] = None             # split_feature：分割线几何
    field_policy: Optional[Dict[str, Any]] = None     # merge_features：{mode, overrides}
    # G32：op 级目标限定 —— 带 wfids 的 plan 级 op 只作用于这些要素（自然 id
    # 或 WFID 均可）；一个 plan 表达多要素异构修改。create/split/merge 自带
    # 目标，不接受该键（shape 校验拒绝）。
    wfids: Optional[List[str]] = Field(default=None, min_length=1, max_length=200)
    # ── 共享拓扑编辑参数（ADR-0300；形状校验在 plan._validate_op_shapes） ──
    at: Optional[Any] = None                          # [lon,lat] 或 {wfid, path_index, vertex_index}
    to: Optional[Any] = None                          # move 目标 / connect 目标（坐标或 endpoint ref）
    edge: Optional[Dict[str, Any]] = None             # insert：{between:[a,b]} 或 {near, tolerance_m}
    path: Optional[List[List[float]]] = None          # reshape：新中间顶点序列（≥1 点）
    endpoint: Optional[Dict[str, Any]] = None         # connect：{wfid, end: start|end | vertex_index}
    level_field: Optional[str] = Field(default=None, min_length=1, max_length=64)
    snap_to_edge: Optional[bool] = None               # insert：投影到边（默认 true）

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _route_from_to(cls, data: Any) -> Any:
        """rename_field 的 `from`/`to` 与共享 op 的 `to`（目标坐标）分流。

        rename_field：顶层 from/to → from_field/to_field（历史 wire 契约不变）；
        其余 op：`to` 落到共享 op 的目标坐标字段。extra=forbid 保持 —— 未分流
        的裸 from/to 键对 rename 之外的 op 显式 422。
        """
        if not isinstance(data, dict):
            return data
        if data.get("op") == "rename_field":
            if "from" in data and "from_field" not in data:
                data = {**data, "from_field": data["from"]}
            if "to" in data and "to_field" not in data:
                data = {**data, "to_field": data["to"]}
        return data

    def model_dump_op(self) -> Dict[str, Any]:
        """op dict（服务层词表形状）：rename 的 from/to 还原为顶层键。"""
        payload = self.model_dump(by_alias=True, exclude_none=True)
        if payload.get("op") == "rename_field":
            if "from_field" in payload:
                payload["from"] = payload.pop("from_field")
            if "to_field" in payload:
                payload["to"] = payload.pop("to_field")
        return payload


class EditTargetModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["selection", "explicit"] = "selection"
    ref_id: Optional[str] = None                       # explicit 必填；selection 缺省取 selection.dataset_ref
    feature_ids: Optional[List[str]] = None            # explicit 必填（≤200）


class FeatureEditPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: EditTargetModel
    ops: List[EditOpModel] = Field(min_length=1, max_length=8)


class PlanDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"]


class PolicyPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["confirm_all", "allow_safe_attribute"]


# ── G14 数据集 provisioning 契约 ──────────────────────────────────────────

class TopologyDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: List[Literal["no_overlap", "no_self_intersection", "no_duplicate_points"]] = Field(min_length=1)
    tolerance_m: Optional[float] = Field(default=None, gt=0)


class DatasetCreateRequest(BaseModel):
    """V01：创建空 typed 矢量数据集。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=96)
    geometry_type: Literal["Point", "LineString", "Polygon"]
    topology: Optional[TopologyDeclaration] = None


class PromoteSketchRequest(BaseModel):
    """V02：草图升格为持久数据集。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=96)
    features: List[Dict[str, Any]] = Field(min_length=1)
    topology: Optional[TopologyDeclaration] = None


class EditableCopyRequest(BaseModel):
    """V20：只读源派生编辑副本。"""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=96)


# ── 共享拓扑约束面（goal-gis-topology-authoring；只读端点请求契约） ────────

class ConstraintsInspectRequest(BaseModel):
    """inspect_edit_constraints 的可选匹配规则覆盖（缺省 = exact）。"""

    model_config = ConfigDict(extra="forbid")

    tolerance_m: Optional[float] = Field(default=None, gt=0)
    level_field: Optional[str] = Field(default=None, min_length=1, max_length=64)
    wfids: Optional[List[str]] = Field(default=None, min_length=1, max_length=64)


class ImpactPreviewRequest(BaseModel):
    """get_impact：候选 op 的有界影响投影（纯读，不落 plan store）。"""

    model_config = ConfigDict(extra="forbid")

    ops: List[Dict[str, Any]] = Field(min_length=1, max_length=8)
    feature_ids: Optional[List[str]] = Field(default=None, min_length=1, max_length=200)
    tolerance_m: Optional[float] = Field(default=None, gt=0)
    level_field: Optional[str] = Field(default=None, min_length=1, max_length=64)


class RevalidateRequest(BaseModel):
    """revalidate：当前修订版声明规则的复检请求（无参数）。"""

    model_config = ConfigDict(extra="forbid")
