"""FC 工具注册中心"""
import inspect
import json
import logging
from typing import Any, Callable, Optional, Type, Dict, List
from pydantic import BaseModel, create_model, ValidationError

from app.services.session_data import session_data_manager
from app.lib.geo_processor.core import GeoAnalysisResult

logger = logging.getLogger(__name__)


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

    def tool(self, name: str, description: str, **kwargs):
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

            # TODO: 支持更复杂的类型推导
            p_type = param.annotation if param.annotation != inspect.Parameter.empty else Any
            default = param.default if param.default != inspect.Parameter.empty else ...

            description = param_descriptions.get(p_name) if param_descriptions else None
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
            arguments = await self._resolve_references(
                session_id,
                arguments,
                skip_keys={"ref_id", "layer_ref", "layer_id", "plan_id", "before_ref"},
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

        # 执行函数
        # 探测函数签名，如果需要 session_id 则传入
        sig = inspect.signature(self._tools[name])
        if "session_id" in sig.parameters:
            arguments["session_id"] = session_id

        try:
            result = self._tools[name](**arguments)
            if inspect.isawaitable(result):
                result = await result
            
            if isinstance(result, GeoAnalysisResult):
                return result.to_llm_response()
                
        except ValueError as e:
            return {
                "success": False,
                "code": "VALIDATION_ERROR",
                "message": str(e),
                "data": None,
                "error_type": "ValueError",
                "correction_hint": f"Error: {str(e)} Please check the tool parameters and try again."
            }
        except KeyError as e:
            return {
                "success": False,
                "code": "NOT_FOUND",
                "message": str(e),
                "data": None,
                "error_type": "KeyError",
                "correction_hint": f"Error: Key {str(e)} not found. Please check the tool parameters and the layer attributes."
            }
        except FileNotFoundError as e:
            return {
                "success": False,
                "code": "NOT_FOUND",
                "message": str(e),
                "data": None,
                "error_type": "FileNotFoundError",
                "correction_hint": f"Error: File {str(e)} not found. Please ensure the path is correct."
            }
        except Exception as e:
            logger.exception(f"Tool execution failed: {name}")
            return {
                "success": False,
                "code": "TOOL_ERROR",
                "message": str(e),
                "data": None,
                "error_type": type(e).__name__,
                "correction_hint": "An unexpected error occurred during tool execution. Please review the error message and parameters."
            }

        return result

    async def _resolve_references(self, session_id: str, arguments: Any, skip_keys: Optional[set[str]] = None) -> Any:
        """递归解析参数中的数据引用 ref:xxx 或 别名"""
        if skip_keys is None: skip_keys = set()

        if isinstance(arguments, str):
            # /review P3-5: detect "is this a ref or a known alias?" via the public
            # resolve_alias accessor — when it returns something different from the
            # input, the input was a registered alias for this session.
            _resolved = await session_data_manager.resolve_alias(session_id, arguments) if session_id else arguments
            if arguments.startswith("ref:") or _resolved != arguments:
                data = await session_data_manager.get(session_id, arguments)
                if data is not None:
                    return data

                # 解引用失败：构造详细错误信息引导 AI 自愈
                available_refs = await session_data_manager.list_refs(session_id)
                ref_info = "\n".join([f"- {rid} ({alias})" if alias else f"- {rid}" for rid, alias in available_refs.items()])
                error_msg = f"无法找到引用数据或别名: '{arguments}'。"
                if available_refs:
                    error_msg += f" 当前会话中可用的引用 ID 如下，请检查名称是否正确，并确保在同一次会话生命周期内使用:\n{ref_info}"
                else:
                    error_msg += " 当前会话中没有任何可用的数据引用。可能是因为页面刷新导致后端会话重置，请通过查询工具重新获取数据。"

                raise ValueError(error_msg)

            # 如果没找到且不是 ref: 格式，保持原样（可能是普通字符串参数）
            return arguments

        if isinstance(arguments, dict):
            new_args = {}
            for k, v in arguments.items():
                if k in skip_keys:
                    new_args[k] = v
                else:
                    new_args[k] = await self._resolve_references(session_id, v, skip_keys)
            return new_args

        if isinstance(arguments, list):
            result = []
            for v in arguments:
                result.append(await self._resolve_references(session_id, v, skip_keys))
            return result

        return arguments

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
