from pydantic import BaseModel, Field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

class DataSourceType(str, Enum):
    POSTGIS = "postgis"
    OSS = "oss"
    THIRD_PARTY_API = "third_party_api"
    LOCAL_FILE = "local_file"

class GISDataType(str, Enum):
    VECTOR = "vector"
    RASTER = "raster"
    ATTRIBUTE = "attribute"

class DataQuery(BaseModel):
    data_source: DataSourceType
    query_params: Dict[str, Any] = Field(default_factory=dict)
    data_type: Optional[GISDataType] = None
    user_id: Optional[str] = None
    user_role: str = "user"
    skip_cache: bool = False
    cache_ttl: Optional[int] = None

class StandardGISData(BaseModel):
    success: bool = True
    data_type: GISDataType
    features: List[Dict[str, Any]] = Field(default_factory=list)
    raster_data: Optional[Any] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None

class DataSourceAdapter:
    """Base interface for all data source adapters"""
    def query(self, query_params: Dict[str, Any]) -> Any:
        raise NotImplementedError("Subclasses must implement query method")
