"""FC 工具注册中心"""
import asyncio
import inspect
import json
import logging
from typing import Any, Callable, Optional, Type, List
from pydantic import BaseModel, create_model, ValidationError

from app.services.session_data import session_data_manager
from app.lib.geo_processor.core import GeoAnalysisResult

logger = logging.getLogger(__name__)

VALID_GEOMETRY_TYPES = {
    "Point", "MultiPoint",
    "LineString", "MultiLineString",
    "Polygon", "MultiPolygon",
    "GeometryCollection",
}
VALID_GEOJSON_TYPES = VALID_GEOMETRY_TYPES | {"Feature", "FeatureCollection"}


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

    def tool(self, name: str, description: str, **kwargs: Any) -> Callable:
        """装饰器：注册工具到此 registry 实例"""
        def decorator(func: Callable):
            self.register(name, description, func, **kwargs)
            return func
        return decorator

    def register(self, name: str, description: str, func: Callable,
                 param_descriptions: Optional[dict[str, str]] = None,
                 args_model: Optional[Type[BaseModel]] = None,
                 parameters: Optional[dict] = None,
                 tier: int = 1,
                 domains: Optional[List[str]] = None):
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

        # 记录分层元数据
        self._metadata[name] = {"tier": tier, "domains": list(domains or [])}

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

    def get_schemas_subset(self, names: set[str]) -> list[dict]:
        """按名称白名单返回 schema 子集；用于 ToolCatalog 分层选择。"""
        return [s for s in self._schemas if s["function"]["name"] in names]

    def metadata(self, name: str) -> dict[str, Any]:
        """获取单个工具的分层元数据；未注册时返回 tier=1 兜底。"""
        return self._metadata.get(name, {"tier": 1, "domains": []})

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
        import json as _json

        from app.services import tool_metrics
        from app.lib.tool_cache import cache_hit_var

        token = cache_hit_var.set(False)  # 重置 — 每次 dispatch 都从未命中开始
        start = _time.perf_counter()
        error_cls: Optional[str] = None
        result: Any = None
        try:
            arg_bytes = len(_json.dumps(arguments, default=str))
        except Exception:
            arg_bytes = 0

        try:
            result = await self._dispatch_impl(name, arguments, session_id)
        except Exception as e:  # noqa: BLE001
            error_cls = type(e).__name__
            raise
        finally:
            duration_ms = int((_time.perf_counter() - start) * 1000)
            if isinstance(result, dict) and result.get("success") is False:
                error_cls = error_cls or result.get("error_type") or result.get("code")
            try:
                result_bytes = len(_json.dumps(result, default=str)) if result is not None else 0
            except Exception:
                result_bytes = 0
            cache_hit = cache_hit_var.get()
            tool_metrics.record_tool_call(
                tool=name,
                arg_bytes=arg_bytes,
                result_bytes=result_bytes,
                duration_ms=duration_ms,
                cache_hit=cache_hit,
                error=error_cls,
                session_id=session_id,
            )
            cache_hit_var.reset(token)

        return result

    async def _dispatch_impl(self, name: str, arguments: dict | str, session_id: Optional[str] = None) -> Any:
        """执行工具，包含 Pydantic 校验与透明解引用"""
        from app.tools._utils import std_error_response

        if name not in self._tools:
            return std_error_response(f"未知工具: {name}", code="UNKNOWN_TOOL")

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
                arguments = await self._resolve_references(
                    session_id,
                    arguments,
                    skip_keys={"ref_id", "layer_ref", "layer_id", "plan_id", "before_ref"},
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
        model = self._models.get(name)
        if model:
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
        try:
            validate_geojson_structure(arguments)
        except ValueError as e:
            return std_error_response(
                str(e),
                code="VALIDATION_ERROR",
                error_type="ValueError",
                correction_hint=f"GeoJSON Validation Error: {str(e)}"
            )

        # 执行函数
        # 探测函数签名，如果需要 session_id 则传入
        sig = inspect.signature(self._tools[name])
        if "session_id" in sig.parameters:
            arguments["session_id"] = session_id

        try:
            # 性能：同步 (CPU-bound) 工具 —— 绝大多数空间分析（ST-DBSCAN /
            # KDE / Moran's I / hotspot / 聚类 等）—— 在调用前先 offload 到
            # 线程池，避免阻塞 asyncio 事件循环。否则一次几十秒的聚类会让
            # 全部 SSE 流 / WebSocket / 其他请求同时卡死。
            # async 工具直接 await（自身即非阻塞）。
            tool_func = self._tools[name]
            if asyncio.iscoroutinefunction(tool_func):
                result = await tool_func(**arguments)
            else:
                # 在线程里跑同步工具。@cached_tool 在线程内 set(cache_hit_var)
                # 但 asyncio.to_thread 复制 context、set 不回传到当前 Task ——
                # 因此显式捕获线程内最终值，await 后恢复到本 Task，保证
                # registry 的 timing 记录正确读到 cache_hit。
                from app.lib.tool_cache import cache_hit_var

                def _run_sync_with_cache_var():
                    res = tool_func(**arguments)
                    return res, cache_hit_var.get()

                result, thread_cache_hit = await asyncio.to_thread(
                    _run_sync_with_cache_var
                )
                cache_hit_var.set(thread_cache_hit)

            if isinstance(result, GeoAnalysisResult):
                return result.to_llm_response()

        except ValueError as e:
            return std_error_response(
                str(e),
                code="VALIDATION_ERROR",
                error_type="ValueError",
                correction_hint=f"Error: {str(e)} Please check the tool parameters and try again."
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
        except Exception as e:
            logger.exception(f"Tool execution failed: {name}")
            return std_error_response(
                str(e),
                code="TOOL_ERROR",
                error_type=type(e).__name__,
                correction_hint="An unexpected error occurred during tool execution. Please review the error message and parameters."
            )

        return result

    async def _resolve_references(self, session_id: str, arguments: Any, skip_keys: Optional[set[str]] = None) -> Any:
        """递归解析参数中的数据引用 ref:xxx 或 别名（批量版）。

        原先每个字符串参数都 await 一次 resolve_alias —— 一次 Redis HGET
        round-trip；N 个字符串参数 = N 次串行 RTT。现在先收集全部字符串
        叶节点，用一次 resolve_aliases（单个 HMGET）批量判定别名，再递归
        替换，每次 dispatch 固定 1 次 RTT。
        """
        if skip_keys is None:
            skip_keys = set()

        if not session_id:
            return arguments

        if isinstance(arguments, str):
            strings = [arguments]
        elif isinstance(arguments, (dict, list)):
            strings: list[str] = []

            def _collect(node) -> None:
                if isinstance(node, str):
                    strings.append(node)
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

        aliases = await session_data_manager.resolve_aliases(session_id, strings)

        async def _resolve(node):
            if isinstance(node, str):
                # /review P3-5: "is this a ref or a known alias?" — when the
                # batch lookup returns something different from the input, the
                # input was a registered alias for this session.
                _resolved = aliases.get(node, node)
                if node.startswith("ref:") or _resolved != node:
                    data = await session_data_manager.get(session_id, node)
                    if data is not None:
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
                new_args = {}
                for k, v in node.items():
                    if k in skip_keys:
                        new_args[k] = v
                    else:
                        new_args[k] = await _resolve(v)
                return new_args

            if isinstance(node, list):
                return [await _resolve(v) for v in node]

            return node

        return await _resolve(arguments)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())


def tool(registry: ToolRegistry, name: str, description: str,
         param_descriptions: Optional[dict[str, str]] = None,
         args_model: Optional[Type[BaseModel]] = None,
         tier: int = 1,
         domains: Optional[List[str]] = None):
    """装饰器：注册工具到 registry.

    tier / domains 见 ToolRegistry.register 文档。未提供时默认 tier=1 always-on。
    """
    def decorator(func: Callable):
        registry.register(
            name, description, func,
            param_descriptions=param_descriptions,
            args_model=args_model,
            tier=tier,
            domains=domains,
        )
        return func
    return decorator
