"""FC 工具注册中心"""
import inspect
import json
from typing import Any, Callable, Optional


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._schemas: list[dict] = []

    def register(self, name: str, description: str, func: Callable, param_descriptions: Optional[dict[str, str]] = None):
        """注册一个工具函数"""
        self._tools[name] = func
        sig = inspect.signature(func)
        properties = {}
        required = []

        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }

        for p_name, param in sig.parameters.items():
            if p_name == "self":
                continue
            p_type = type_map.get(param.annotation, "string")
            prop = {"type": p_type}
            if param_descriptions and p_name in param_descriptions:
                prop["description"] = param_descriptions[p_name]
            properties[p_name] = prop
            if param.default is inspect.Parameter.empty:
                required.append(p_name)

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
        self._schemas.append(schema)

    def get_schemas(self) -> list[dict]:
        return self._schemas

    def dispatch(self, name: str, arguments: dict | str) -> Any:
        """执行工具"""
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        return self._tools[name](**arguments)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())


def tool(registry: ToolRegistry, name: str, description: str, param_descriptions: Optional[dict[str, str]] = None):
    """装饰器：注册工具到 registry"""
    def decorator(func: Callable):
        registry.register(name, description, func, param_descriptions)
        return func
    return decorator
