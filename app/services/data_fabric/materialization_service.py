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
    ) -> Dict[str, Any]:
        """按 ResultMode 物化/直返（见模块 docstring）。"""
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
