"""ProjectKnowledge — 项目级知识投影 / 跨 Mission 空间复用（本包唯一门面）。

Kill-switch：``GIS_PROJECT_KNOWLEDGE``（ADR-0206 起**默认 ON** —— 分层情境
系统把它提升为默认 Pi 热路径面；``0`` 一键回到 opt-in）。flag off 时：路由
端点全部 503、hook 不注册、不产生任何投影写入；flag on 时路由仍走
IDOR 鉴权门，投影正确性从不依赖 hook（检索期 lazy liveness 兜底），空库
退化为"无复用候选"。
"""
from __future__ import annotations

import os

FLAG_ENV = "GIS_PROJECT_KNOWLEDGE"


def _env_truthy(name: str, default: str) -> bool:
    raw = (os.environ.get(name) or default).strip().lower()
    return raw not in ("0", "false", "off", "no")


def project_knowledge_enabled() -> bool:
    """Gate（ADR-0206 起默认 ON；kill switch 保留）。"""
    return _env_truthy(FLAG_ENV, "1")


__all__ = ["FLAG_ENV", "project_knowledge_enabled"]
