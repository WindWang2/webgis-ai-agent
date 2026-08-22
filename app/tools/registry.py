"""FC 工具注册中心"""
import asyncio
import contextvars
import inspect
import json
import logging
import os
from contextlib import contextmanager
from typing import Any, Callable, Optional, Type, List
from pydantic import BaseModel, create_model, ValidationError

from enum import Enum

from app.services.session_data import session_data_manager
from app.lib.geo_processor.core import GeoAnalysisResult

from app.services.jobs.cancellation import OperationCancelled
from app.services.llm_result_formatter import is_error_like_result

logger = logging.getLogger(__name__)

# SEC-F1: tier-3 (destructive / RCE-class) tools may only be dispatched through
# an execution context that explicitly confirmed them. Route-level checks
# (chat /tools/execute confirm_destructive, Pi-bridge rejection) are NOT a
# chokepoint: workflow execution, subagents and plan-mode steps all reach
# registry.dispatch directly. Default False → those paths are refused here.
_allow_tier3_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "allow_tier3_tools", default=False
)


def tier3_confirmed() -> bool:
    """Whether the current execution context carries an explicit tier-3 confirmation."""
    return _allow_tier3_var.get()


@contextmanager
def confirm_tier3():
    """Grant tier-3 dispatch rights for the enclosed (synchronous) scope.

    Must wrap the await of dispatch() in the same asyncio task — ContextVar
    tokens propagate into coroutines but not across create_task boundaries.
    """
    token = _allow_tier3_var.set(True)
    try:
        yield
    finally:
        _allow_tier3_var.reset(token)


class ToolExecutionPolicy(str, Enum):
    INLINE = "inline"    # <5ms, immediate execution in event loop task (state mutation/meta/UI JSON response)
    ASYNC = "async"      # Genuine non-blocking async I/O (httpx, session_data_manager, async DB)
    THREAD = "thread"    # Sync blocking file I/O or fast CPU/Shapely operations via asyncio.to_thread + semaphore
    CELERY = "celery"    # Heavy GDAL, raster warps, large spatial joins, KDE/IDW surfaces via Celery background tasks

# 同步工具并发上限（见 _dispatch_impl 的 to_thread 路径）。GIL 下 CPU-bound
# 工具超过核数无收益；给足核数余量 + 小缓冲，避免并行工具波互相拖累。
_TOOL_THREAD_LIMIT = max(4, min(16, (os.cpu_count() or 4) + 4))
_tool_thread_semaphore = asyncio.Semaphore(_TOOL_THREAD_LIMIT)

# 单次工具执行的墙钟预算（秒）。默认 300s，TOOL_TIMEOUT_S 环境变量可覆盖；
# 注册时工具声明了 timeout 元数据则优先于此处（见 _dispatch_impl）。注意
# INLINE 同步工具直接在事件循环上执行，真挂死时预算也无法抢占（循环被
# 阻塞）——该限制是既有约束，INLINE 只允许 <5ms 的超轻量工具。
_TOOL_TIMEOUT_S = float(os.environ.get("TOOL_TIMEOUT_S", "300"))

# to_thread 的 worker 线程无法被终止：调用方被取消/超时后线程仍会跑完。
# 这类「比调用方活得更久」的线程计数（诊断 + 测试断言用）。信号量槽位由
# done 回调在线程真实结束时归还（见 _execute_sync_in_thread），不在此计数。
_tool_thread_leaked_count = 0

VALID_GEOMETRY_TYPES = {
    "Point", "MultiPoint",
    "LineString", "MultiLineString",
    "Polygon", "MultiPolygon",
    "GeometryCollection",
}
VALID_GEOJSON_TYPES = VALID_GEOMETRY_TYPES | {"Feature", "FeatureCollection"}


# #677: node-budgeted estimator — width-unbounded payloads (100k features)
# would otherwise visit every leaf on the event loop (~1M nodes, ~0.7s).
# Budget caps total visited nodes; once exhausted the walker samples the
# average cost of visited items and extrapolates the remainder, so large
# payloads return an approximate byte estimate in O(budget) time. Exact
# for small/medium payloads, approximate (but traceable via budget) for
# huge ones — metrics use only, correctness never depends on it.
_ESTIMATE_MAX_NODES = 20_000
_ESTIMATE_SIZE_LIMIT = 262_144  # 256 KB — the cache/validation gate threshold

# ContextVar to share the single args-size probe between registry and the
# cached_tool wrapper (tool_cache.make_cache_key), so the same large args
# dict is not walked 2-3 times per dispatch. Set in dispatch(), read in
# make_cache_key(); fallback to a fresh walk when not set.
_arg_size_hint_var: contextvars.ContextVar[tuple[int, bool] | None] = (
    contextvars.ContextVar("_arg_size_hint", default=None)
)


def _estimate_json_bytes(
    obj: Any, _depth: int = 0, _budget: list[int] | None = None
) -> int:
    """Cheap structural estimate of the JSON byte length of ``obj``.

    PERF-01: ``json.dumps`` of a large tool result (e.g. a 10k-feature
    GeoJSON FeatureCollection) just to record a byte metric duplicates the
    serialization the dispatch path already performs. This walker estimates
    the serialized size without materializing the full string — accurate to
    within a few percent for typical JSON and bounded by ``_depth`` to avoid
    pathological cycles. Used only for metrics; never for correctness.

    #677: additionally bounded by a total node budget (default
    ``_ESTIMATE_MAX_NODES``). When the budget is exhausted the walker stops
    visiting new nodes and extrapolates from the sampled average, so a
    100k-feature payload costs O(budget) rather than O(features). If the
    caller passes an explicit ``_budget`` list, ``_budget[0] <= 0`` after
    the call indicates the result is an approximation (budget hit).
    """
    if _budget is None:
        _budget = [_ESTIMATE_MAX_NODES]
    if _budget[0] <= 0:
        return 0
    _budget[0] -= 1
    if _depth > 12:
        return 64  # deep nested: stop walking, small placeholder
    if obj is None:
        return 4
    if isinstance(obj, bool):
        return 4 if obj else 5
    if isinstance(obj, (int, float)):
        return len(str(obj))
    if isinstance(obj, str):
        # +2 for the quotes; escape overhead is minor for typical strings.
        return len(obj) + 2
    if isinstance(obj, dict):
        # {"k":v,...} → 2 braces + per-entry overhead (4: `","` and `:`).
        total = 2
        first = True
        items = list(obj.items())
        sampled = 0
        for k, v in items:
            if _budget[0] <= 0:
                remaining = len(items) - sampled
                if sampled > 0:
                    avg = (total - 2) / sampled
                    total += int(avg * remaining)
                else:
                    total += remaining * 16
                break
            if not first:
                total += 1  # comma
            first = False
            total += len(str(k)) + 4 + _estimate_json_bytes(v, _depth + 1, _budget)
            sampled += 1
        return total
    if isinstance(obj, (list, tuple)):
        total = 2
        first = True
        n = len(obj)
        if n == 0:
            return total
        sampled = 0
        # For large lists (features), sample until budget exhausted then
        # extrapolate via average per-item cost.
        for item in obj:
            if _budget[0] <= 0:
                remaining = n - sampled
                if sampled > 0:
                    # average cost per sampled item (including comma)
                    avg = (total - 2) / sampled if sampled else 8
                    total += int(avg * remaining) + remaining  # commas for remainder
                else:
                    total += remaining * 8
                break
            if not first:
                total += 1
            first = False
            total += _estimate_json_bytes(item, _depth + 1, _budget)
            sampled += 1
        return total
    # Fallback: stringify (rare; non-JSON-native types default-str in dumps).
    try:
        return len(str(obj))
    except Exception:
        return 32


# audit #824: 别名批量查表的去重后字段上限 —— 超限（或 oversized 载荷）降级为
# 仅解析显式 ref: 前缀，避免把内联大 GeoJSON 的海量字符串叶塞进一条 HMGET。
_ALIAS_LOOKUP_MAX_DISTINCT = 1024


def _is_args_oversized(arguments: Any) -> bool:
    """#699 + #677：超大 args 的统一预算化门（Pydantic 旁路与 GeoJSON 校验共用）。

    实证结论：pydantic-core 的 SchemaSerializer.to_python/model_dump 持 GIL 做
    整树深拷贝（100k 要素 FC 实测 dump ~104ms、validate ~0ms；cProfile 累计
    ~220ms），to_thread 实测 tick 仍 ~139ms 未释放 GIL，故选 (b) 大载荷旁路
    而非 (a) 线程化。hints 复用 #677 的单次预算化估计，阈值与
    _ESTIMATE_SIZE_LIMIT 对齐。
    """
    _hint = _arg_size_hint_var.get()
    if _hint is not None:
        _hint_bytes, _hint_over = _hint
        if _hint_over:
            return True
        # hint 小但经 ref 展开后可能膨胀 —— 对当前 arguments 预算化复核
        _cur_budget: list[int] = [_ESTIMATE_MAX_NODES]
        _cur_est = _estimate_json_bytes(arguments, _budget=_cur_budget)
        return _cur_budget[0] <= 0 or _cur_est > _ESTIMATE_SIZE_LIMIT
    _nb: list[int] = [_ESTIMATE_MAX_NODES]
    _ne = _estimate_json_bytes(arguments, _budget=_nb)
    return _nb[0] <= 0 or _ne > _ESTIMATE_SIZE_LIMIT


def validate_geojson_structure(obj: Any) -> None:
    """GeoJSON 结构校验辅助函数 (BE-AUDIT-08)。
    在调用空间分析等工具函数前校验参数中的 GeoJSON 几何/要素/要素集合结构。
    """
    if not isinstance(obj, (dict, list)):
        return

    if isinstance(obj, list):
        for item in obj:
            validate_geojson_structure(item)
        return

    if isinstance(obj, dict):
        obj_type = obj.get("type")
        if isinstance(obj_type, str) and obj_type in VALID_GEOJSON_TYPES:
            if obj_type == "FeatureCollection":
                if "features" not in obj:
                    raise ValueError("GeoJSON FeatureCollection 缺少必需的 'features' 字段")
                if not isinstance(obj["features"], list):
                    raise ValueError("GeoJSON FeatureCollection 的 'features' 字段必须为列表 (list)")
                for feat in obj["features"]:
                    validate_geojson_structure(feat)
            elif obj_type == "Feature":
                geom = obj.get("geometry")
                if geom is not None:
                    validate_geojson_structure(geom)
            elif obj_type == "GeometryCollection":
                if "geometries" not in obj:
                    raise ValueError("GeoJSON GeometryCollection 缺少必需的 'geometries' 字段")
                if not isinstance(obj["geometries"], list):
                    raise ValueError("GeoJSON GeometryCollection 的 'geometries' 字段必须为列表 (list)")
                for geom in obj["geometries"]:
                    validate_geojson_structure(geom)
            else:
                if "coordinates" not in obj:
                    raise ValueError(f"GeoJSON Geometry '{obj_type}' 缺少必需的 'coordinates' 字段")
                coords = obj["coordinates"]
                if not isinstance(coords, (list, tuple)):
                    raise ValueError(f"GeoJSON Geometry '{obj_type}' 的 'coordinates' 字段必须为列表或元组")

        for key, val in obj.items():
            if key not in ("features", "geometries", "geometry"):
                validate_geojson_structure(val)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._models: dict[str, Type[BaseModel]] = {}
        self._schemas: list[dict] = []
        # 工具分层元数据。无标注的工具默认 tier=1 (always-on)，确保向后兼容。
        # tier: 1 = 总在 catalog 中 (foundational / high-frequency)
        #       2 = 仅当本轮用户消息触发或最近 N 轮命中相应 domain 时载入
        #       3 = 仅在 LLM 显式 list_available_tools 后才看见 (rare / heavy)
        # domains: tier 2 工具属于哪些主题，用于关键词触发
        self._metadata: dict[str, dict[str, Any]] = {}

    def tool(self, name: str, description: str,
             param_descriptions: Optional[dict[str, str]] = None,
             args_model: Optional[Type[BaseModel]] = None,
             tier: int = 1,
             domains: Optional[List[str]] = None,
             execution_policy: Optional[ToolExecutionPolicy | str] = None,
             timeout: Optional[float] = None,
             version: str = "1.0",
             contract_version: int = 1,
             **kwargs: Any) -> Callable:
        """装饰器：注册工具到此 registry 实例"""
        def decorator(func: Callable):
            self.register(
                name, description, func,
                param_descriptions=param_descriptions,
                args_model=args_model,
                tier=tier,
                domains=domains,
                execution_policy=execution_policy,
                timeout=timeout,
                version=version,
                contract_version=contract_version,
                **kwargs,
            )
            return func
        return decorator

    def register(self, name: str, description: str, func: Callable,
                 param_descriptions: Optional[dict[str, str]] = None,
                 args_model: Optional[Type[BaseModel]] = None,
                 parameters: Optional[dict] = None,
                 tier: int = 1,
                 domains: Optional[List[str]] = None,
                 execution_policy: Optional[ToolExecutionPolicy | str] = None,
                 timeout: Optional[float] = None,
                 version: str = "1.0",
                 contract_version: int = 1,
                 **kwargs: Any):
        """注册一个工具函数"""
        self._tools[name] = func
        if parameters:
            # 优先使用显式提供的 parameters (OpenAI 格式)
            properties = parameters.get("properties", {})
            required = parameters.get("required", [])
        else:
            # 如果没有显式提供 parameters 或 model，则根据函数签名自动推导
            if args_model is None:
                args_model = self._generate_model(name, func, param_descriptions)

            self._models[name] = args_model

            # 使用 Pydantic 生成 JSON Schema
            schema_json = args_model.model_json_schema()

            properties = schema_json.get("properties", {})
            # 将 description 注入到 properties 中（OpenAI 格式需要）
            if param_descriptions:
                for p_name, p_desc in param_descriptions.items():
                    if p_name in properties:
                        properties[p_name]["description"] = p_desc
            required = schema_json.get("required", [])

        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }
            }
        }
        # 移除已存在的同名 schema，确保唯一性
        self._schemas = [s for s in self._schemas if s["function"]["name"] != name]
        self._schemas.append(schema)

        # 校验并确定执行策略
        if execution_policy is None:
            if inspect.iscoroutinefunction(func):
                policy = ToolExecutionPolicy.ASYNC
            else:
                policy = ToolExecutionPolicy.THREAD
        else:
            policy = ToolExecutionPolicy(execution_policy)
            if inspect.iscoroutinefunction(func) and policy in (
                ToolExecutionPolicy.THREAD,
                ToolExecutionPolicy.CELERY,
            ):
                # 审计修复 #374:async def 工具配 THREAD/CELERY 时,策略分支会
                # 在线程里「同步」调用它——返回未 await 的 coroutine,下游
                # json.dumps 抛 TypeError。按函数类型自动路由到 ASYNC:
                # async 工具自己 await 其内部 I/O/线程卸载,无需外层线程池。
                # (iscoroutinefunction 会解包 functools.partial/__wrapped__。)
                logger.warning(
                    "工具 %s 以 async def 注册却声明 %s 策略,自动改用 ASYNC",
                    name, policy.value,
                )
                policy = ToolExecutionPolicy.ASYNC

        # 记录分层元数据与执行策略
        # version / contract_version: 工具实现指纹（INV-TOOL，规范 §12）。
        # declared version 是作者声明的人类可读版本；contract_version 是结果契约
        # （result shape）版本——二者拼接形成稳定指纹，供 lineage/run manifest 记录
        # 「当时执行的工具是哪一个版本」。绝不把 git SHA 塞进每个 tool schema。
        # timeout: 单次执行墙钟预算（秒），覆盖模块级 _TOOL_TIMEOUT_S。
        self._metadata[name] = {
            "tier": tier,
            "domains": list(domains or []),
            "execution_policy": policy,
            "timeout": timeout,
            "version": str(version or "1.0"),
            "contract_version": int(contract_version or 1),
        }

    def _generate_model(self, name: str, func: Callable, param_descriptions: Optional[dict[str, str]]) -> Type[BaseModel]:
        """根据函数签名动态推导 Pydantic Model"""
        sig = inspect.signature(func)
        fields = {}

        for p_name, param in sig.parameters.items():
            if p_name == "self":
                continue

            # Type derivation: uses inspect.Parameter.annotation directly.
            # Complex types (Union, Optional, nested models) are passed through
            # as-is; the LLM-facing description does not decompose them.
            p_type = param.annotation if param.annotation != inspect.Parameter.empty else Any
            default = param.default if param.default != inspect.Parameter.empty else ...

            fields[p_name] = (p_type, default)

        return create_model(f"{name}_args", **fields)

    def get_schemas(self) -> list[dict]:
        return self._schemas

    def update_args_model(self, name: str, args_model: Type[BaseModel]) -> None:
        """Replace a registered tool's args model and rebuild its schema in place,
        preserving the tool description / tier / domains metadata.

        #556: ``list_available_tools`` registers before the network/temporal/
        data_fabric modules, so its domain vocabulary (derived from the live
        registry) was an incomplete snapshot. After ALL modules register,
        init_tools calls refresh_list_available_tools_args → this method so the
        published schema reflects the final domain set (single source of truth).
        """
        if name not in self._tools:
            raise KeyError(f"tool {name!r} not registered")
        self._models[name] = args_model
        schema_json = args_model.model_json_schema()
        new_params: dict[str, Any] = {
            "type": "object",
            "properties": schema_json.get("properties", {}),
            "required": schema_json.get("required", []),
        }
        for s in self._schemas:
            if s["function"]["name"] == name:
                s["function"]["parameters"] = new_params

    def get_schemas_subset(self, names: set[str]) -> list[dict]:
        """按名称白名单返回 schema 子集；用于 ToolCatalog 分层选择。"""
        return [s for s in self._schemas if s["function"]["name"] in names]

    def metadata(self, name: str) -> dict[str, Any]:
        """获取单个工具的分层元数据；未注册时返回 tier=1 兜底。"""
        return self._metadata.get(
            name, {"tier": 1, "domains": [], "version": "1.0", "contract_version": 1}
        )

    def tool_version(self, name: str) -> str:
        """稳定实现指纹：``"{declared_version}#cv{contract_version}"``。

        未注册的工具回退到 ``"1.0#cv1"``。供 ArtifactLineage.tool_version 与
        run manifest 记录「当时执行的工具版本」——替代以前硬编码的 "1.0"。

        audit #829 纪律：全库工具当前均为默认 "1.0"/cv1（历史从未 bump）。
        今后任何改变工具 RESULT 契约的提交（如 to_llm_response 形状、
        ref 剥离语义）必须同时 bump 该工具的 contract_version=，否则
        lineage 指纹永远无区分度。tests/unit/test_tooling_audit824_831.py
        锁定显式声明的版本必须贯穿 dispatch。
        """
        meta = self._metadata.get(name)
        if not meta:
            return "1.0#cv1"
        return f"{meta.get('version', '1.0')}#cv{int(meta.get('contract_version', 1) or 1)}"

    def all_metadata(self) -> dict[str, dict[str, Any]]:
        """获取全部工具的元数据快照。"""
        return dict(self._metadata)

    async def dispatch(self, name: str, arguments: dict | str, session_id: Optional[str] = None) -> Any:
        """执行工具，包含 Pydantic 校验与透明解引用。

        外层装饰：自动落 tool_metrics 一行 JSONL（含 cache_hit、错误类、时延）。
        cache_hit 通过 ContextVar 从 @cached_tool 装饰器传上来——同一 asyncio.Task
        内 ContextVar 自动跨 await 边界传播，无需 copy_context()。
        """
        import time as _time

        from app.services import tool_metrics
        from app.lib.tool_cache import cache_hit_var

        token = cache_hit_var.set(False)  # 重置 — 每次 dispatch 都从未命中开始
        start = _time.perf_counter()
        error_cls: Optional[str] = None
        active_exc: Optional[BaseException] = None
        result: Any = None
        # #677: single traversal — one budget-limited walk for arg_bytes, gate
        # and cache. Share via ContextVar so _dispatch_impl / make_cache_key
        # reuse without re-walking the same large args dict 2-3 times.
        _arg_budget = [_ESTIMATE_MAX_NODES]
        arg_bytes = _estimate_json_bytes(arguments, _budget=_arg_budget)
        _arg_approx = _arg_budget[0] <= 0
        # Approximate estimates are conservatively treated as oversized for
        # cache/validation gates (avoid false-small on huge payloads).
        _arg_is_oversized = _arg_approx or arg_bytes > _ESTIMATE_SIZE_LIMIT
        _hint_token = _arg_size_hint_var.set((arg_bytes, _arg_is_oversized))

        requested_policy = self._metadata.get(name, {}).get("execution_policy")
        req_policy_str = requested_policy.value if requested_policy else "THREAD"

        tool_func = self._tools.get(name)
        if not tool_func:
            actual_mode = "UNKNOWN"
        elif requested_policy == ToolExecutionPolicy.INLINE:
            actual_mode = "INLINE"
        elif inspect.iscoroutinefunction(tool_func):
            # async def 工具一律直接 await——即使声明了 THREAD/CELERY 策略
            # （#374，注册期已自动路由，此处兜底）。如实上报实际执行模式。
            actual_mode = "ASYNC"
        else:
            actual_mode = "THREAD"
        try:
            result = await self._dispatch_impl(name, arguments, session_id)
        except Exception as e:  # noqa: BLE001
            error_cls = type(e).__name__
            active_exc = e
            raise
        finally:
            duration_ms = int((_time.perf_counter() - start) * 1000)
            # #529: {"error": <str>} normal-return failures must be classified
            # as errors in the metrics row too (not silently "no error class").
            if isinstance(result, dict) and (
                result.get("success") is False or is_error_like_result(result)
            ):
                error_cls = (
                    error_cls
                    or result.get("error_type")
                    or result.get("code")
                    or "tool_error"
                )
            # PERF-01: avoid a second full json.dumps of large tool results
            # (e.g. a 10k-feature GeoJSON) purely to record a byte metric. The
            # dispatch service already serializes for the LLM payload; here we
            # use a cheap structural estimate that is accurate to within a few
            # percent for typical JSON, never materializing the full string.
            # #677: budget-capped so 100k-feature results cost O(budget) on
            # the event loop, not O(features). Exact for small/medium, approx
            # for huge — approximation is traceable via result_bytes_approx
            # flowing into tool_metrics (budget exhaustion is the marker).
            _result_budget = [_ESTIMATE_MAX_NODES]
            result_bytes = (
                _estimate_json_bytes(result, _budget=_result_budget) if result is not None else 0
            )
            result_bytes_approx = result is not None and _result_budget[0] <= 0
            cache_hit = cache_hit_var.get()
            # design-v3 §6 observability（additive）：只在 plan_store 进程缓存命中时
            # 附带 plan_id/plan_revision；失败时附 failure_class/recovery_action。
            plan_id = plan_revision = step_id = failure_class = recovery_action = None
            if session_id:
                try:
                    from app.services.planning.store import plan_store as _plan_store
                    _canon = _plan_store.peek(session_id)
                    if _canon is not None:
                        plan_id = _canon.plan_id
                        plan_revision = _canon.revision
                        # P2-10(a)：计划里有 running / 匹配本工具的步骤时填 step_id
                        # （running = 正在执行的步骤；tool 匹配 = 已绑定的步骤）。
                        from app.services.planning.models import StepStatus as _StepStatus
                        for _cs in _canon.steps:
                            if _cs.status == _StepStatus.running or _cs.tool == name:
                                step_id = _cs.id
                                break
                except Exception:  # noqa: BLE001
                    pass
            if error_cls is not None:
                try:
                    from app.services.planning.recovery import (
                        classify_error as _classify_error,
                        recovery_action_for as _recovery_action_for,
                    )
                    if active_exc is not None:
                        # P2-10(b)：异常路径直接分类异常——OperationCancelled 等
                        # 才能被正确分到 cancelled，而不是落到默认 internal。
                        _fc = _classify_error(exception=active_exc)
                    else:
                        _code = result.get("code") if isinstance(result, dict) else None
                        _etype = result.get("error_type") if isinstance(result, dict) else None
                        _emsg = result.get("message") if isinstance(result, dict) else None
                        _fc = _classify_error(code=_code, error_type=_etype, message=_emsg)
                    failure_class = _fc.value
                    recovery_action = _recovery_action_for(_fc).value
                except Exception:  # noqa: BLE001
                    pass
            # #691：result 为 MapSpec 产物时补 revision/fingerprint（无遍历，取即有字段）
            _mapspec_revision = _mapspec_fingerprint = None
            if isinstance(result, dict):
                _mapspec_revision = result.get("mutation_revision")
                if _mapspec_revision is not None:
                    try:
                        _mapspec_revision = int(_mapspec_revision)
                    except Exception:
                        _mapspec_revision = None
                _mapspec_fingerprint = result.get("mapspec_fingerprint")
                if not isinstance(_mapspec_fingerprint, str) or not _mapspec_fingerprint:
                    _mapspec_fingerprint = None
            tool_metrics.record_tool_call(
                tool=name,
                arg_bytes=arg_bytes,
                result_bytes=result_bytes,
                duration_ms=duration_ms,
                cache_hit=cache_hit,
                error=error_cls,
                session_id=session_id,
                requested_execution_policy=req_policy_str,
                actual_execution_mode=actual_mode,
                compute_ms=duration_ms if not cache_hit else 0,
                arg_bytes_approx=_arg_approx,
                result_bytes_approx=result_bytes_approx,
                plan_id=plan_id,
                plan_revision=plan_revision,
                step_id=step_id,
                failure_class=failure_class,
                recovery_action=recovery_action,
                mapspec_revision=_mapspec_revision,
                mapspec_fingerprint=_mapspec_fingerprint,
            )
            # Runtime observability: record this actual tool execution into the
            # live turn's evidence (single chokepoint — registry.dispatch is the
            # only path that reaches real tool execution on BOTH the Pi and
            # legacy engines, and deduped calls return before here). Recorded
            # AFTER tool_metrics so a failure here never suppresses the metrics
            # row.
            try:
                from app.lib.runtime.evidence import current_turn_evidence
                _tev = current_turn_evidence()
                if _tev is not None:
                    _tev.add_tool_call(duration_ms=duration_ms,
                                       failure_class=failure_class)
            except Exception:  # noqa: BLE001
                pass
            cache_hit_var.reset(token)
            try:
                _arg_size_hint_var.reset(_hint_token)
            except Exception:
                pass

        return result

    async def _dispatch_impl(self, name: str, arguments: dict | str, session_id: Optional[str] = None) -> Any:
        """执行工具，包含 Pydantic 校验与透明解引用"""
        from app.tools._utils import std_error_response

        # PERF/H1: resolve name → (func, meta, model) ONCE. The prior code
        # re-resolved _tools[name] / _metadata.get(name) / _models.get(name) at
        # four separate sites (existence check, signature probe, execution,
        # policy read). All are O(1) but the repetition is needless work on the
        # dispatch hot path and obscures intent.
        tool_func = self._tools.get(name)
        if tool_func is None:
            return std_error_response(f"未知工具: {name}", code="UNKNOWN_TOOL")
        meta = self._metadata.get(name, {})
        model = self._models.get(name)

        # SEC-F1: the dispatch chokepoint refuses tier-3 tools unless the
        # calling context carried an explicit confirmation (see confirm_tier3).
        # Workflow steps, subagent catalogs and plan-mode execution reach this
        # method directly — without this gate they bypass every route-level
        # confirm_destructive / tier check.
        if int(meta.get("tier", 1)) >= 3 and not tier3_confirmed():
            return std_error_response(
                f"工具 {name} 为 tier-3（危险/破坏性）操作，需要显式确认后经由管理员通道执行",
                code="TIER3_CONFIRMATION_REQUIRED",
                error_type="Tier3ConfirmationRequired",
            )

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return std_error_response(
                    f"工具参数 JSON 格式错误: {arguments}",
                    code="VALIDATION_ERROR",
                    error_type="JSONDecodeError",
                )

        # 注意：排除某些特殊字段（如 ref_id, layer_ref, layer_id, plan_id），
        # 这些字段本身就是为了接收引用 ID，绝不应被自动解引用为 GeoJSON 数据。
        if session_id and isinstance(arguments, dict):
            try:
                # audit #824: 与 Pydantic 旁路同门的预算探测提前到解析之前 ——
                # oversized 内联载荷的字符串叶是数据不是别名，别名查表直接降级。
                _oversized_for_resolver = _is_args_oversized(arguments)
                skip_keys = {"ref_id", "layer_ref", "layer_id", "plan_id", "before_ref"}
                # MapSpec ingestion preserves the source ref as provenance and
                # resolves it inside the session-aware tool. Generic transparent
                # resolution would erase that identity before the tool sees it.
                if (
                    name == "webgis_layer_upsert"
                    and isinstance(arguments.get("source_data"), str)
                ):
                    skip_keys.add("source_data")
                # GIS Harness product assembler receives ref cursors by design
                # (fetches descriptor + data itself); transparent resolution
                # would inline full FeatureCollections into tool arguments.
                if name == "webgis_map_product":
                    skip_keys.update({"primary_ref", "overlay_refs"})
                arguments = await self._resolve_references(
                    session_id,
                    arguments,
                    skip_keys=skip_keys,
                    oversized_hint=_oversized_for_resolver,
                )
            except ValueError as e:
                error_msg = str(e)
                return std_error_response(
                    error_msg,
                    code="VALIDATION_ERROR",
                    error_type="ValueError",
                    correction_hint=f"Reference Resolution Error: {error_msg}"
                )

        # Pydantic 语义校验
        # #699：超大内联 GeoJSON（>256KB / 预算耗尽）走大载荷旁路 ——
        # tool 函数本就以 `dict` 收参 + 会话层已有 descriptor 覆盖，跳过
        # 同步的 deep validate+dump（持 GIL 的 O(features) 深拷贝，100k 实测
        # ~104ms dump / tick ~163ms；to_thread 仍 ~139ms，见探针结论）。
        # 小/中载荷完整校验语义不变；是否旁路由预算化 hint 判定（与 GeoJSON
        # 校验同门，避免不同门径语义漂移）。
        # #699：单次预算化探测，Pydantic 旁路与 GeoJSON 校验门共用（两次调用
        # 会在无 hint 路径双重遍历——#677 的既有测试以顶层预算调用计数钉住）。
        _args_oversized_now = _is_args_oversized(arguments)
        if model:
            _pydantic_bypass = False
            if isinstance(arguments, dict) and _args_oversized_now:
                # 保守：仅对 `Any` 载体字段做旁路；若顶层缺失必填或类型硬错误
                # 仍应走校验（否则非法调用静默通过）。Any 字段永远通过，旁路只
                # 省掉昂贵的 deep dump。
                _has_hard_error = False
                try:
                    # 轻量探针：只验必填/类型表层（不触发深拷贝）—— 用 model_fields
                    # 做最小门；此处先以“无必填缺失”作为旁路准入，结构错误由下游
                    # 工具自检/错误面兜底（大载荷本就是透传给 session 层的）。
                    for fname, finfo in model.model_fields.items():
                        if finfo.is_required() and fname not in arguments:
                            _has_hard_error = True
                            break
                        if fname in arguments and fname != "source_data":
                            # 非载体字段做一次轻量类型探针（失败则不旁路）
                            ann = finfo.annotation
                            val = arguments[fname]
                            # 仅对 str/int 等标量做极简检查，避免重走 Pydantic
                            if ann is str and not isinstance(val, str):
                                _has_hard_error = True
                                break
                    if not _has_hard_error:
                        _pydantic_bypass = True
                except Exception:
                    _pydantic_bypass = False
            if _pydantic_bypass:
                # dict 直通：工具以 dict 形态收参，无需 normalize；跳过
                # model_dump 的 O(features) 深拷贝
                pass
            else:
                # audit #828: 未知参数此前被 extra=ignore 静默丢弃 —— LLM 幻觉
                # 出的参数无声失效。显式拒绝并列出合法参数集，走自愈通道。
                _model_extra = getattr(getattr(model, "model_config", None), "get", lambda *_: None)("extra")
                if _model_extra != "allow" and isinstance(arguments, dict):
                    _allowed = set(model.model_fields.keys())
                    _unknown = [k for k in arguments.keys() if k not in _allowed]
                    if _unknown:
                        _msg = (
                            f"工具 {name} 不接受参数: {', '.join(sorted(_unknown))}。"
                            f"合法参数: {', '.join(sorted(_allowed))}。"
                        )
                        return std_error_response(
                            _msg,
                            code="VALIDATION_ERROR",
                            error_type="ValidationError",
                            correction_hint=(
                                f"Unknown parameter(s) {sorted(_unknown)} — remove them and "
                                f"retry with only the documented parameters."
                            ),
                        )
                try:
                    validated_args = model.model_validate(arguments)
                    arguments = validated_args.model_dump()
                except ValidationError as e:
                    # 构造友好的错误信息，帮助 LLM "自愈"
                    error_msgs = []
                    for error in e.errors():
                        loc = ".".join(str(i) for i in error["loc"])
                        msg = error["msg"]
                        error_msgs.append(f"参数 '{loc}' 校验失败: {msg}")

                    message = "\n".join(error_msgs)
                    return std_error_response(
                        message,
                        code="VALIDATION_ERROR",
                        error_type="ValidationError",
                        correction_hint=f"Validation Error: {message}. Please check the tool definition and ensure all required parameters are provided with correct types."
                    )

        # GeoJSON 几何结构校验 (BE-AUDIT-08)
        # PERF-F2 + #699 + #677：与上节 Pydantic 旁路同门（_is_args_oversized），
        # 避免两道门用不同预算/阈值造成大载荷一处放行另一处仍全量走。
        try:
            if not _args_oversized_now:
                validate_geojson_structure(arguments)
        except ValueError as e:
            return std_error_response(
                str(e),
                code="VALIDATION_ERROR",
                error_type="ValueError",
                correction_hint=f"GeoJSON Validation Error: {str(e)}"
            )

        # 执行函数
        # 探测函数签名，如果需要 session_id 则传入。dispatch 的第三参是 harness
        # 注入的上下文 session；工具入参里的 session_id（LLM 显式传入）与之同名时，
        # 以已存在的非 None 工具参为准，避免用 None 覆盖显式值（composite 工具
        # 的 session_id 即走此路径，测试 `dispatch(..., {"session_id": "x"})`
        # 依赖该语义）。
        sig = inspect.signature(tool_func)
        if "session_id" in sig.parameters:
            if session_id is not None or "session_id" not in arguments:
                arguments["session_id"] = session_id
            # else: keep the explicit tool-arg session_id already in arguments

        try:
            policy = meta.get("execution_policy", ToolExecutionPolicy.THREAD)
            # 每工具墙钟预算：注册元数据 timeout 优先，否则模块级默认
            # （TOOL_TIMEOUT_S 环境变量可覆盖）。预算覆盖线程卸载路径——
            # 同步工具挂死时 to_thread worker 无法被终止，预算先放弃等待并
            # 返回超时错误结果，线程槽位由 done 回调在真实结束时归还。
            timeout = meta.get("timeout") or _TOOL_TIMEOUT_S
            async with asyncio.timeout(timeout):
                result = await self._execute_tool(name, policy, tool_func, arguments)

            if isinstance(result, GeoAnalysisResult):
                return result.to_llm_response()

            return result

        except TimeoutError:
            # #406:超时不是工具逻辑错误——单独归类，LLM 可据此收缩数据范围
            # 重试，而不是被当作 TOOL_ERROR 反复重试同一份输入。
            logger.warning("[registry] Tool '%s' timed out after %.0fs", name, timeout)
            return std_error_response(
                f"工具 {name} 执行超时（>{timeout:.0f}s），已放弃等待并回收执行槽位",
                code="TOOL_TIMEOUT",
                error_type="TimeoutError",
                correction_hint="工具耗时超过预算，请缩小数据范围或参数规模后重试。",
            )
        except ValueError as e:
            return std_error_response(
                str(e),
                code="VALIDATION_ERROR",
                error_type="ValueError",
                correction_hint=f"Error: {str(e)} Please check the tool parameters and try again."
            )
        except TypeError as e:
            # audit #828: 显式 parameters 注册的工具多传参数时以裸 TypeError
            # 落入 TOOL_ERROR —— 归类为参数校验错误并给出自愈提示。
            return std_error_response(
                str(e),
                code="VALIDATION_ERROR",
                error_type="TypeError",
                correction_hint=(
                    f"Argument mismatch: {e}. Check the tool's documented "
                    "parameters and retry with exactly those."
                ),
            )
        except KeyError as e:
            return std_error_response(
                str(e),
                code="NOT_FOUND",
                error_type="KeyError",
                correction_hint=f"Error: Key {str(e)} not found. Please check the tool parameters and the layer attributes."
            )
        except FileNotFoundError as e:
            return std_error_response(
                str(e),
                code="NOT_FOUND",
                error_type="FileNotFoundError",
                correction_hint=f"Error: File {str(e)} not found. Please ensure the path is correct."
            )
        except OperationCancelled:
            # ADR-0052：用户取消不是「工具坏了」。这里必须上抛，否则通用兜底会把它
            # 变成一个 TOOL_ERROR 结果 —— 工具管道的取消分支永远不会触发，LLM 会
            # 收到「工具执行异常」而不是「已被用户取消」，且取消有可能被当作可重试
            # 的失败对待。
            raise
        except Exception as e:
            logger.exception(f"Tool execution failed: {name}")
            return std_error_response(
                str(e),
                code="TOOL_ERROR",
                error_type=type(e).__name__,
                correction_hint="An unexpected error occurred during tool execution. Please review the error message and parameters."
            )

        return result

    async def _execute_tool(self, name: str, policy: ToolExecutionPolicy,
                            tool_func: Callable, arguments: dict) -> Any:
        """按执行策略运行工具函数（不含校验/解引用等前置步骤）。

        #374:async def 工具在 THREAD/CELERY 策略下也必须 await。to_thread 只能
        同步执行——把 coroutine 当普通对象调用会原样返回未 await 的
        coroutine，dispatch 结果无法 JSON 序列化。注册期已把 async+THREAD/
        CELERY 自动路由到 ASYNC；此处再按函数类型兜底，策略元数据与函数
        实现不符（旧数据/直接改 _metadata）时仍正确执行。
        """
        if policy == ToolExecutionPolicy.INLINE:
            # 超轻量工具 (<5ms)：直接在当前 Task 执行，不占用线程池信号量
            if inspect.iscoroutinefunction(tool_func):
                return await tool_func(**arguments)
            return tool_func(**arguments)
        if policy == ToolExecutionPolicy.ASYNC and inspect.iscoroutinefunction(tool_func):
            # 纯非阻塞 async I/O：直接 await
            return await tool_func(**arguments)
        if inspect.iscoroutinefunction(tool_func):
            # THREAD/CELERY × async def：绕过线程路径直接 await（见注释头）
            return await tool_func(**arguments)
        if policy == ToolExecutionPolicy.CELERY:
            # 重型 GDAL / 栅格 / 空间分析工具：若配置了 Celery Worker，优先投递
            # 异步 Task；在单机无 Worker 环境下，优雅降级到本地线程池隔离运行。
            logger.debug("[registry] Executing heavy tool '%s' via CELERY policy boundary", name)
        return await self._execute_sync_in_thread(tool_func, arguments)

    async def _execute_sync_in_thread(self, tool_func: Callable, arguments: dict) -> Any:
        """在隔离线程池中安全运行同步工具，并完整传递 cache_hit_var ContextVar。

        取消/超时语义（#406）：asyncio.to_thread 的 worker 线程无法被终止——
        调用方被取消或超时后线程仍会跑完。若在 await 处归还信号量，槽位就
        不再反映真实线程占用：一批挂死的工具会在 _TOOL_THREAD_LIMIT 之外
        继续累积线程，默认 executor 同时服务 Pi reader 与 context assembly，
        跨子系统互相拖累。因此槽位绑定到线程真实结束（done 回调归还），
        shield 防止取消把 CancelledError 注入 to_thread 任务本身；放弃等待
        的调用计入 _tool_thread_leaked_count（诊断/测试断言用）。
        """
        from app.lib.tool_cache import cache_hit_var

        def _run_sync_with_cache_var():
            res = tool_func(**arguments)
            return res, cache_hit_var.get()

        await _tool_thread_semaphore.acquire()
        try:
            thread_task = asyncio.create_task(asyncio.to_thread(_run_sync_with_cache_var))
        except BaseException:
            _tool_thread_semaphore.release()
            raise
        # 槽位在线程真实结束时归还（回调在事件循环中执行）
        thread_task.add_done_callback(lambda _t: _tool_thread_semaphore.release())
        try:
            result, thread_cache_hit = await asyncio.shield(thread_task)
        except asyncio.CancelledError:
            global _tool_thread_leaked_count
            _tool_thread_leaked_count += 1
            raise
        cache_hit_var.set(thread_cache_hit)
        return result

    async def _resolve_references(
        self, session_id: str, arguments: Any,
        skip_keys: Optional[set[str]] = None,
        oversized_hint: bool = False,
    ) -> Any:
        """递归解析参数中的数据引用 ref:xxx 或 别名（批量版）。

        原先每个字符串参数都 await 一次 resolve_alias —— 一次 Redis HGET
        round-trip；N 个字符串参数 = N 次串行 RTT。现在先收集全部字符串
        叶节点，用一次 resolve_aliases（单个 HMGET）批量判定别名，再递归
        替换，每次 dispatch 固定 1 次 RTT。

        audit #824: 大内联 GeoJSON 参数（20k 要素 ≈ 10 万字符串叶）曾把全
        部叶子（含重复的 "Feature"/"Point"）塞进一条 HMGET 并整树重建。
        现在：(a) 字符串叶去重后再查别名；(b) oversized 载荷或去重后仍超
        ``_ALIAS_LOOKUP_MAX_DISTINCT`` 时降级为仅解析显式 ``ref:`` 前缀
        （内联载荷里的普通字符串是数据不是别名）；(c) 重建按恒等短路 ——
        子树无任何解析命中时原样返回原节点，不再 O(tree) 拷贝。
        """
        if skip_keys is None:
            skip_keys = set()

        if not session_id:
            return arguments

        if isinstance(arguments, str):
            distinct_strings = {arguments}
        elif isinstance(arguments, (dict, list)):
            if oversized_hint:
                # audit #824: an oversized payload needs NO alias walk at all —
                # _resolve below only attempts explicit ``ref:`` cursors, so
                # the 100k-leaf collection pass is skipped entirely.
                distinct_strings = set()
            else:
                distinct_strings = set()

                def _collect(node) -> None:
                    if isinstance(node, str):
                        distinct_strings.add(node)
                    elif isinstance(node, dict):
                        for k, v in node.items():
                            if k in skip_keys:
                                continue
                            _collect(v)
                    elif isinstance(node, list):
                        for v in node:
                            _collect(v)

                _collect(arguments)
        else:
            return arguments

        # audit #824: alias lookup degradation — an oversized inline payload's
        # strings are data, not references. Resolve explicit ref: cursors only.
        aliases: dict[str, str] = {}
        if (
            not oversized_hint
            and len(distinct_strings) <= _ALIAS_LOOKUP_MAX_DISTINCT
        ):
            aliases = await session_data_manager.resolve_aliases(
                session_id, list(distinct_strings)
            )

        async def _resolve(node):
            if isinstance(node, str):
                # /review P3-5: "is this a ref or a known alias?" — when the
                # batch lookup returns something different from the input, the
                # input was a registered alias for this session.
                _resolved = aliases.get(node, node)
                if node.startswith("ref:") or _resolved != node:
                    data = await session_data_manager.get(session_id, node)
                    if data is not None:
                        # PERF-F2: the dereferenced payload is OPAQUE — refs
                        # live in the ARGUMENTS, not inside stored data. The
                        # old code recursed into the whole payload (rebuilding
                        # a 100k-feature tree node-by-node on the event loop,
                        # ~1s) re-resolving strings that merely HAPPENED to
                        # match aliases. Return by reference.
                        return data

                    # 解引用失败：构造详细错误信息引导 AI 自愈
                    available_refs = await session_data_manager.list_refs(session_id)
                    ref_info = "\n".join([f"- {rid} ({alias})" if alias else f"- {rid}" for rid, alias in available_refs.items()])
                    error_msg = f"无法找到引用数据或别名: '{node}'。"
                    if available_refs:
                        error_msg += f" 当前会话中可用的引用 ID 如下，请检查名称是否正确，并确保在同一次会话生命周期内使用:\n{ref_info}"
                    else:
                        error_msg += " 当前会话中没有任何可用的数据引用。可能是因为页面刷新导致后端会话重置，请通过查询工具重新获取数据。"

                    raise ValueError(error_msg)

                # 如果没找到且不是 ref: 格式，保持原样（可能是普通字符串参数）
                return node

            if isinstance(node, dict):
                changed = False
                new_args = {}
                for k, v in node.items():
                    if k in skip_keys:
                        new_args[k] = v
                    else:
                        r = await _resolve(v)
                        if r is not v:
                            changed = True
                        new_args[k] = r
                # audit #824: identity short-circuit — a subtree with no
                # resolution hits returns the ORIGINAL node (no O(tree) copy).
                return new_args if changed else node

            if isinstance(node, list):
                changed = False
                new_items = []
                for v in node:
                    r = await _resolve(v)
                    if r is not v:
                        changed = True
                    new_items.append(r)
                return new_items if changed else node

            return node

        return await _resolve(arguments)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())


def tool(registry: ToolRegistry, name: str, description: str,
         param_descriptions: Optional[dict[str, str]] = None,
         args_model: Optional[Type[BaseModel]] = None,
         tier: int = 1,
         domains: Optional[List[str]] = None,
         execution_policy: Optional[ToolExecutionPolicy | str] = None,
         timeout: Optional[float] = None,
         version: str = "1.0",
         contract_version: int = 1,
         **kwargs: Any):
    """装饰器：注册工具到 registry.

    tier / domains 见 ToolRegistry.register 文档。未提供时默认 tier=1 always-on。
    version / contract_version 提供稳定实现指纹（见 ToolRegistry.tool_version）。
    """
    def decorator(func: Callable):
        registry.register(
            name, description, func,
            param_descriptions=param_descriptions,
            args_model=args_model,
            tier=tier,
            domains=domains,
            execution_policy=execution_policy,
            timeout=timeout,
            version=version,
            contract_version=contract_version,
            **kwargs,
        )
        return func
    return decorator
