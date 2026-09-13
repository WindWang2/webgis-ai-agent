"""Skill 资产模型基类 —— 版本化契约的统一资产纪律（ADR-0182 §2.5）。

所有进入 YAML 资产的模型（契约 / 过程 IR / 语义 / 组合）一律继承本基类：
**未知字段拒绝**（extra=forbid）—— 技能是审定资产，字段拼错必须装载期
红掉，而不是静默忽略；新字段 = schema_version 提升（版本演进纪律）。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SkillAssetModel(BaseModel):
    """资产模型基类（strict dict 边界；见模块 docstring）。"""

    model_config = ConfigDict(extra="forbid")


__all__ = ["SkillAssetModel"]
