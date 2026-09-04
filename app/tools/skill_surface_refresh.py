"""Truthful skill-surface refresh (ADR-0100, /goal §11 skills).

Pi 的原生工具面（native schema dump）在子进程 spawn 时冻结 —— 这是
vendored runtime 的约束，不是本仓库可改的行为（不 fork Pi）。真相是两层的：

1. **注册表层可以热刷新**：``load_skills`` 重新扫描技能目录，新技能立即可
   经 ``webgis_execute`` 代理（长尾通道）调用 —— 不需要任何 respawn；
2. **原生 schema 面不可热刷新**：新技能的独立 native schema（如果它是
   registry 工具）要等 worker respawn 后才对 Pi 的 schema 选择可见。

本工具把这两层真相**如实报告**给调用方（Agent/运维），而不是假装刷新
成功或静默忽略。绝不自动 respawn —— respawn 会杀死该 worker 上的活跃
回合，那是运维决策（池的 ``respawn_if_dead`` 在 worker 死亡时已有安全
路径）。
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class SkillSurfaceRefreshArgs(BaseModel):
    reason: Optional[str] = Field(
        default=None, max_length=200,
        description="Why the refresh is requested (audit trail).")


def register_skill_surface_refresh(registry: ToolRegistry) -> None:
    @tool(
        registry,
        name="refresh_skill_surface",
        description=(
            "重新扫描技能目录并刷新注册表工具面（注册表层）。新技能立即可经 "
            "webgis_execute 调用；原生 schema 面在 Pi worker spawn 时冻结，"
            "新 native 工具需 respawn 后才对 schema 选择可见 —— 返回的报告"
            "会如实区分这两层。不自动 respawn（会终止活跃回合）。",
        ),
        tier=3,
        domains=["skills"],
        args_model=SkillSurfaceRefreshArgs,
    )
    async def refresh_skill_surface(reason: Optional[str] = None) -> dict:
        try:
            from app.tools.skills import load_skills

            before = set(registry.list_tools())
            load_skills(registry)
            after = set(registry.list_tools())
            added = sorted(after - before)
        except Exception as e:  # noqa: BLE001 — 刷新失败如实报告
            logger.warning("[SkillSurface] refresh failed: %s", e, exc_info=True)
            return {
                "type": "error",
                "message": f"技能面刷新失败: {e}",
            }
        return {
            "type": "success",
            "registry_tools_added": added[:32],
            "registry_tool_count": len(after),
            "layers": {
                # 立即生效：长尾代理通道无需任何 respawn。
                "registry_layer": {
                    "refreshed": True,
                    "reachable_via": "webgis_execute",
                    "new_skills_callable_now": True,
                },
                # 不立即生效：native schema 面冻结于 spawn —— 如实说。
                "native_schema_layer": {
                    "refreshed": False,
                    "reason": "frozen at Pi worker spawn (vendored runtime)",
                    "requires": "worker respawn (operator decision — respawns "
                                "terminate active turns on that worker)",
                },
            },
            "reason": reason or "",
        }
