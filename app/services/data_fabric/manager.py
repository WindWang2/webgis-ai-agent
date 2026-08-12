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
from app.services.data_fabric.errors import MATERIALIZATION_FAILED
from app.services.data_fabric.fingerprint import dataset_fingerprint_service
from app.services.data_fabric.limits import enforce_result_bounds
from app.services.data_fabric.metadata import (
    classify_feature_type,
    normalize_crs,
    normalize_feature_count,
    normalize_geometry_type,
)
from app.services.data_fabric.registry import build_adapter
from app.services.data_fabric.security import DataFabricSecurity
from app.services.session_data import session_data_manager
from app.services.session_data_protocol import is_unavailable_ref

logger = logging.getLogger(__name__)


class DataFabricManager:
    """
    Core orchestrator and registry for Enterprise Geospatial Data Fabric.
    Manages adapters, data source connections, spatial catalog synchronization,
    pushdown queries, and session ref_id materializations.
    """

    @staticmethod
    def get_adapter(profile: ConnectionProfile) -> GeospatialDataSourceAdapter:
        """Factory method to instantiate protocol-specific adapter.

        Routes through the canonical ``AdapterRegistry`` (single source of
        truth). All 10 real adapters are reachable; an unregistered source type
        raises ``UnsupportedSourceError`` — never a silent mock fallback.
        """
        return build_adapter(profile)

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
        """Discover and sync datasets from data source adapter into Spatial Catalog.

        Efficiency (Section 30/31):
        - describe() calls run with bounded concurrency (network I/O), clamped to
          a small pool — a 5000-dataset source no longer serializes ~5000 remote
          round-trips;
        - existing catalog rows are fetched in ONE batch query (no per-item N+1);
        - incremental: each row's descriptor fingerprint is stored and compared;
          unchanged rows are skipped (no needless write/updated_at churn).
        """
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

        # Resolve dataset names + keep the raw list-datasets dicts for fallbacks.
        names: List[str] = []
        raw: Dict[str, Dict[str, Any]] = {}
        for ds in datasets:
            dataset_name = ds.get("id") or ds.get("name") or ds.get("title")
            if not dataset_name:
                continue
            names.append(dataset_name)
            raw[dataset_name] = ds

        # Bounded-concurrency describe(). Adapter sessions are thread-safe for
        # independent requests; keep the pool small to avoid hammering sources.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from app.core.config import settings as _settings

        max_workers = max(1, min(16, int(getattr(_settings, "DATA_FABRIC_SYNC_CONCURRENCY", 4))))

        def _describe(name: str) -> DatasetDescriptor:
            # Tenant/source-scoped TTL cache (Section 37): avoids re-describing
            # unchanged datasets on back-to-back syncs. scope is the source key —
            # sync is server-side per source, never crosses tenants.
            from app.services.data_fabric.metadata_cache import cached_describe

            def _do(dataset_id: str) -> DatasetDescriptor:
                try:
                    return adapter.describe(dataset_id)
                except Exception:
                    return DatasetDescriptor(
                        id=dataset_id, title=raw.get(dataset_id, {}).get("title", dataset_id),
                        source_type=ds_model.source_type,
                    )

            return cached_describe(_do, source_id, name, scope=f"source:{source_id}")

        descriptors: Dict[str, DatasetDescriptor] = {}
        if names:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_describe, n): n for n in names}
                for fut in as_completed(futures):
                    descriptors[futures[fut]] = fut.result()

        # Batch DB lookup: ONE query for all existing items for this source.
        existing_rows = db.query(CatalogItemModel).filter(CatalogItemModel.source_id == source_id).all()
        existing_by_id: Dict[str, CatalogItemModel] = {row.id: row for row in existing_rows}

        synced_items: List[CatalogItemModel] = []
        now = datetime.now(timezone.utc)
        for name in names:
            descriptor = descriptors.get(name) or DatasetDescriptor(id=name, source_type=ds_model.source_type)
            ds = raw[name]
            item_id = f"cat_{source_id}_{name}".replace(".", "_").replace("/", "_")

            item_title = descriptor.title or ds.get("title") or name
            item_desc = descriptor.description or ds.get("description", "")
            # Metadata truthfulness (Section 27/28): normalize instead of fabricating.
            geom_type = normalize_geometry_type(descriptor.geometry_type or ds.get("geometry_type"))
            feature_type = classify_feature_type(geom_type)
            crs = normalize_crs(descriptor.srs or descriptor.crs)
            descriptor_dict = descriptor.model_dump()
            meta_profile = {
                "srs": crs,
                "feature_count": normalize_feature_count(descriptor.feature_count),
                "fields": descriptor.fields,
            }
            fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(descriptor)

            existing = existing_by_id.get(item_id)
            if existing:
                # Incremental skip: descriptor unchanged since last sync → no write.
                if existing.fingerprint == fp and existing.geometry_type == geom_type:
                    synced_items.append(existing)
                    continue
                existing.title = item_title
                existing.description = item_desc
                existing.geometry_type = geom_type
                existing.feature_type = feature_type
                existing.crs = crs
                existing.bbox_json = descriptor.bbox
                existing.descriptor_json = descriptor_dict
                existing.meta_profile_json = meta_profile
                existing.fingerprint = fp
                existing.updated_at = now
                synced_items.append(existing)
            else:
                new_item = CatalogItemModel(
                    id=item_id,
                    source_id=source_id,
                    name=name,
                    title=item_title,
                    description=item_desc,
                    geometry_type=geom_type,
                    feature_type=feature_type,
                    crs=crs,
                    bbox_json=descriptor.bbox,
                    tags_json=[ds_model.source_type, feature_type],
                    descriptor_json=descriptor_dict,
                    meta_profile_json=meta_profile,
                    fingerprint=fp,
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
    async def query_catalog_item_async(
        cls,
        db: Session,
        item_id: str,
        query_spec: QuerySpec,
        cancel_token: Optional["object"] = None,
    ) -> QueryResult:
        """Async-safe pushdown query.

        The DB lookups run on the calling coroutine (fast, and the SQLAlchemy
        session is not thread-safe); the blocking remote ``adapter.query()`` runs
        in a worker thread via ``asyncio.to_thread`` so it does NOT stall the
        event loop. Cooperative cancellation: if a ``cancel_token`` is supplied,
        ``raise_if_cancelled`` is checked before and after the remote fetch, so a
        cancellation during the fetch surfaces as ``OperationCancelled`` and the
        caller never materializes a stale result.
        """
        # Cooperative cancel check first — abort before any DB or remote work.
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()

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

        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        # Offload the blocking remote query; the await lets the event loop run
        # and lets asyncio.CancelledError propagate on task cancellation.
        import asyncio

        result = await asyncio.to_thread(adapter.query, item.name, query_spec)
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        return result

    @classmethod
    async def materialize_catalog_item(
        cls,
        db: Session,
        session_id: str,
        item_id: str,
        query_spec: Optional[QuerySpec] = None,
        owner_token: Optional[str] = None,
        cancel_token: Optional["object"] = None,
    ) -> Dict[str, Any]:
        """Materialize catalog query results into session ref_id and save audit log.

        Truthfulness contract: the returned dict carries ``success``. On any
        store or audit failure the result is ``success=False`` with ``ref_id=None``
        and a typed ``error_type`` — no fake ref is persisted or returned, and no
        audit row is written for a non-retrievable ref.

        The blocking remote query runs off the event loop (see
        ``query_catalog_item_async``); a supplied ``cancel_token`` aborts before
        materialization if the operation was cancelled during the fetch.
        """
        spec = query_spec or QuerySpec(limit=500)
        item = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
        if not item:
            raise ValueError(f"Catalog item '{item_id}' not found")

        q_res = await cls.query_catalog_item_async(db, item_id, spec, cancel_token=cancel_token)

        feature_count = len(q_res.features)
        base = {
            "feature_count": feature_count,
            "total_count": q_res.total_count,
            "dataset_id": item_id,
            "source_id": item.source_id,
            "title": item.title,
        }

        # Resource guard (Section 22): reject oversized results before storing.
        # Raises ResultTooLargeError with an actionable hint; the route maps it
        # to HTTP 413.
        enforce_result_bounds(q_res.features)

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

        # Store in SessionStore (Fetch-on-Demand cursor). A ref exists iff its
        # payload is retrievable: an exception OR the store-unavailability
        # sentinel is a real failure — never persist a fake audit ref.
        try:
            ref_id = await session_data_manager.store(session_id, fc, prefix="df")
        except Exception as e:
            logger.error(
                "[DataFabricManager] materialize store failed for item '%s': %s",
                item_id, e,
            )
            return {**base, "success": False, "ref_id": None,
                    "error_type": MATERIALIZATION_FAILED,
                    "error": f"session store failed: {e}"}

        if is_unavailable_ref(ref_id):
            logger.error(
                "[DataFabricManager] materialize store unavailable for item '%s': %s",
                item_id, ref_id,
            )
            return {**base, "success": False, "ref_id": None,
                    "error_type": MATERIALIZATION_FAILED,
                    "error": "session store unavailable"}

        # Materialization atomicity: query success AND payload stored AND audit
        # record committed must hold together. If the audit commit fails after
        # the payload was stored, the ref is orphaned (no audit) — report failure
        # so the caller does not trust an unaudited ref, and roll the TX back.
        mat_id = f"mat_{uuid.uuid4().hex[:12]}"
        mat_record = MaterializationModel(
            id=mat_id,
            dataset_id=item_id,
            source_id=item.source_id,
            ref_id=ref_id,
            query_spec_json=spec.model_dump(),
            record_count=feature_count,
            materialized_at=datetime.now(timezone.utc),
        )
        db.add(mat_record)
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "[DataFabricManager] audit commit failed for item '%s'; ref '%s' orphaned",
                item_id, ref_id,
            )
            return {**base, "success": False, "ref_id": None,
                    "error_type": MATERIALIZATION_FAILED,
                    "error": "materialization audit failed"}

        return {**base, "success": True, "ref_id": ref_id}


data_fabric_manager = DataFabricManager()
