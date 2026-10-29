"""Feature-edit REST 契约（/goal 03）。extra=forbid —— 未知键显式拒绝。"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class EditOpModel(BaseModel):
    """编辑操作（discriminated by `op`；词表见 feature_edit.contracts.EDIT_OPS）。"""

    model_config = ConfigDict(extra="forbid")

    op: Literal["set_fields", "rename_field", "repair_geometry", "move_by", "snap_to_ref"]
    fields: Optional[Dict[str, Any]] = None           # set_fields
    fixes: Optional[List[str]] = None                 # repair_geometry
    dx_m: Optional[float] = None                      # move_by（米，必填）
    dy_m: Optional[float] = None                      # move_by（米，必填）
    ref_id: Optional[str] = None                      # snap_to_ref（skip 解引用名单键）
    tolerance_m: Optional[float] = None               # snap_to_ref（米，必填）
    # rename_field 的 from/to（`from` 是 Python 保留字 → 别名）
    from_field: Optional[str] = Field(default=None, alias="from")
    to_field: Optional[str] = Field(default=None, alias="to")

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
