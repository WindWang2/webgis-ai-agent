"""
Enterprise Geospatial Data Fabric Manager Service
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
)
from app.models.data_fabric import DataSourceModel, CatalogItemModel, MaterializationModel
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.security import DataFabricSecurity
from app.services.session_data import session_data_manager

from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter
from app.services.data_fabric.adapters.ogc_api_adapter import OGCAPIAdapter
from app.services.data_fabric.adapters.wfs_adapter import WFSAdapter
from app.services.data_fabric.adapters.wms_wmts_adapter import WMSWMTSAdapter
from app.services.data_fabric.adapters.arcgis_adapter import ArcGISAdapter

logger = logging.getLogger(__name__)


class DataFabricManager:
    """
    Core orchestrator and registry for Enterprise Geospatial Data Fabric.
    Manages adapters, data source connections, spatial catalog synchronization,
    pushdown queries, and session ref_id materializations.
    """

    @staticmethod
    def get_adapter(profile: ConnectionProfile) -> GeospatialDataSourceAdapter:
        """Factory method to instantiate protocol-specific adapter."""
        st = (profile.source_type or "").lower().strip()
        if st == "postgis":
            return PostGISAdapter(profile)
        elif st in ("ogc_api", "ogc_api_features", "ogc"):
            return OGCAPIAdapter(profile)
        elif st == "wfs":
            return WFSAdapter(profile)
        elif st in ("wms", "wmts", "wms_wmts"):
            return WMSWMTSAdapter(profile)
        elif st in ("arcgis", "arcgis_rest", "featureserver", "mapserver"):
            return ArcGISAdapter(profile)
        else:
            raise ValueError(f"Unsupported Data Fabric source type: '{profile.source_type}'")

    @classmethod
    def probe_profile(cls, profile: ConnectionProfile) -> DataFabricHealth:
        """Lightweight probe for a ConnectionProfile."""
        try:
            adapter = cls.get_adapter(profile)
            return adapter.health()
        except Exception as e:
            return DataFabricHealth(
                status="unreachable",
                message=f"Adapter creation failed: {e}",
            )

    @classmethod
    def create_data_source(
        cls,
        db: Session,
        name: str,
        source_type: str,
        endpoint_url: str,
        profile_options: Optional[Dict[str, Any]] = None,
        allow_private: bool = False,
        org_id: Optional[int] = None,
        owner_id: Optional[str] = None,
    ) -> DataSourceModel:
        """Register a new Data Source connection profile in DB."""
        source_id = f"ds_{uuid.uuid4().hex[:12]}"
        options = profile_options or {}

        # Validate URL via SSRF policy engine
        clean_url = endpoint_url
        if endpoint_url:
            clean_url = DataFabricSecurity.validate_url(endpoint_url, allow_private=allow_private)

        conn_profile = ConnectionProfile(
            id=source_id,
            name=name,
            source_type=source_type,
            url=clean_url,
            options=options,
            allow_private=allow_private,
        )

        # Probe health & discover capabilities
        health_res = cls.probe_profile(conn_profile)
        capabilities: List[str] = []
        try:
            adapter = cls.get_adapter(conn_profile)
            capabilities = adapter.capabilities()
        except Exception:
            pass

        # SEC-07 (deep-audit round 4): persist the REAL profile. The previous
        # code stored the SANITIZED dict (password -> "********"), so every
        # later probe/sync/query rebuilt the ConnectionProfile with a fake
        # password and failed to connect — a registered source could never be
        # used again. Sanitization belongs on EGRESS only (the REST routes
        # already sanitize before returning profiles to callers).
        stored_profile = conn_profile.model_dump()

        ds_model = DataSourceModel(
            id=source_id,
            org_id=org_id,
            owner_id=owner_id,
            name=name,
            source_type=source_type,
            endpoint_url=clean_url,
            connection_profile=stored_profile,
            capabilities_json=capabilities,
            status=health_res.status,
            last_health_check=datetime.now(timezone.utc),
        )

        db.add(ds_model)
        db.commit()
        db.refresh(ds_model)

        # Automatically sync catalog
        try:
            cls.sync_catalog(db, source_id)
        except Exception as e:
            logger.warning(f"Initial catalog sync failed for {source_id}: {e}")

        return ds_model

    @classmethod
    def sync_catalog(cls, db: Session, source_id: str) -> List[CatalogItemModel]:
        """Discover and sync datasets from data source adapter into Spatial Catalog."""
        ds_model = db.query(DataSourceModel).filter(DataSourceModel.id == source_id).first()
        if not ds_model:
            raise ValueError(f"Data source '{source_id}' not found")

        conn_profile = ConnectionProfile(
            id=ds_model.id,
            name=ds_model.name,
            source_type=ds_model.source_type,
            url=ds_model.endpoint_url,
            options=ds_model.connection_profile.get("options", {}),
            allow_private=ds_model.connection_profile.get("allow_private", False),
        )

        adapter = cls.get_adapter(conn_profile)
        datasets = adapter.list_datasets()
        synced_items: List[CatalogItemModel] = []

        for ds in datasets:
            dataset_name = ds.get("id") or ds.get("name") or ds.get("title")
            if not dataset_name:
                continue

            try:
                descriptor = adapter.describe(dataset_name)
            except Exception:
                descriptor = DatasetDescriptor(
                    id=dataset_name,
                    title=ds.get("title", dataset_name),
                    source_type=ds_model.source_type,
                )

            item_id = f"cat_{source_id}_{dataset_name}".replace(".", "_").replace("/", "_")
            existing = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()

            item_title = descriptor.title or ds.get("title") or dataset_name
            item_desc = descriptor.description or ds.get("description", "")
            geom_type = descriptor.geometry_type or ds.get("geometry_type", "Geometry")
            feature_type = "raster" if geom_type and "raster" in geom_type.lower() else "vector"

            descriptor_dict = descriptor.model_dump()
            meta_profile = {
                "srs": descriptor.srs,
                "feature_count": descriptor.feature_count,
                "fields": descriptor.fields,
            }

            if existing:
                existing.title = item_title
                existing.description = item_desc
                existing.geometry_type = geom_type
                existing.feature_type = feature_type
                existing.crs = descriptor.srs or "EPSG:4326"
                existing.bbox_json = descriptor.bbox
                existing.descriptor_json = descriptor_dict
                existing.meta_profile_json = meta_profile
                existing.updated_at = datetime.now(timezone.utc)
                synced_items.append(existing)
            else:
                new_item = CatalogItemModel(
                    id=item_id,
                    source_id=source_id,
                    name=dataset_name,
                    title=item_title,
                    description=item_desc,
                    geometry_type=geom_type,
                    feature_type=feature_type,
                    crs=descriptor.srs or "EPSG:4326",
                    bbox_json=descriptor.bbox,
                    tags_json=[ds_model.source_type, feature_type],
                    descriptor_json=descriptor_dict,
                    meta_profile_json=meta_profile,
                )
                db.add(new_item)
                synced_items.append(new_item)

        db.commit()
        return synced_items

    @classmethod
    def query_catalog_item(
        cls,
        db: Session,
        item_id: str,
        query_spec: QuerySpec,
    ) -> QueryResult:
        """Execute pushdown query against catalog item."""
        item = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
        if not item:
            raise ValueError(f"Catalog item '{item_id}' not found")

        ds_model = item.data_source
        if not ds_model:
            ds_model = db.query(DataSourceModel).filter(DataSourceModel.id == item.source_id).first()
        if not ds_model:
            raise ValueError(f"Parent data source for item '{item_id}' not found")

        conn_profile = ConnectionProfile(
            id=ds_model.id,
            name=ds_model.name,
            source_type=ds_model.source_type,
            url=ds_model.endpoint_url,
            options=ds_model.connection_profile.get("options", {}),
            allow_private=ds_model.connection_profile.get("allow_private", False),
        )

        adapter = cls.get_adapter(conn_profile)
        return adapter.query(item.name, query_spec)

    @classmethod
    async def materialize_catalog_item(
        cls,
        db: Session,
        session_id: str,
        item_id: str,
        query_spec: Optional[QuerySpec] = None,
        owner_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Materialize catalog query results into session ref_id and save audit log."""
        spec = query_spec or QuerySpec(limit=500)
        item = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
        if not item:
            raise ValueError(f"Catalog item '{item_id}' not found")

        q_res = cls.query_catalog_item(db, item_id, spec)

        fc = {
            "type": "FeatureCollection",
            "features": q_res.features,
            "metadata": {
                "catalog_item_id": item_id,
                "dataset_id": q_res.dataset_id,
                "source_id": item.source_id,
                "total_count": q_res.total_count,
            },
        }

        # Store in SessionStore (Fetch-on-Demand cursor)
        ref_id = await session_data_manager.store(session_id, fc, prefix="df")

        # Save materialization audit record
        mat_id = f"mat_{uuid.uuid4().hex[:12]}"
        mat_record = MaterializationModel(
            id=mat_id,
            dataset_id=item_id,
            source_id=item.source_id,
            ref_id=ref_id,
            query_spec_json=spec.model_dump(),
            record_count=len(q_res.features),
            materialized_at=datetime.now(timezone.utc),
        )
        db.add(mat_record)
        db.commit()

        return {
            "ref_id": ref_id,
            "feature_count": len(q_res.features),
            "total_count": q_res.total_count,
            "dataset_id": item_id,
            "source_id": item.source_id,
            "title": item.title,
        }


data_fabric_manager = DataFabricManager()
