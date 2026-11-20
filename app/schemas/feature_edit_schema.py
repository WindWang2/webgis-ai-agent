"""Feature-edit REST 契约（/goal 03）。extra=forbid —— 未知键显式拒绝。"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class EditOpModel(BaseModel):
    """编辑操作（discriminated by `op`；词表见 feature_edit.contracts.EDIT_OPS）。"""

    op: Literal[
        "set_fields", "rename_field", "repair_geometry", "move_by", "snap_to_ref",
        # G14 结构性操作
        "create_features", "delete_features", "replace_geometry", "split_feature", "merge_features",
    ]
    fields: Optional[Dict[str, Any]] = None           # set_fields
    fixes: Optional[List[str]] = None                 # repair_geometry
    dx_m: Optional[float] = None                      # move_by（米，必填）
    dy_m: Optional[float] = None                      # move_by（米，必填）
    ref_id: Optional[str] = None                      # snap_to_ref（skip 解引用名单键）
    tolerance_m: Optional[float] = None               # snap_to_ref（米，必填）
    # rename_field 的 from/to（`from` 是 Python 保留字 → 别名）
    from_field: Optional[str] = Field(default=None, alias="from")
    to_field: Optional[str] = Field(default=None, alias="to")
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

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


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
