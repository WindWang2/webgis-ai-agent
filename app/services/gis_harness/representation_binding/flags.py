"""ADR-0219 kill-switches（repo 惯例：``GIS_*`` env flags，默认 on）。

与 ``context_assembly/flags.py`` 同纪律：默认开启 + 单向 kill——
``GIS_REQUIREMENT_BINDING=0`` / ``GIS_REQUIREMENT_PROJECTION=0`` /
``GIS_REQUIREMENT_CONTEXT_BLOCK=0`` 分别独立回退三个接线面，互不影响。
"""
from __future__ import annotations

import os


def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


def requirement_binding_enabled() -> bool:
    """C3 面：requirement → MapSpec 绑定应用与 cartography 工具 pin 合并。"""
    return _flag("GIS_REQUIREMENT_BINDING", "1")


def requirement_projection_enabled() -> bool:
    """C4 面：用户 UI mutation → Requirement 语义投影（门面 post-commit 挂点）。"""
    return _flag("GIS_REQUIREMENT_PROJECTION", "1")


def requirement_context_block_enabled() -> bool:
    """C6 面：下一轮 Pi context 的 representation binding 有界摘要块。"""
    return _flag("GIS_REQUIREMENT_CONTEXT_BLOCK", "1")
