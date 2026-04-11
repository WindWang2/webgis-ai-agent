"""FC 工具注册中心"""
import inspect
import json
import logging
from typing import Any, Callable, Optional, Type, Dict, List
from pydantic import BaseModel, create_model, ValidationError

from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._models: dict[str, Type[BaseModel]] = {}
        self._schemas: list[dict] = []

    def register(self, name: str, description: str, func: Callable, 
                 param_descriptions: Optional[dict[str, str]] = None,
                 args_model: Optional[Type[BaseModel]] = None):
        """注册一个工具函数"""
        self._tools[name] = func
        
        # 如果没有显式提供 model，则根据函数签名自动推导
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

        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": schema_json.get("required", []),
                }
            }
        }
        self._schemas.append(schema)

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

    async def dispatch(self, name: str, arguments: dict | str, session_id: Optional[str] = None) -> Any:
        """执行工具，包含 Pydantic 校验与透明解引用"""
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
            
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                raise ValueError(f"Invalid JSON arguments for tool {name}: {arguments}")

        # 透明解引用 (Transparent Dereferencing)
        # 注意：排除某些特殊字段（如 ref_id），这些字段本身就是为了接收引用 ID
        if session_id and isinstance(arguments, dict):
            arguments = self._resolve_references(session_id, arguments, skip_keys={"ref_id"})

        # Pydantic 语义校验
        model = self._models.get(name)
        if model:
            try:
                validated_args = model.model_validate(arguments)
                arguments = validated_args.model_dump()
            except ValidationError as e:
                # 构造友好的错误信息，帮助 LLM “自愈”
                error_msgs = []
                for error in e.errors():
                    loc = ".".join(str(i) for i in error["loc"])
                    msg = error["msg"]
                    error_msgs.append(f"参数 '{loc}' 校验失败: {msg}")
                raise ValueError("\n".join(error_msgs))

        # 执行函数
        # 探测函数签名，如果需要 session_id 则传入
        sig = inspect.signature(self._tools[name])
        if "session_id" in sig.parameters:
            arguments["session_id"] = session_id

        result = self._tools[name](**arguments)
        if inspect.isawaitable(result):
            result = await result
        return result

    def _resolve_references(self, session_id: str, arguments: Any, skip_keys: Optional[set[str]] = None) -> Any:
        """递归解析参数中的数据引用 ref:xxx 或 别名"""
        if skip_keys is None: skip_keys = set()

        if isinstance(arguments, str):
            # 尝试解引用（可能是 ref:xxx 或是 用户设置的别名）
            data = session_data_manager.get(session_id, arguments)
            if data is not None:
                return data
            # 如果没找到，保持原样（可能是普通字符串参数）
            return arguments
        
        if isinstance(arguments, dict):
            new_args = {}
            for k, v in arguments.items():
                if k in skip_keys:
                    new_args[k] = v
                else:
                    new_args[k] = self._resolve_references(session_id, v, skip_keys)
            return new_args
        
        if isinstance(arguments, list):
            return [self._resolve_references(session_id, v, skip_keys) for v in arguments]
        
        return arguments

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())


def tool(registry: ToolRegistry, name: str, description: str, 
         param_descriptions: Optional[dict[str, str]] = None,
         args_model: Optional[Type[BaseModel]] = None):
    """装饰器：注册工具到 registry"""
    def decorator(func: Callable):
        registry.register(name, description, func, param_descriptions, args_model)
        return func
    return decorator
