"""
Geospatial Data Fabric: Protocol & Data Source Adapters
"""
from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter
from app.services.data_fabric.adapters.ogc_api_adapter import OGCAPIAdapter
from app.services.data_fabric.adapters.wfs_adapter import WFSAdapter
from app.services.data_fabric.adapters.wms_wmts_adapter import WMSWMTSAdapter
from app.services.data_fabric.adapters.arcgis_adapter import ArcGISAdapter
from app.services.data_fabric.adapters.stac_adapter import STACAdapter
from app.services.data_fabric.adapters.geoparquet_adapter import GeoParquetAdapter
from app.services.data_fabric.adapters.flatgeobuf_adapter import FlatGeobufAdapter
from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter
from app.services.data_fabric.adapters.s3_storage_seam import S3StorageSeam, S3StorageAdapter, S3ObjectStorageSeam

# Convenient aliases
OGCApiFeaturesAdapter = OGCAPIAdapter
OGCAPIFeaturesAdapter = OGCAPIAdapter
WMSAdapter = WMSWMTSAdapter
WMTSAdapter = WMSWMTSAdapter
ArcGISRestAdapter = ArcGISAdapter
ArcGISRESTAdapter = ArcGISAdapter
ArcGISRESTAdapter = ArcGISAdapter

__all__ = [
    "PostGISAdapter",
    "OGCAPIAdapter",
    "OGCApiFeaturesAdapter",
    "OGCAPIFeaturesAdapter",
    "WFSAdapter",
    "WMSWMTSAdapter",
    "WMSAdapter",
    "WMTSAdapter",
    "ArcGISAdapter",
    "ArcGISRestAdapter",
    "ArcGISRESTAdapter",
    "STACAdapter",
    "GeoParquetAdapter",
    "FlatGeobufAdapter",
    "PMTilesAdapter",
    "S3StorageSeam",
    "S3StorageAdapter",
    "S3ObjectStorageSeam",
]
