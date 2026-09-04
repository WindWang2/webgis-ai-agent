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
        # 审计 C2 配套（V2）：只配 DSN 的 profile 必须把 DSN 解析进结构化
        # 字段——否则 adapter 只能拿到 None 并静默回退 localhost 空密码默认
        # 值，把配置错误伪装成连接失败/空数据。显式传入的结构化字段优先，
        # 绝不覆盖。
        if eff_url.startswith(("postgresql://", "postgres://")) and self.host is None:
            from urllib.parse import urlparse, unquote
            u = urlparse(eff_url)
            if u.hostname:
                self.host = u.hostname
                self.port = u.port or 5432
                db = u.path.lstrip("/")
                if db and not self.database:
                    self.database = db
                if u.username and not self.username:
                    self.username = unquote(u.username)
                if u.password is not None and not self.password:
                    self.password = unquote(u.password)


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
    """Dataset metadata contract (ADR-0094)。

    诚实默认（V2 / 审计 C2 修复）：``srs``/``crs``/``bbox``/``feature_count``
    默认 None = 未知，绝不伪造 EPSG:4326 / 全球 extent / 0 行。消费方必须
    将 None 渲染为 "unknown" 并在 planner 中按未知处理。
    """

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
    srs: Optional[str] = None
    crs: Optional[str] = None
    bbox: Optional[List[float]] = None
    feature_count: Optional[int] = None
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
    # ── V2 (ADR-0094) additive fields ─────────────────────────────────────
    next_cursor: Optional[str] = None
    has_more: bool = False
    result_mode: Optional[str] = None       # descriptor|statistics|sample|features|materialize|vector_tile
    is_demo: bool = False


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
