"""
Explorer Pipeline Engine — Pure in-process 5-stage GIS exploration runner.
"""
import logging
from typing import Any, Callable, Dict, Optional

from app.services.explorer.discover_stage import run_discover_stage
from app.services.explorer.fetch_stage import run_fetch_stage
from app.services.explorer.geocode_stage import geocode_stage
from app.services.explorer.models import SearchContext, StageResult
from app.services.explorer.parse_stage import run_parse_stage
from app.services.explorer.validate_stage import run_validate_stage
from app.services.task_tracker import TaskTracker
from app.tools.chinese_maps import batch_geocode_cn

logger = logging.getLogger(__name__)


class ExplorerPipelineRunner:
    """Deep pipeline engine encapsulating 5-stage execution and step logging."""

    def __init__(self, tracker: Optional[TaskTracker] = None) -> None:
        self.tracker = tracker or TaskTracker()

    async def execute(
        self,
        task_id: str,
        query: str,
        context: SearchContext,
        session_id: str = "",
        adapter: Optional[Any] = None,
        load_ref: Optional[Callable[[str], Any]] = None,
        store_ref: Optional[Callable[[dict, str], str]] = None,
        on_progress: Optional[Callable[[str, int], None]] = None,
    ) -> StageResult:
        """
        Execute all 5 exploration stages sequentially (discover -> fetch -> parse -> geocode -> validate)
        with automatic TaskTracker logging and error boundary recovery.
        """
        in_memory_refs: Dict[str, Any] = {}

        def default_store(data: dict, prefix: str) -> str:
            ref_id = f"ref:{prefix}:{len(in_memory_refs) + 1}"
            in_memory_refs[ref_id] = data
            return ref_id

        def default_load(ref_id: str) -> Any:
            return in_memory_refs.get(ref_id)

        _store = store_ref or default_store
        _load = load_ref or default_load

        # 1. Discover Stage
        async with self.tracker.track_step(task_id, "explorer_discover", {"query": query}):
            logger.info(f"[ExplorerPipelineRunner:{task_id}] Stage 1: Discover")
            discover_res = await run_discover_stage(
                task_id, query, context.model_dump(), adapter=adapter,
                on_progress=lambda p: on_progress("discover", p) if on_progress else None,
            )
            if not discover_res.success:
                return discover_res

        # 2. Fetch Stage
        async with self.tracker.track_step(task_id, "explorer_fetch", {}):
            logger.info(f"[ExplorerPipelineRunner:{task_id}] Stage 2: Fetch")
            selected_sources = discover_res.data.get("selected_sources", []) if discover_res.data else []
            fetch_res = await run_fetch_stage(
                task_id, selected_sources, adapter=adapter, store_ref=_store,
                on_progress=lambda p: on_progress("fetch", p) if on_progress else None,
            )
            if not fetch_res.success:
                return fetch_res

        # 3. Parse Stage
        async with self.tracker.track_step(task_id, "explorer_parse", {}):
            logger.info(f"[ExplorerPipelineRunner:{task_id}] Stage 3: Parse")
            fetch_results = fetch_res.data.get("fetch_results", []) if fetch_res.data else []
            # #774: per-source fetch failures ride along to the final summary.
            fetch_errors = fetch_res.data.get("fetch_errors", []) if fetch_res.data else []
            parse_res = await run_parse_stage(
                task_id, fetch_results, adapter=adapter, load_ref=_load, store_ref=_store,
                on_progress=lambda p: on_progress("parse", p) if on_progress else None,
            )
            if not parse_res.success:
                return parse_res

        # 4. Geocode Stage
        async with self.tracker.track_step(task_id, "explorer_geocode", {}):
            logger.info(f"[ExplorerPipelineRunner:{task_id}] Stage 4: Geocode")
            parsed_results = parse_res.data.get("parsed_results", []) if parse_res.data else []
            _bg_func = getattr(adapter, "batch_geocode", None) or batch_geocode_cn
            geo_stage_res = await geocode_stage(
                parsed_results,
                load_ref=_load,
                batch_geocode=_bg_func,
                on_progress=lambda p: on_progress("geocode", p) if on_progress else None,
            )
            if geo_stage_res.rows or geo_stage_res.summary.total:
                geocoded_ref_id = _store(
                    {"rows": geo_stage_res.rows, "summary": geo_stage_res.summary.as_dict()},
                    prefix="geocoded",
                )
            else:
                geocoded_ref_id = None

        # 5. Validate Stage
        async with self.tracker.track_step(task_id, "explorer_validate", {}):
            logger.info(f"[ExplorerPipelineRunner:{task_id}] Stage 5: Validate")
            validate_res = await run_validate_stage(
                task_id,
                geocoded_ref_id=geocoded_ref_id,
                total_rows=geo_stage_res.summary.total,
                fetch_errors=fetch_errors,
                on_progress=lambda p: on_progress("validate", p) if on_progress else None,
                # #776: in-process 路径同样桥接进 chat session 命名空间。
                session_id=session_id,
            )

            return validate_res


class ExplorerPipeline:
    """Backward-compatible wrapper delegating to ExplorerPipelineRunner."""

    def __init__(self, tracker: Optional[TaskTracker] = None) -> None:
        self._runner = ExplorerPipelineRunner(tracker=tracker)

    async def run_in_process(
        self,
        task_id: str,
        query: str,
        context: SearchContext,
        session_id: str = "",
        adapter: Optional[Any] = None,
        load_ref: Optional[Callable[[str], Any]] = None,
        store_ref: Optional[Callable[[dict, str], str]] = None,
        on_progress: Optional[Callable[[str, int], None]] = None,
    ) -> StageResult:
        return await self._runner.execute(
            task_id=task_id,
            query=query,
            context=context,
            session_id=session_id,
            adapter=adapter,
            load_ref=load_ref,
            store_ref=store_ref,
            on_progress=on_progress,
        )


__all__ = ["ExplorerPipelineRunner", "ExplorerPipeline"]
