"""
Pydantic Schemas for Geospatial Data Fabric Contract
"""
import time
from typing import Dict, Any, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class ConnectionProfile(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: Optional[str] = "profile_default"
    source_type: Optional[str] = "generic"
    provider_type: Optional[str] = "generic"
    name: Optional[str] = "default"
    url: Optional[str] = ""
    endpoint: Optional[str] = ""
    endpoint_url: Optional[str] = ""
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    region: Optional[str] = None
    credentials: Dict[str, Any] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)
    allow_private: bool = False

    def model_post_init(self, __context: Any) -> None:
        eff_url = self.url or self.endpoint_url or self.endpoint or ""
        if not self.url:
            self.url = eff_url
        if not self.endpoint:
            self.endpoint = eff_url
        if not self.endpoint_url:
            self.endpoint_url = eff_url


class CatalogItemModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    source_id: str = "src_1"
    name: str = ""
    title: str = ""
    geometry_type: str = "Polygon"
    feature_type: str = "vector"
    crs: str = "EPSG:4326"
    bbox: Optional[List[float]] = Field(default_factory=lambda: [-180.0, -90.0, 180.0, 90.0])
    tags: List[str] = Field(default_factory=list)


class DatasetDescriptor(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    source_type: str = "generic"
    source_id: Optional[str] = None
    title: Optional[str] = ""
    name: Optional[str] = ""
    description: Optional[str] = ""
    geometry_type: Optional[str] = "Unknown"
    feature_type: Optional[str] = "vector"
    data_type: Optional[str] = "vector"
    srs: Optional[str] = "EPSG:4326"
    crs: Optional[str] = "EPSG:4326"
    bbox: Optional[List[float]] = Field(default_factory=lambda: [-180.0, -90.0, 180.0, 90.0])
    feature_count: Optional[int] = 0
    fields: List[Dict[str, Any]] = Field(default_factory=list)
    schema_fields: Dict[str, str] = Field(default_factory=dict)
    query_capabilities: List[str] = Field(default_factory=list)
    style_hints: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def dataset_id(self) -> str:
        return self.id


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    bbox: Optional[List[float]] = None
    columns: Optional[List[str]] = None
    fields: Optional[List[str]] = None
    limit: int = 100
    offset: int = 0
    filter_expr: Optional[Dict[str, Any]] = None
    where: Optional[Union[str, Dict[str, Any]]] = None
    datetime_range: Optional[List[str]] = None
    zoom: Optional[int] = None
    tile_coords: Optional[Dict[str, int]] = None

    def model_post_init(self, __context: Any) -> None:
        if self.fields and not self.columns:
            self.columns = self.fields
        elif self.columns and not self.fields:
            self.fields = self.columns


class QueryResult(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    dataset_id: str
    query_spec: Optional[QuerySpec] = None
    features: List[Dict[str, Any]] = Field(default_factory=list)
    data: Any = None
    ref_id: Optional[str] = None
    total_count: Optional[int] = 0
    total_matching: Optional[int] = 0
    returned_count: int = 0
    truncated: bool = False
    is_pushed_down: bool = True
    payload_type: str = "geojson"
    execution_time_seconds: float = 0.0
    schema_info: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DataFabricHealth(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    status: str = "healthy"
    source_type: Optional[str] = "generic"
    adapter: Optional[str] = ""
    latency_ms: float = 0.0
    message: Optional[str] = None
    reachable: bool = True
    auth_status: str = "ok"
    capability_status: str = "healthy"
    last_checked: float = Field(default_factory=time.time)
    details: Dict[str, Any] = Field(default_factory=dict)
