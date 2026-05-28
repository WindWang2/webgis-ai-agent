"""Explorer Celery task chain"""
import logging
import asyncio
import zlib
import json
from app.services.task_queue import celery_app
from app.services.explorer.models import SearchContext, RawContent
from app.adapters.gov.gov_data_adapter import GovDataAdapter
from app.adapters.base import DataSource

logger = logging.getLogger(__name__)


def _store_ref(data: dict, prefix: str = "explorer") -> str:
    """存储数据到 session manager，返回 ref_id"""
    from app.services.session_data import session_data_manager
    # asyncio.run() is safe here for Celery prefork workers; breaks with gevent/eventlet concurrency
    ref_id = asyncio.run(session_data_manager.store("explorer", data, prefix=prefix))
    return ref_id


def _load_ref(ref_id: str):
    """从 session manager 加载数据"""
    from app.services.session_data import session_data_manager
    # asyncio.run() is safe here for Celery prefork workers; breaks with gevent/eventlet concurrency
    return asyncio.run(session_data_manager.get("explorer", ref_id))


@celery_app.task(bind=True, max_retries=2, soft_time_limit=30, time_limit=30)
def explorer_discover_task(self, task_id: str, query: str, context: dict):
    """数据发现阶段"""
    logger.info(f"[Explorer:{task_id}] Starting discover stage")
    self.update_state(state="PROGRESS", meta={"stage": "discover", "progress": 10})

    try:
        ctx = SearchContext(**context)
        adapter = GovDataAdapter()

        # 发现数据源
        sources = asyncio.run(adapter.discover(query, ctx))

        # 质量预评估
        scored = []
        for source in sources[:3]:  # top-3
            score = asyncio.run(adapter.quick_assess(query, source))
            scored.append({
                "source": source.model_dump(),
                "score": score.model_dump(),
            })

        scored.sort(key=lambda x: x["score"]["overall"], reverse=True)

        self.update_state(state="PROGRESS", meta={"stage": "discover", "progress": 100})

        return {
            "task_id": task_id,
            "selected_sources": scored,
        }

    except Exception as e:
        logger.error(f"[Explorer:{task_id}] Discover failed: {e}")
        raise self.retry(exc=e, countdown=2 ** self.request.retries)


@celery_app.task(bind=True, max_retries=1, soft_time_limit=55, time_limit=60)
def explorer_fetch_task(self, prev_result: dict):
    """内容抓取阶段"""
    task_id = prev_result["task_id"]
    logger.info(f"[Explorer:{task_id}] Starting fetch stage")
    self.update_state(state="PROGRESS", meta={"stage": "fetch", "progress": 10})

    adapter = GovDataAdapter()
    sources_data = prev_result.get("selected_sources", [])

    results = []
    for item in sources_data:
        source_dict = item["source"]
        source = DataSource(**source_dict)

        try:
            raw = asyncio.run(adapter.fetch(source))
            # 存储原始数据 (hex encode bytes for JSON serialization)
            ref_id = _store_ref({
                "data": raw.data.hex(),
                "content_type": raw.content_type,
                "encoding": raw.encoding,
            }, prefix="fetch")

            results.append({
                "source_id": source.id,
                "ref_id": ref_id,
                "size_bytes": len(raw.data),
                "format": source.format,
            })
        except Exception as e:
            logger.warning(f"[Explorer:{task_id}] Fetch failed for {source.id}: {e}")
            results.append({
                "source_id": source.id,
                "error": str(e),
            })

    successful = [r for r in results if "ref_id" in r]
    if not successful:
        raise RuntimeError(f"All source fetches failed: {results}")

    self.update_state(state="PROGRESS", meta={"stage": "fetch", "progress": 100})

    return {
        "task_id": task_id,
        "fetch_results": successful,
    }


@celery_app.task(bind=True, soft_time_limit=55, time_limit=60)
def explorer_parse_task(self, prev_result: dict):
    """结构化解析阶段"""
    task_id = prev_result["task_id"]
    logger.info(f"[Explorer:{task_id}] Starting parse stage")
    self.update_state(state="PROGRESS", meta={"stage": "parse", "progress": 10})

    adapter = GovDataAdapter()
    fetch_results = prev_result["fetch_results"]

    parsed_all = []
    for result in fetch_results:
        ref_id = result["ref_id"]
        stored = _load_ref(ref_id)

        if not stored:
            logger.warning(f"[Explorer:{task_id}] Ref {ref_id} not found")
            continue

        # 重建 RawContent
        raw = RawContent(
            data=bytes.fromhex(stored["data"]),
            content_type=stored["content_type"],
            encoding=stored["encoding"],
        )

        structured = asyncio.run(adapter.parse(raw))

        # 字段映射（简化：自动匹配常见字段名）
        mapping = _auto_field_mapping(structured.fields)
        confidence = _mapping_confidence(mapping)

        parsed_ref = _store_ref({
            "rows": structured.rows,
            "fields": [f.model_dump() for f in structured.fields],
            "mapping": mapping,
        }, prefix="parsed")

        parsed_all.append({
            "source_id": result["source_id"],
            "ref_id": parsed_ref,
            "row_count": len(structured.rows),
            "mapping": mapping,
            "confidence": confidence,
        })

    self.update_state(state="PROGRESS", meta={"stage": "parse", "progress": 100})

    return {
        "task_id": task_id,
        "parsed_results": parsed_all,
    }


@celery_app.task(bind=True, max_retries=2, soft_time_limit=290, time_limit=300)
def explorer_geocode_task(self, prev_result: dict):
    """地理编码阶段"""
    from app.tools.chinese_maps import batch_geocode_cn

    task_id = prev_result["task_id"]
    logger.info(f"[Explorer:{task_id}] Starting geocode stage")
    self.update_state(state="PROGRESS", meta={"stage": "geocode", "progress": 0})

    parsed_results = prev_result["parsed_results"]
    total_rows = sum(r["row_count"] for r in parsed_results)

    if total_rows == 0:
        return {"task_id": task_id, "geocoded_ref_id": None, "total_rows": 0, "success_rate": 0.0}

    providers = ["amap", "baidu", "tianditu"]
    all_geocoded = []
    processed = 0
    multi_provider = False

    for parsed in parsed_results:
        data = _load_ref(parsed["ref_id"])
        if not data:
            processed += parsed["row_count"]
            continue

        rows = data["rows"]
        mapping = data.get("mapping", {})
        address_field = mapping.get("address", "address")
        lat_field = mapping.get("lat")
        lon_field = mapping.get("lon")

        # Collect rows that need geocoding
        chunk = []  # List of (row_index, address_string)
        for idx, row in enumerate(rows):
            has_lat = lat_field is not None and row.get(lat_field) is not None
            has_lon = lon_field is not None and row.get(lon_field) is not None
            if has_lat and has_lon:
                row["_lat"] = row[lat_field]
                row["_lon"] = row[lon_field]
                row["_geocode_status"] = "predefined"
                row["_geocode_provider"] = None
                row["_geocode_error"] = None
            else:
                address = row.get(address_field)
                if address:
                    chunk.append((idx, str(address)))
                else:
                    row["_lat"] = None
                    row["_lon"] = None
                    row["_geocode_status"] = "failed"
                    row["_geocode_provider"] = None
                    row["_geocode_error"] = "missing address"
            all_geocoded.append(row)

        # Process in batches of 100
        for batch_start in range(0, len(chunk), 100):
            batch = chunk[batch_start:batch_start + 100]
            pending = list(range(len(batch)))  # indices into batch
            provider_idx = 0

            while pending and provider_idx < len(providers):
                provider = providers[provider_idx]
                addresses = [batch[i][1] for i in pending]

                result = asyncio.run(batch_geocode_cn(addresses, provider=provider, max_concurrency=3))

                # Handle complete provider failure
                if "error" in result and not result.get("results") and not result.get("errors"):
                    provider_idx += 1
                    multi_provider = True
                    continue

                # Map results back by index within the current addresses list
                # result["index"] maps to position in `addresses`, translate back to batch index
                success_by_idx = {}
                for r in result.get("results", []):
                    batch_idx = pending[r["index"]]
                    success_by_idx[batch_idx] = r
                error_by_idx = {}
                for e in result.get("errors", []):
                    batch_idx = pending[e["index"]]
                    error_by_idx[batch_idx] = e

                failed_this_attempt = []
                for p_idx in pending:
                    if p_idx in success_by_idx:
                        r = success_by_idx[p_idx]
                        row_idx = batch[p_idx][0]
                        row = rows[row_idx]

                        # Extract coordinates
                        lat = None
                        lon = None
                        results_list = r.get("results")
                        if results_list and len(results_list) > 0:
                            loc = results_list[0].get("location")
                            if loc and len(loc) == 2:
                                lon, lat = loc[0], loc[1]
                        if lat is None:
                            lat = r.get("lat")
                        if lon is None:
                            lon = r.get("lon")

                        if lat is not None and lon is not None:
                            row["_lat"] = lat
                            row["_lon"] = lon
                            row["_geocode_status"] = "ok"
                            row["_geocode_provider"] = provider
                            row["_geocode_error"] = None
                        else:
                            failed_this_attempt.append(p_idx)
                    else:
                        failed_this_attempt.append(p_idx)

                failure_rate = len(failed_this_attempt) / len(pending) if pending else 0
                if failure_rate > 0.30 and failed_this_attempt and provider_idx < len(providers) - 1:
                    multi_provider = True
                    pending = failed_this_attempt
                    provider_idx += 1
                else:
                    # Mark remaining as failed
                    for p_idx in failed_this_attempt:
                        row_idx = batch[p_idx][0]
                        row = rows[row_idx]
                        row["_lat"] = None
                        row["_lon"] = None
                        row["_geocode_status"] = "failed"
                        row["_geocode_provider"] = provider
                        if provider_idx == len(providers) - 1:
                            row["_geocode_error"] = "all_providers_failed"
                        elif p_idx in error_by_idx:
                            row["_geocode_error"] = error_by_idx[p_idx].get("error", "unknown error")
                        else:
                            row["_geocode_error"] = "no response"
                    pending = []

            # If we exhausted all providers and still have pending, mark all as failed
            for p_idx in pending:
                row_idx = batch[p_idx][0]
                row = rows[row_idx]
                row["_lat"] = None
                row["_lon"] = None
                row["_geocode_status"] = "failed"
                row["_geocode_provider"] = providers[-1] if providers else None
                row["_geocode_error"] = "all_providers_failed"

        processed += len(rows)
        progress = int(processed / total_rows * 100)
        self.update_state(state="PROGRESS", meta={"stage": "geocode", "progress": progress})

    total = len(all_geocoded)
    success = sum(1 for r in all_geocoded if r.get("_geocode_status") == "ok")
    failed = sum(1 for r in all_geocoded if r.get("_geocode_status") == "failed")
    predefined = sum(1 for r in all_geocoded if r.get("_geocode_status") == "predefined")
    to_geocode = total - predefined
    success_rate = round(success / to_geocode, 4) if to_geocode > 0 else 0.0

    result_ref = _store_ref({
        "rows": all_geocoded,
        "summary": {
            "total": total,
            "success": success,
            "failed": failed,
            "predefined": predefined,
            "success_rate": success_rate,
            "multi_provider": multi_provider,
        }
    }, prefix="geocoded")

    self.update_state(state="PROGRESS", meta={"stage": "geocode", "progress": 100})

    return {
        "task_id": task_id,
        "geocoded_ref_id": result_ref,
        "total_rows": total,
        "success_rate": success_rate,
    }


@celery_app.task(bind=True, soft_time_limit=25, time_limit=30)
def explorer_validate_task(self, prev_result: dict):
    """质量验证阶段"""
    task_id = prev_result["task_id"]
    logger.info(f"[Explorer:{task_id}] Starting validate stage")

    geocoded_ref_id = prev_result.get("geocoded_ref_id")
    total_rows = prev_result.get("total_rows", 0)

    return {
        "task_id": task_id,
        "status": "completed",
        "geocoded_ref_id": geocoded_ref_id,
        "total_rows": total_rows,
    }


def _auto_field_mapping(fields: list) -> dict:
    """自动字段映射"""
    mapping = {}
    name_patterns = ["name", "名称", "title", "标题"]
    address_patterns = ["address", "地址", "addr", "location", "位置"]
    lat_patterns = ["lat", "latitude", "纬度", "y"]
    lon_patterns = ["lon", "lng", "longitude", "经度", "x"]

    for field in fields:
        fname = field.name.lower()
        if any(p in fname for p in name_patterns):
            mapping["name"] = field.name
        elif any(p in fname for p in address_patterns):
            mapping["address"] = field.name
        elif any(p in fname for p in lat_patterns):
            mapping["lat"] = field.name
        elif any(p in fname for p in lon_patterns):
            mapping["lon"] = field.name

    return mapping


def _mapping_confidence(mapping: dict) -> float:
    """计算字段映射置信度"""
    required = ["name", "address"]
    matched = sum(1 for k in required if k in mapping)
    return round(matched / len(required), 4)
