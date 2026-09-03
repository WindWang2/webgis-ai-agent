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
from app.services.data_fabric.circuit_breaker import get_breaker_registry
from app.services.data_fabric.errors import (
    DataFabricError,
    MATERIALIZATION_FAILED,
    error_from_query_result,
)
from app.services.data_fabric.fingerprint import dataset_fingerprint_service
from app.services.data_fabric.limits import enforce_result_bounds
from app.services.data_fabric.metadata import (
    classify_feature_type,
    normalize_crs,
    normalize_feature_count,
    normalize_geometry_type,
)
from app.services.data_fabric.registry import build_adapter, resolve_adapter_spec
from app.services.data_fabric.security import DataFabricSecurity
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)


class _SkipDescribe(Exception):
    """sync_catalog 内部：describe 失败的条目跳过落库（M2 语义）。"""


def _execute_remote_query(
    adapter: GeospatialDataSourceAdapter,
    source_key: str,
    dataset_name: str,
    query_spec: QuerySpec,
):
    """Run ``adapter.query`` under the per-source circuit breaker and surface
    in-band adapter failures as typed errors.

    #766: adapters return empty-but-"successful" QueryResults whose failure
    signal lives in ``schema_info['error']`` / ``metadata['error_type']``.
    Those are converted to typed ``DataFabricError`` so "fetch failed" is never
    mistaken for a genuinely empty dataset.

    #770: the remote call runs via ``get_breaker_registry().call`` so repeated
    failures (raised or in-band) trip the breaker and later attempts fail fast
    with ``SourceUnreachableError`` instead of waiting the full adapter
    timeout.
    """
    registry = get_breaker_registry()

    def _query_and_validate():
        result = adapter.query(dataset_name, query_spec)
        err = error_from_query_result(result)
        if err is not None:
            # Raise INSIDE the breaker-wrapped callable so the in-band failure
            # is recorded as a breaker failure (a non-raising empty result
            # would otherwise be counted as success and reset the counter).
            raise err
        return result

    return registry.call(source_key, _query_and_validate)


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

        # #767: reject unregistered/unsupported source types LOUDLY, before any
        # probe/persist. Previously the probe/capabilities try/except below
        # swallowed UnsupportedSourceError, the row was still persisted with
        # status="unreachable", and create returned success — a false-success
        # registration for types the fabric can never serve (e.g. 'csv').
        resolve_adapter_spec(source_type)

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
    def sync_catalog(cls, db: Session, source_id: str) -> Dict[str, Any]:
        """增量目录同步（ADR-0094 §9，修复审计 M2）。

        - describe 失败的条目**跳过落库**（不再用合成 stub descriptor 污染
          catalog 被 fingerprint 锁死）；失败计入 warnings。
        - 消失的条目标记 ``availability='unavailable'``（保留元数据供 stale
          检索），重新出现则恢复 available。
        - 返回结构化 diff：{added, updated, unchanged, removed, warnings,
          counts}。
        - 既有并发/批查/熔断/TTL-cache 语义保留（Section 30/31）。
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

        names: List[str] = []
        raw: Dict[str, Dict[str, Any]] = {}
        for ds in datasets:
            dataset_name = ds.get("id") or ds.get("name") or ds.get("title")
            if not dataset_name:
                continue
            names.append(dataset_name)
            raw[dataset_name] = ds

        from concurrent.futures import ThreadPoolExecutor, as_completed
        from app.core.config import settings as _settings

        max_workers = max(1, min(16, int(getattr(_settings, "DATA_FABRIC_SYNC_CONCURRENCY", 4))))

        describe_errors: Dict[str, str] = {}

        def _describe(name: str) -> DatasetDescriptor:
            """describe（熔断 + TTL cache）；失败返回 None-marker 由收集方跳过。"""
            from app.services.data_fabric.metadata_cache import cached_describe

            class _DescribeFailed:
                def __init__(self, err: str):
                    self.error = err

            def _do(dataset_id: str):
                try:
                    return get_breaker_registry().call(source_id, adapter.describe, dataset_id)
                except Exception as e:  # describe 失败 → 跳过落库（M2）
                    return _DescribeFailed(str(e))

            out = cached_describe(_do, source_id, name, scope=f"source:{source_id}")
            if isinstance(out, _DescribeFailed):
                describe_errors[name] = out.error
                raise _SkipDescribe(name)
            return out

        descriptors: Dict[str, DatasetDescriptor] = {}
        if names:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_describe, n): n for n in names}
                for fut in as_completed(futures):
                    fname = futures[fut]
                    try:
                        descriptors[fname] = fut.result()
                    except _SkipDescribe:
                        continue
                    except Exception:
                        continue

        existing_rows = db.query(CatalogItemModel).filter(CatalogItemModel.source_id == source_id).all()
        existing_by_id: Dict[str, CatalogItemModel] = {row.id: row for row in existing_rows}

        synced_items: List[CatalogItemModel] = []
        now = datetime.now(timezone.utc)
        added = updated = unchanged = 0
        seen_ids = set()
        for name in names:
            descriptor = descriptors.get(name)
            if descriptor is None:
                continue  # describe 失败：跳过（不落 stub，不锁 fingerprint）
            ds = raw[name]
            item_id = f"cat_{source_id}_{name}".replace(".", "_").replace("/", "_")
            seen_ids.add(item_id)

            item_title = descriptor.title or ds.get("title") or name
            item_desc = descriptor.description or ds.get("description", "")
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
                if (
                    existing.fingerprint == fp
                    and existing.geometry_type == geom_type
                    and getattr(existing, "availability", "available") == "available"
                ):
                    unchanged += 1
                    synced_items.append(existing)
                    continue
                was_unavailable = existing.availability != "available"
                existing.title = item_title
                existing.description = item_desc
                existing.geometry_type = geom_type
                existing.feature_type = feature_type
                existing.crs = crs
                existing.bbox_json = descriptor.bbox
                existing.descriptor_json = descriptor_dict
                existing.meta_profile_json = meta_profile
                existing.fingerprint = fp
                existing.availability = "available"
                existing.updated_at = now
                updated += 1 if not was_unavailable else 0
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
                    availability="available",
                )
                db.add(new_item)
                added += 1
                synced_items.append(new_item)

        # 消失的条目 → availability=unavailable（保留元数据，不物理删除）
        removed = 0
        for item_id, row in existing_by_id.items():
            if item_id not in seen_ids and getattr(row, "availability", "available") == "available":
                row.availability = "unavailable"
                row.updated_at = now
                removed += 1

        warnings = [f"describe failed for '{n}': {e[:120]}" for n, e in describe_errors.items()]
        if removed:
            warnings.append(f"{removed} dataset(s) no longer listed by the source; marked unavailable")

        db.commit()
        return {
            "status": "synced",
            "source_id": source_id,
            "items": synced_items,
            "added": added,
            "updated": updated,
            "unchanged": unchanged,
            "removed": removed,
            "warnings": warnings,
            "counts": {
                "total": len(synced_items),
                "listed": len(names),
                "describe_failures": len(describe_errors),
            },
        }

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
        # #766/#770: run under the circuit breaker; in-band adapter failure
        # markers surface as typed DataFabricError (never a silent empty set).
        result = _execute_remote_query(adapter, ds_model.id, item.name, query_spec)
        # Resource guard (Section 22 / #425): only materialize enforced bounds
        # before — the preview/query REST paths returned unbounded payloads
        # (up to 10k features of arbitrary geometry, no byte cap). The guard
        # does not trust the remote `limit` parameter (servers may ignore it).
        enforce_result_bounds(result.features)
        return result

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
        # #766/#770: the worker-thread call runs under the per-source circuit
        # breaker and converts in-band adapter failure markers into typed
        # DataFabricError (fetch failed ≠ empty dataset).
        import asyncio

        result = await asyncio.to_thread(
            _execute_remote_query, adapter, ds_model.id, item.name, query_spec
        )
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        # Same resource guard as the sync path (#425): the preview/query REST
        # routes ride this method after the offload fix, so their responses
        # must respect the hard feature/byte bounds too. materialize() keeps
        # its own (idempotent) enforcement before storing the ref.
        enforce_result_bounds(result.features)
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

        V2（ADR-0094 §8/§43）：REST 与 agent 工具共用 MaterializationService
        单管线（ref 前缀统一 ``data-fabric``，evidence 随 FC metadata 落库）；
        审计行记录 query_fingerprint/result_mode。真实性契约保留：失败 =
        ``success=False`` + ``ref_id=None`` + typed error_type；审计提交失败
        补偿删除 ref（#618-6）。
        """
        from app.services.data_fabric.materialization_service import materialization_service

        spec = query_spec or QuerySpec(limit=500)
        item = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
        if not item:
            raise ValueError(f"Catalog item '{item_id}' not found")

        base = {
            "feature_count": 0,
            "total_count": 0,
            "dataset_id": item_id,
            "source_id": item.source_id,
            "title": item.title,
        }

        ds_model = item.data_source
        if not ds_model:
            ds_model = db.query(DataSourceModel).filter(DataSourceModel.id == item.source_id).first()
        if not ds_model:
            raise ValueError(f"Parent data source for item '{item_id}' not found")

        if cancel_token is not None:
            cancel_token.raise_if_cancelled()

        conn_profile = ConnectionProfile(
            id=ds_model.id,
            name=ds_model.name,
            source_type=ds_model.source_type,
            url=ds_model.endpoint_url,
            options=ds_model.connection_profile.get("options", {}),
            allow_private=ds_model.connection_profile.get("allow_private", False),
        )
        adapter = cls.get_adapter(conn_profile)

        # 单管线：REST 路径与 materialize_dataset 工具同一 MaterializationService
        try:
            mat = await materialization_service.materialize_dataset(
                adapter, item.name, spec, session_id=session_id,
            )
        except DataFabricError as e:
            logger.error("[DataFabricManager] materialize query failed for item '%s': %s", item_id, e)
            d = e.to_dict()
            return {**base, "success": False, "ref_id": None,
                    "error_type": d["error_type"], "error": d["error"]}
        except Exception as e:
            logger.exception("[DataFabricManager] materialize failed for item '%s'", item_id)
            return {**base, "success": False, "ref_id": None,
                    "error_type": MATERIALIZATION_FAILED,
                    "error": f"materialization failed: {e}"}

        if not mat.get("success"):
            return {**base, "success": False, "ref_id": None,
                    "error_type": mat.get("error_type", MATERIALIZATION_FAILED),
                    "error": mat.get("error", "materialization failed")}

        if cancel_token is not None:
            cancel_token.raise_if_cancelled()

        ref_id = mat["ref_id"]
        result_mode = mat.get("result_mode", "features")
        evidence = mat.get("query_evidence") or {}

        # 轻量模式（statistics/descriptor/vector_tile）：无 ref、无审计行
        if ref_id is None:
            return {
                **base,
                "success": True,
                "ref_id": None,
                "result_mode": result_mode,
                "data": mat.get("data"),
                "feature_count": 0,
                "total_count": mat.get("total_count", 0),
                "query_evidence": evidence,
            }

        # 审计原子性：payload 已存 + 审计必须同生共死（#618-6 补偿删除）。
        mat_id = f"mat_{uuid.uuid4().hex[:12]}"
        mat_record = MaterializationModel(
            id=mat_id,
            dataset_id=item_id,
            source_id=item.source_id,
            ref_id=ref_id,
            query_spec_json=spec.model_dump(),
            fingerprint=mat.get("fingerprint"),
            query_fingerprint=evidence.get("query_fingerprint"),
            result_mode=result_mode,
            record_count=mat.get("feature_count", 0),
            materialized_at=datetime.now(timezone.utc),
        )
        db.add(mat_record)
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "[DataFabricManager] audit commit failed for item '%s'; compensating ref '%s'",
                item_id, ref_id,
            )
            deleter = getattr(session_data_manager, "delete_ref", None)
            if deleter is not None:
                try:
                    await deleter(session_id, ref_id)
                except Exception:
                    logger.exception(
                        "[DataFabricManager] failed to delete unaudited ref '%s'", ref_id)
            return {**base, "success": False, "ref_id": None,
                    "error_type": MATERIALIZATION_FAILED,
                    "error": "materialization audit failed"}

        return {
            **base,
            "success": True,
            "ref_id": ref_id,
            "result_mode": result_mode,
            "feature_count": mat.get("feature_count", 0),
            "total_count": mat.get("total_count", 0),
            "truncated": mat.get("truncated", False),
            "next_cursor": mat.get("next_cursor"),
            "has_more": mat.get("has_more", False),
            "is_demo": mat.get("is_demo", False),
            "query_evidence": evidence,
        }

    @classmethod
    def explain_catalog_item(
        cls,
        db: Session,
        item_id: str,
        query_spec: Optional[QuerySpec] = None,
    ) -> Dict[str, Any]:
        """explain（dry-run 计划，不执行；ADR-0094 §5/§13）。

        输出可读 plan lines + 结构化 QueryPlan + capability 矩阵。
        永不包含 secret/连接 URI/password。
        """
        from app.services.data_fabric.query.capabilities import get_capabilities
        from app.services.data_fabric.query.normalize import normalize_query_spec
        from app.services.data_fabric.query.planner import plan_query

        item = db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).first()
        if not item:
            raise ValueError(f"Catalog item '{item_id}' not found")

        descriptor = DatasetDescriptor(
            id=item.name,
            source_type=(item.data_source.source_type if item.data_source else "generic"),
            source_id=item.source_id,
            title=item.title or item.name,
            geometry_type=item.geometry_type,
            srs=item.crs,
            bbox=item.bbox_json,
            feature_count=(item.meta_profile_json or {}).get("feature_count"),
            fields=(item.meta_profile_json or {}).get("fields", []),
            metadata=dict(item.descriptor_json or {}).get("metadata", {}),
        )
        fp = item.fingerprint or dataset_fingerprint_service.calculate_descriptor_fingerprint(descriptor)
        try:
            v2 = normalize_query_spec(query_spec or QuerySpec(limit=100))
        except DataFabricError as e:
            return {
                "status": "error",
                "error_type": e.code,
                "error": str(e),
                "dataset_id": item_id,
            }
        # R1-M8：优先探测后 capability（与执行路径一致），静态默认兜底。
        caps = None
        try:
            conn_profile = ConnectionProfile(
                id=item.data_source.id,
                name=item.data_source.name,
                source_type=item.data_source.source_type,
                url=item.data_source.endpoint_url,
                options=item.data_source.connection_profile.get("options", {}),
                allow_private=item.data_source.connection_profile.get("allow_private", False),
            )
            adapter = cls.get_adapter(conn_profile)
            caps = getattr(adapter, "capabilities_v2", None)
            caps = caps(descriptor) if caps else None
        except Exception:
            caps = None
        if caps is None:
            caps = get_capabilities(descriptor.source_type)
        try:
            plan = plan_query(v2, descriptor, caps, source_id=item.source_id, dataset_fingerprint=fp)
        except DataFabricError as e:
            return {
                "status": "error",
                "error_type": e.code,
                "error": str(e),
                "details": e.details,
                "dataset_id": item_id,
            }
        return {
            "status": "success",
            "dataset_id": item_id,
            "dataset_fingerprint": fp,
            "explain": plan.summary_lines(),
            "plan": plan.model_dump(),
            "capabilities": caps.model_dump(),
            "dataset": {
                "geometry_type": descriptor.geometry_type,
                "srs": descriptor.srs,
                "feature_count": descriptor.feature_count,
            },
        }


data_fabric_manager = DataFabricManager()
