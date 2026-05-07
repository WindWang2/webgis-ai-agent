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
    ref_id = session_data_manager.store("explorer", data, prefix=prefix)
    return ref_id


def _load_ref(ref_id: str):
    """从 session manager 加载数据"""
    from app.services.session_data import session_data_manager
    return session_data_manager.get("explorer", ref_id)


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


@celery_app.task(bind=True, soft_time_limit=290, time_limit=300)
def explorer_geocode_task(self, prev_result: dict):
    """地理编码阶段"""
    task_id = prev_result["task_id"]
    logger.info(f"[Explorer:{task_id}] Starting geocode stage")
    self.update_state(state="PROGRESS", meta={"stage": "geocode", "progress": 0})

    parsed_results = prev_result["parsed_results"]
    total_rows = sum(r["row_count"] for r in parsed_results)

    if total_rows == 0:
        return {"task_id": task_id, "geocoded_ref_id": None, "success_rate": 0.0}

    # 简化实现：标记为待编码
    all_geocoded = []
    processed = 0

    for parsed in parsed_results:
        data = _load_ref(parsed["ref_id"])
        if not data:
            continue

        rows = data["rows"]
        mapping = data.get("mapping", {})
        address_field = mapping.get("address", "address")

        for row in rows:
            row["_lat"] = None
            row["_lon"] = None
            row["_geocode_status"] = "pending"

        all_geocoded.extend(rows)
        processed += len(rows)

        progress = int(processed / total_rows * 100)
        self.update_state(state="PROGRESS", meta={"stage": "geocode", "progress": progress})

    # 存储结果
    result_ref = _store_ref({"rows": all_geocoded}, prefix="geocoded")

    self.update_state(state="PROGRESS", meta={"stage": "geocode", "progress": 100})

    return {
        "task_id": task_id,
        "geocoded_ref_id": result_ref,
        "total_rows": len(all_geocoded),
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
