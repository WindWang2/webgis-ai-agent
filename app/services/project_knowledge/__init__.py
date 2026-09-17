"""ProjectKnowledge — 项目级知识投影 / 跨 Mission 空间复用（本包唯一门面）。

Kill-switch：``GIS_PROJECT_KNOWLEDGE``（默认 OFF —— 新 LLM 可见面必须
opt-in，与 ``GIS_MISSION_HOTPATH`` 同哲学）。flag off 时：路由端点全部
503、hook 不注册、不产生任何投影写入。
"""
from __future__ import annotations

import os

FLAG_ENV = "GIS_PROJECT_KNOWLEDGE"


def _env_truthy(name: str, default: str) -> bool:
    raw = (os.environ.get(name) or default).strip().lower()
    return raw not in ("0", "false", "off", "no")


def project_knowledge_enabled() -> bool:
    """Opt-in gate（default OFF）。"""
    return _env_truthy(FLAG_ENV, "0")


__all__ = ["FLAG_ENV", "project_knowledge_enabled"]
