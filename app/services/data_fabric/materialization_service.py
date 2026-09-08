"""Geospatial Data Fabric: Materialization Service — V2 (ADR-0094).

单一物化管线（REST 与 agent 工具共用）：

- ``execute_query``：adapter 查询（V2 adapter 抛 typed error；in-band 标记
  仅作 V1 兼容兜底转换）。
- ``materialize``：按 ResultMode 分流——
  - FEATURES / MATERIALIZE：FeatureCollection → SessionStore ref
    （prefix 统一 ``data-fabric``）；
  - STATISTICS / DESCRIPTOR / VECTOR_TILE：零物化，data 直返 +
    query_evidence（不产生 ref，LLM/前端直接消费轻量结果）；
  - SAMPLE：有界特征直返（deterministic，不强制物化）。
- 真实性契约（V3 保留）：ref 存在 ⟺ payload 可取回；失败 = typed
  ``MATERIALIZATION_FAILED`` + ``ref_id=None``，绝不伪造。
- is_demo 语义（V2）：demo 判定优先读 ``QueryResult.is_demo``（V2 adapter
  显式标注），V1 source_type 判定作兜底。
- query_evidence（ADR-0094 §43）随结果返回并写入 FC metadata，供
  Map Product / Workflow lineage（ADR-0092）消费；不建第二 lineage store。
"""
import asyncio
import logging
import re
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

from app.schemas.data_fabric_schema import QueryResult, QuerySpec
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import (
    DataFabricError,
    MaterializationFailedError,
    error_from_query_result,
)
from app.services.data_fabric.fingerprint import dataset_fingerprint_service
from app.services.data_fabric.limits import enforce_result_bounds
from app.services.session_data import session_data_manager
from app.services.session_data_protocol import is_unavailable_ref

logger = logging.getLogger(__name__)

#: GeoParquet 磁盘工件 ref 前缀（对齐 raster 的 ``ref:raster/<id>`` 磁盘
#: cursor 先例；文件路径是实现细节，不进 LLM/前端）。
GEOPARQUET_REF_PREFIX = "ref:fabric-parquet/"

#: 会话/工件 id 白名单（路径段边界即拒绝 traversal/分隔符 —— 与 raster ref
#: 的 charset 纪律一致）。
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+\Z")

#: Durable DataObject 发布的字节预算（与 workspace snapshot 的 materialize
#: 默认预算同量级）：超预算文件仍落盘+登记台账，只是不做 BlobStore 复制，
#: durable="oversized" 诚实披露（绝不假装持久）。
_FABRIC_PARQUET_BLOB_PUBLISH_BUDGET_BYTES = 256 * 1024 * 1024


def _file_sha256(path: Path) -> str:
    """流式 sha256（O(payload) IO，无大内存驻留）。"""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _publish_parquet_object(
    path: Path,
    session_id: str,
    title: str,
    content_sha256: str,
    geo_summary: Dict[str, Any],
    feature_count: int,
):
    """GeoParquet 文件 → DataObject（blob + manifest，BlobStore CAS）。"""
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        publish_data_object,
    )

    return publish_data_object(
        {"data.parquet": path},
        kind="vector_parquet",
        owner_scope=normalize_owner_scope(session_id=session_id),
        payload={
            "feature_count": int(feature_count),
            **({"bbox": geo_summary["bbox"]} if "bbox" in geo_summary else {}),
            **({"crs": geo_summary["crs"]} if geo_summary.get("crs") is not None else {}),
            **({"geometry_types": geo_summary["geometry_types"]}
               if "geometry_types" in geo_summary else {}),
            "content_sha256": content_sha256,
            "title": title,
        },
        producer={"capability": "fabric.materialize", "tool": "materialize_geoparquet"},
        input_fingerprint=content_sha256,
    )


def _is_demo_source_type(source_type: Any) -> bool:
    """#767: source_type 是否解析到显式 demo/sample adapter。"""
    if not source_type or not isinstance(source_type, str):
        return False
    try:
        from app.services.data_fabric.registry import resolve_adapter_spec

        return bool(resolve_adapter_spec(source_type).is_demo)
    except Exception:
        return False


def _is_demo_adapter_source(query_result: QueryResult) -> bool:
    """V2：优先 QueryResult.is_demo（adapter 显式标注），V1 判定兜底。"""
    if getattr(query_result, "is_demo", False):
        return True
    return _is_demo_source_type((query_result.metadata or {}).get("source_type"))


class MaterializationService:
    """Data Fabric 物化管线（V2 单管线）。"""

    def execute_query(
        self,
        adapter: GeospatialDataSourceAdapter,
        dataset_id: str,
        query_spec: QuerySpec,
    ) -> QueryResult:
        """执行查询；V2 typed error 直接传播，V1 in-band 标记兜底转换。"""
        try:
            result = adapter.query(dataset_id, query_spec)
        except DataFabricError:
            raise
        except Exception as e:
            logger.error("[MaterializationService] query failed for '%s': %s", dataset_id, e)
            raise
        # V1 兼容：仍返回 in-band 标记的 adapter（如 WMS）→ typed error。
        err = error_from_query_result(result)
        if err is not None:
            logger.error("[MaterializationService] query failed for '%s': %s", dataset_id, err)
            raise err
        return result

    async def materialize(
        self,
        dataset_id: str,
        query_result: QueryResult,
        session_id: str = "default",
        layer_name: Optional[str] = None,
        *,
        output_format: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按 ResultMode 物化/直返（见模块 docstring）。

        ``output_format="geoparquet"`` 是显式 opt-in（Wave 5）：FEATURES/
        MATERIALIZE/SAMPLE 走 GeoParquet 磁盘工件 lane（``ref:fabric-parquet/
        <id>``）；缺省 None 时行为与此前完全一致（GeoJSON session ref）。
        轻量结果模式（statistics/descriptor/vector_tile）保持零物化直返，
        不受 format 影响。
        """
        layer_title = layer_name or f"Materialized Layer {dataset_id}"
        is_demo = _is_demo_adapter_source(query_result)
        evidence = (query_result.metadata or {}).get("query_evidence") or {}
        mode = query_result.result_mode or "features"

        # ---- 轻量结果模式：零物化直返 ----
        if mode in ("statistics", "descriptor", "vector_tile"):
            return {
                "status": "success",
                "success": True,
                "ref_id": None,
                "result_mode": mode,
                "dataset_id": dataset_id,
                "layer_name": layer_title,
                "feature_count": 0,
                "total_count": query_result.total_count or 0,
                "data": query_result.data,
                "fingerprint": None,
                "is_demo": is_demo,
                "schema_info": query_result.schema_info,
                "metadata": query_result.metadata,
                "query_evidence": evidence,
            }

        if output_format == "geoparquet":
            return await self._materialize_geoparquet_result(
                dataset_id, query_result, session_id, layer_title,
                is_demo, evidence, mode,
            )

        # ---- FEATURES / MATERIALIZE / SAMPLE：payload → ref（SAMPLE 有界直返
        # 特征 + ref 由调用方决定；这里统一物化以便地图/分析消费）----
        geojson_payload = {
            "type": "FeatureCollection",
            "features": query_result.features,
            "properties": {
                "dataset_id": dataset_id,
                "layer_name": layer_title,
                "total_count": query_result.total_count or len(query_result.features),
                "schema_info": query_result.schema_info,
                "result_mode": mode,
            },
        }
        # evidence 写入 FC metadata（lineage 供数；ADR-0092 artifact lineage
        # 的输入侧，不建第二 store）
        if evidence:
            geojson_payload["properties"]["query_evidence"] = evidence

        feature_count = len(query_result.features)
        total_count = query_result.total_count or feature_count
        fingerprint = await asyncio.to_thread(
            dataset_fingerprint_service.calculate_data_fingerprint,
            query_result.features,
        )

        # 资源守卫（Section 22 / #425）：入库前拒绝超限（服务器忽略 limit 时
        # 不得 OOM 进程）。
        enforce_result_bounds(query_result.features)

        try:
            ref_id = await session_data_manager.store(session_id, geojson_payload, prefix="data-fabric")
        except Exception as e:
            logger.error("[MaterializationService] store failed for '%s': %s", dataset_id, e)
            return self._failure(
                dataset_id, layer_title, feature_count, total_count,
                fingerprint, query_result,
                MaterializationFailedError(f"session store failed: {e}"),
            )

        if is_unavailable_ref(ref_id):
            logger.error(
                "[MaterializationService] store returned unavailable ref for '%s': %s",
                dataset_id, ref_id,
            )
            return self._failure(
                dataset_id, layer_title, feature_count, total_count,
                fingerprint, query_result,
                MaterializationFailedError("session store unavailable"),
            )

        # set_alias best-effort（失败不 invalidate ref）。
        try:
            await session_data_manager.set_alias(session_id, ref_id, layer_title)
        except Exception as ae:
            logger.warning(
                "[MaterializationService] set_alias failed (%s); ref '%s' still valid",
                ae, ref_id,
            )

        logger.info(
            "[MaterializationService] materialized '%s' -> ref_id %s (%d features, mode=%s)",
            dataset_id, ref_id, feature_count, mode,
        )
        return {
            "status": "success",
            "success": True,
            "ref_id": ref_id,
            "result_mode": mode,
            "dataset_id": dataset_id,
            "layer_name": layer_title,
            "feature_count": feature_count,
            "total_count": total_count,
            "total_matching": query_result.total_matching,
            "truncated": query_result.truncated,
            "next_cursor": query_result.next_cursor,
            "has_more": query_result.has_more,
            "fingerprint": fingerprint,
            "is_demo": is_demo,
            "schema_info": query_result.schema_info,
            "metadata": query_result.metadata,
            "query_evidence": evidence,
        }

    async def materialize_geoparquet(
        self,
        session_id: str,
        table: Any,
        title: str,
        *,
        row_group_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Arrow Table → GeoParquet 磁盘工件（V6：一等台账公民 + 内容身份）。

        落盘 ``<DATA_DIR>/<session>/fabric-geoparquet/<16hex>.parquet``
        （zstd，经 ``vector_carrier.table_to_geoparquet``），返回 ``ref
        = ref:fabric-parquet/<id>`` 与磁盘 ``path`` —— 对齐 raster PNG 磁盘
        ref 先例的最小诚实语义（路径不透明，ref 即 cursor）。

        V6（ADR-0118）闭合此前的 write-only 悬空引用：

        - **路径单点**：``artifact_registry.fabric_parquet_path`` 是唯一路径
          派生（本函数不再自带目录约定）；
        - **台账注册**：写后 ``register_artifact``（type=fabric_geoparquet，
          descriptor=真实 bbox/count/crs/digest）—— ref 进 ledger，GC/probe
          由此可见（注册失败降级日志，绝不阻断工具路径）；
        - **内容身份**：文件流式 sha256 恒计算；预算内（256MiB，与 workspace
          materialize 预算同量级）额外发布 DataObject（blobs+manifest 经
          BlobStore CAS —— durable identity，同内容重发布免费去重）；
          超预算诚实跳过（durable="oversized"），绝不假装。

        - pyarrow 缺失 → typed ``VectorCarrierUnavailable``（诚实降级：
          dict-lane ``materialize`` 缺省路径不受任何影响）；
        - session_id 过白名单校验（路径段边界拒绝 traversal）。
        """
        from app.services.artifact_registry import fabric_parquet_path
        from app.services.data_fabric.vector_carrier import (
            VectorCarrierUnavailable,
            arrow_available,
            table_geo_summary,
            table_to_geoparquet,
        )

        if not arrow_available():
            raise VectorCarrierUnavailable(
                "GeoParquet materialization requires the optional 'pyarrow' "
                "dependency; fall back to the GeoJSON dict-lane materialize "
                "or install pyarrow",
            )
        if not isinstance(session_id, str) or not _ID_RE.match(session_id):
            raise ValueError(f"invalid session id for disk artifact: {session_id!r}")
        artifact_id = secrets.token_hex(8)  # 16 hex
        ref = f"{GEOPARQUET_REF_PREFIX}{artifact_id}"
        path = fabric_parquet_path(session_id, ref)
        if path is None:  # 防御性（id 由本函数铸造，恒合法）
            raise ValueError(f"unresolvable fabric-parquet ref: {ref}")

        def _write() -> int:
            path.parent.mkdir(parents=True, exist_ok=True)
            table_to_geoparquet(
                table, str(path), compression="zstd", row_group_size=row_group_size,
            )
            return path.stat().st_size

        size = await asyncio.to_thread(_write)

        # 内容身份：流式 sha256（O(payload) IO，恒算 —— 内容寻址默认参与
        # DataObject 身份）；geo 摘要零额外几何扫描（schema 元数据读回）。
        content_sha256 = await asyncio.to_thread(_file_sha256, path)
        geo_summary = table_geo_summary(table)

        # Durable 发布（预算内）：blobs+manifest → BlobStore（CAS 去重）。
        data_object_id: Optional[str] = None
        manifest_location: Optional[str] = None
        durable_state = "published"
        if size <= _FABRIC_PARQUET_BLOB_PUBLISH_BUDGET_BYTES:
            try:
                identity = await asyncio.to_thread(
                    _publish_parquet_object,
                    path, session_id, title, content_sha256, geo_summary,
                    int(table.num_rows),
                )
                data_object_id = identity.data_object_id
                manifest_location = identity.manifest_location
            except Exception as e:  # noqa: BLE001 — durable 发布失败诚实降级
                durable_state = "failed"
                logger.warning(
                    "[MaterializationService] data object publish failed for "
                    "%s: %s", ref, e,
                )
        else:
            durable_state = "oversized"

        # 台账注册（best-effort；ref 成为 ledger 一等公民 —— GC/probe 可见）。
        try:
            from app.services.artifact_registry import register_artifact

            await register_artifact(
                session_id,
                artifact_id=ref,
                artifact_type="fabric_geoparquet",
                producer_capability="fabric.materialize",
                producer_tool="materialize_geoparquet",
                descriptor={
                    "feature_count": int(table.num_rows),
                    **({"bbox": geo_summary["bbox"]} if "bbox" in geo_summary else {}),
                    **({"crs": geo_summary["crs"]} if isinstance(geo_summary.get("crs"), str) else {}),
                },
                metadata={
                    "content_sha256": content_sha256,
                    "byte_size": int(size),
                    **({"data_object_id": data_object_id} if data_object_id else {}),
                    "storage": "disk-cursor",
                },
            )
        except Exception as e:  # noqa: BLE001 — 注册是增值记录，绝不阻断
            logger.warning(
                "[MaterializationService] ledger registration skipped for %s: %s",
                ref, e,
            )

        logger.info(
            "[MaterializationService] geoparquet artifact '%s' -> %s (%d bytes, "
            "durable=%s)",
            title, path, size, durable_state,
        )
        return {
            "status": "success",
            "success": True,
            "ref": ref,
            "path": str(path),
            "title": title,
            "format": "geoparquet",
            "feature_count": int(table.num_rows),
            "bytes": int(size),
            "content_sha256": content_sha256,
            **({"data_object_id": data_object_id} if data_object_id else {}),
            "durable": durable_state,
            **({"manifest": manifest_location} if manifest_location else {}),
        }

    async def _materialize_geoparquet_result(
        self,
        dataset_id: str,
        query_result: QueryResult,
        session_id: str,
        layer_title: str,
        is_demo: bool,
        evidence: Dict[str, Any],
        mode: str,
    ) -> Dict[str, Any]:
        """``materialize(output_format="geoparquet")`` 分支：features → 载体
        表 → 磁盘工件（一次 Arrow 编码，杜绝 GeoJSON 再序列化）。"""
        from app.services.data_fabric.vector_carrier import (
            features_to_arrow,
        )

        features = query_result.features
        feature_count = len(features)
        total_count = query_result.total_count or feature_count

        # 资源守卫与 dict lane 同一红线（Section 22 / #425）。
        enforce_result_bounds(features)

        fingerprint = await asyncio.to_thread(
            dataset_fingerprint_service.calculate_data_fingerprint,
            features,
        )
        schema_info = query_result.schema_info if isinstance(query_result.schema_info, dict) else {}
        crs = schema_info.get("crs") or None
        try:
            table = await asyncio.to_thread(features_to_arrow, features, crs=crs)
        except DataFabricError:
            raise  # VectorCarrierUnavailable / EncodeError 原样（typed）
        except Exception as e:
            raise MaterializationFailedError(f"GeoParquet encode failed: {e}") from e
        try:
            written = await self.materialize_geoparquet(session_id, table, layer_title)
        except DataFabricError:
            raise
        except Exception as e:
            return self._failure(
                dataset_id, layer_title, feature_count, total_count,
                fingerprint, query_result,
                MaterializationFailedError(f"GeoParquet write failed: {e}"),
            )
        metadata = dict(query_result.metadata or {})
        metadata["materialization_format"] = "geoparquet"
        return {
            "status": "success",
            "success": True,
            "ref": written["ref"],
            "ref_id": None,  # session-store ref 不产生（磁盘工件 lane）
            "artifact_ref": written["ref"],
            "path": written["path"],
            "format": "geoparquet",
            "result_mode": mode,
            "dataset_id": dataset_id,
            "layer_name": layer_title,
            "feature_count": feature_count,
            "total_count": total_count,
            "total_matching": query_result.total_matching,
            "truncated": query_result.truncated,
            "has_more": query_result.has_more,
            "fingerprint": fingerprint,
            "is_demo": is_demo,
            "schema_info": query_result.schema_info,
            "metadata": metadata,
            "query_evidence": evidence,
        }

    @staticmethod
    def _failure(
        dataset_id: str,
        layer_title: str,
        feature_count: int,
        total_count: int,
        fingerprint: Optional[str],
        query_result: QueryResult,
        err: DataFabricError,
    ) -> Dict[str, Any]:
        """真实失败结果（无 ref、无 success）。"""
        d = err.to_dict()
        return {
            "status": "failed",
            "success": False,
            "ref_id": None,
            "dataset_id": dataset_id,
            "layer_name": layer_title,
            "feature_count": feature_count,
            "total_count": total_count,
            "fingerprint": fingerprint,
            "is_demo": _is_demo_adapter_source(query_result),
            "schema_info": query_result.schema_info,
            "metadata": query_result.metadata,
            "error_type": d["error_type"],
            "error": d["error"],
        }

    async def materialize_dataset(
        self,
        adapter: GeospatialDataSourceAdapter,
        dataset_id: str,
        query_spec: Optional[QuerySpec] = None,
        session_id: str = "default",
        layer_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """查询 + 物化统一管线（阻塞远端经 to_thread 下放事件循环外）。"""
        spec = query_spec or QuerySpec(limit=100)
        layer_title = layer_name or f"Materialized Layer {dataset_id}"
        try:
            query_result = await asyncio.to_thread(self.execute_query, adapter, dataset_id, spec)
        except DataFabricError as e:
            logger.error(
                "[MaterializationService] materialize query failed for '%s': %s",
                dataset_id, e,
            )
            return {
                "status": "failed",
                "success": False,
                "ref_id": None,
                "dataset_id": dataset_id,
                "layer_name": layer_title,
                "feature_count": 0,
                "total_count": 0,
                "fingerprint": None,
                "is_demo": False,
                "schema_info": {},
                "metadata": {},
                "error_type": e.code,
                "error": str(e),
            }
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "[MaterializationService] materialize query crashed for '%s': %s",
                dataset_id, e,
            )
            return {
                "status": "failed",
                "success": False,
                "ref_id": None,
                "dataset_id": dataset_id,
                "layer_name": layer_title,
                "feature_count": 0,
                "total_count": 0,
                "fingerprint": None,
                "is_demo": False,
                "schema_info": {},
                "metadata": {},
                "error_type": MaterializationFailedError.code,
                "error": f"query execution failed: {e}",
            }
        return await self.materialize(
            dataset_id, query_result, session_id=session_id, layer_name=layer_name
        )


# Global singleton instance
materialization_service = MaterializationService()
