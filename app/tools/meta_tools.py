"""元工具：让 LLM 在缺少合适工具时主动发现工具子集之外的工具。

`list_available_tools` 是 tier 1（始终可见），LLM 任何时候都能调用它来
「自救」——查询某个领域下当前未推送到 schema 子集里的工具（含 tier 3）。
"""
import logging

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class ListAvailableToolsArgs(BaseModel):
    domain: str = Field(
        ...,
        description=(
            "要查询的领域，取值之一：core / chinese / osm / raster / "
            "network / statistics / report / what_if / meta"
        ),
    )


def register_meta_tools(registry: ToolRegistry) -> None:
    """注册元工具。"""

    @tool(
        registry,
        name="list_available_tools",
        description=(
            "列出某个领域下当前所有可用工具的名称与描述。"
            "✅ 用于：当你判断需要某类能力、但本轮工具列表里没有合适工具时，"
            "调用本工具发现该领域的全部工具（包括默认未推送的重型工具）。"
        ),
        args_model=ListAvailableToolsArgs,
        tier=1,
    )
    async def list_available_tools(domain: str) -> dict:
        descriptions: dict[str, str] = {}
        for schema in registry.get_schemas():
            fn = schema.get("function", {})
            descriptions[fn.get("name", "")] = fn.get("description", "")
        matched = []
        for name, meta in registry.all_metadata().items():
            if domain in set(meta.get("domains", [])):
                matched.append({
                    "name": name,
                    "description": descriptions.get(name, ""),
                    "tier": meta.get("tier", 1),
                })
        return {"domain": domain, "count": len(matched), "tools": matched}
