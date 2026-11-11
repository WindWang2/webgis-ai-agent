"""G17 导入工作台 —— API 合同模型（Pydantic）。

命名/风格遵循 data_preparation_schema.py：XxxRequest/XxxResponse 成对、
additive 字段全部带默认值、ConfigDict(json_schema_extra=examples)。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.data_import.models import INTERPRETATION_FIELD_TYPES, INTERPRETATION_MODES


class BatchCreateResponse(BaseModel):
    """POST /data-import/batches 响应（创建即枚举成员）。"""

    batch_id: str
    status: str = "inspected"
    session_id: str
    created_at: str = ""
    expires_at: str = ""
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    members: List[Dict[str, Any]] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "batch_id": "ib-0f1e2d3c4b5a6978877665544332211f",
                    "status": "inspected",
                    "session_id": "sess-abc",
                    "sources": [
                        {
                            "source_id": "src-1",
                            "original_name": "layers.gpkg",
                            "sha256": "9f86d0818…",
                            "size": 4096,
                            "ext": ".gpkg",
                        }
                    ],
                    "members": [
                        {
                            "member_key": "c:src-1:gpkg:points",
                            "kind": "vector",
                            "display_name": "points",
                            "geometry_type": "Point",
                            "feature_count": 3,
                            "crs": "EPSG:4326",
                            "crs_source": "source_metadata",
                            "fields": ["id", "name"],
                        }
                    ],
                    "notes": [],
                }
            ]
        }
    )


class MemberConfig(BaseModel):
    """单成员解释配置（save_plan 请求体成员项）。"""

    mode: str = Field("auto", description=f"解释模式: {'|'.join(sorted(INTERPRETATION_MODES))}")
    encoding: Optional[str] = Field(None, description="显式编码（如 utf-8/gb18030）；缺省探测回退")
    delimiter: Optional[str] = Field(None, max_length=3, description="分隔符（单字符）；缺省嗅探")
    header_row: int = Field(0, ge=0, le=100, description="表头行（0 基）")
    x_column: Optional[str] = Field(None, max_length=200)
    y_column: Optional[str] = Field(None, max_length=200)
    wkt_column: Optional[str] = Field(None, max_length=200)
    source_crs_declared: Optional[str] = Field(None, max_length=100, description="源 CRS 声明（如 EPSG:32650）")
    field_types: Dict[str, str] = Field(
        default_factory=dict,
        description=f"列 → {'|'.join(sorted(INTERPRETATION_FIELD_TYPES))}；缺省原样字符串（不丢前导零）",
    )
    keep_fields: Optional[List[str]] = Field(None, description="保留字段子集；缺省全部")


class PlanSaveRequest(BaseModel):
    member_configs: Dict[str, MemberConfig] = Field(default_factory=dict)
    selected: Optional[List[str]] = Field(None, description="勾选导入的成员 key 列表")
    expected_plan_version: Optional[int] = Field(None, ge=0)


class PlanSaveResponse(BaseModel):
    batch_id: str
    plan_version: int
    plan_hashes: Dict[str, str] = Field(default_factory=dict)
    status: str = ""


class PreviewRequest(BaseModel):
    member_keys: Optional[List[str]] = Field(None, description="缺省全部可预览成员")
    row_limit: int = Field(200, ge=1, le=2000)


class PreviewResponse(BaseModel):
    batch_id: str
    plan_version: int
    previews: List[Dict[str, Any]] = Field(default_factory=list)


class CommitRequest(BaseModel):
    member_keys: Optional[List[str]] = Field(None, description="缺省 = 当前勾选成员")
    expected_plan_version: Optional[int] = Field(None, ge=0)


class CommitAcceptedResponse(BaseModel):
    batch_id: str
    status: str = "committing"
    members: List[str] = Field(default_factory=list)
    poll: str = ""


class ReceiptsResponse(BaseModel):
    batch_id: str
    status: str
    plan_version: int = 0
    receipts: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)


class BatchListResponse(BaseModel):
    batches: List[Dict[str, Any]] = Field(default_factory=list)


class BatchDetailResponse(BaseModel):
    batch_id: str
    status: str
    session_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    expires_at: str = ""
    plan_version: int = 0
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    members: List[Dict[str, Any]] = Field(default_factory=list)
    interpretations: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    plan_hashes: Dict[str, str] = Field(default_factory=dict)
    previewed_hashes: Dict[str, str] = Field(default_factory=dict)
    receipts: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    license: str = ""
    attribution: str = ""


class CancelResponse(BaseModel):
    batch_id: str
    status: str = "cancelled"


class DeleteResponse(BaseModel):
    batch_id: str
    deleted: bool = False
    note: str = "仅清理未提交 staging；已提交数据集/ref 不受影响"
