"""元工具：让 LLM 在缺少合适工具时主动发现工具子集之外的工具。

`list_available_tools` 是 tier 1（始终可见），LLM 任何时候都能调用它来
「自救」——查询某个领域下当前未推送到 schema 子集里的工具（含 tier 3）。
"""
import logging
from typing import Type

from pydantic import BaseModel, Field, create_model

from app.tools.registry import ToolRegistry, tool, ToolExecutionPolicy

logger = logging.getLogger(__name__)


def _registry_domains_sorted(registry: ToolRegistry) -> list[str]:
    """Derive the domain vocabulary from the live registry — the single source
    of truth. The previous static description listed domains that don't exist
    in the registry (``core`` / ``report`` have zero tools, a dead end) while
    omitting real ones, so the LLM's discovery vocabulary drifted from reality
    (#556). Contract: every domain this tool advertises must return ≥1 tool.
    """
    return sorted(
        {
            d
            for meta in registry.all_metadata().values()
            for d in meta.get("domains", [])
        }
    )


def _build_list_available_tools_args_model(registry: ToolRegistry) -> Type[BaseModel]:
    """Args model for list_available_tools, generated from the live registry
    domains so the advertised vocabulary can never drift from reality again."""
    domains = _registry_domains_sorted(registry)
    return create_model(
        "ListAvailableToolsArgs",
        domain=(
            str,
            Field(
                ...,
                description=f"要查询的领域，取值之一：{' / '.join(domains)}",
                # audit4 #983: schema 层枚举 —— 此前拼错域名静默返回 count=0
                # 死胡同，LLM 无从纠正。
                json_schema_extra={"enum": domains},
            ),
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
        args_model=_build_list_available_tools_args_model(registry),
        tier=1,
        execution_policy=ToolExecutionPolicy.INLINE,
    )
    async def list_available_tools(domain: str) -> dict:
        # audit4 #983: 运行时兜底 —— json_schema_extra 的 enum 只是文档层提示，
        # 拼错域名时返回 available_domains 纠错信息而非静默 count=0 死胡同。
        # 保持非错误形状（success≠False）：#556 契约测试在部分注册表场景下
        # 派发任意域且要求不落入错误分支。
        valid = _registry_domains_sorted(registry)
        if domain not in valid:
            return {
                "domain": domain,
                "count": 0,
                "tools": [],
                "available_domains": valid,
                "message": (
                    f"未知领域 {domain!r}"
                    + (f"；可用领域：{' / '.join(valid)}" if valid else "（当前注册表未注册任何域工具）")
                ),
            }
        descriptions: dict[str, str] = {}
        for schema in registry.get_schemas():
            fn = schema.get("function", {})
            descriptions[fn.get("name", "")] = fn.get("description", "")
        matched = []
        hidden_tier3 = 0
        for name, meta in registry.all_metadata().items():
            if domain not in set(meta.get("domains", [])):
                continue
            # Pi 兼容审查：tier>=3 在两条 agent 路径都不可派发（registry 的
            # tier3_confirmed 门 + Pi 桥接的硬拒）—— 列出来只会诱导一轮注定
            # 失败的调用。如实过滤并披露数量（管理员通道不经本工具发现）。
            if int(meta.get("tier", 1)) >= 3:
                hidden_tier3 += 1
                continue
            matched.append({
                "name": name,
                "description": descriptions.get(name, ""),
                "tier": meta.get("tier", 1),
            })
        out = {"domain": domain, "count": len(matched), "tools": matched}
        if hidden_tier3:
            out["hidden_tier3"] = hidden_tier3
            out["message"] = (
                f"另有 {hidden_tier3} 个 tier-3 管理员工具未列出"
                "（需管理员确认通道，agent 不可执行）。"
            )
        return out


def refresh_list_available_tools_args(registry: ToolRegistry) -> None:
    """Rebuild list_available_tools' domain vocabulary after ALL tool modules
    have registered (#556).

    meta_tools registers early in init_tools (before network_tools /
    temporal_tools / data_fabric_tools), so the args model built at
    registration time derived its domain list from an incomplete registry
    (missing temporal / data_fabric / spatial_catalog / dataset). Called once
    at the end of init_tools so the published schema always matches the final
    registry — the single source of truth.
    """
    registry.update_args_model(
        "list_available_tools", _build_list_available_tools_args_model(registry)
    )
