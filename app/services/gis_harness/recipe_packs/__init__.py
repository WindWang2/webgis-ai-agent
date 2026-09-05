"""Workflow Recipe Packs（Goal C / C2）—— 专业领域工作流知识库。

结构契约：

- 每个领域模块暴露 ``RECIPES: List[CartographyRecipe]``；
- 模块间注册顺序按模块名排序（确定性），包内按声明序；
- 与 17 个 V1 seed 的关系：seed 是跨领域通用产品族，packs 是专业差异化
  工作流 —— 重复 id 按 registry keep-first 语义由 seed 胜出（parity 测试
  锁定不重叠）；
- 所有 capability id / 制图元素 / 组件 / precondition id 必须来自既有
  registry（registry_validation + parity 测试校验），包内不得复制事实。
"""
from __future__ import annotations

import importlib
from typing import Iterator, List

from app.services.gis_harness.recipes import CartographyRecipe

#: 领域包模块名（import 路径后缀）。加载顺序 = 本元组顺序（确定性）。
PACK_MODULES = (
    "accessibility",
    "change_detection",
    "density",
    "distribution",
    "disaster",
    "environment",
    "equity",
    "exposure",
    "hydrology",
    "interpolation",
    "natural_resources",
    "network",
    "point_pattern",
    "public_health",
    "remote_sensing",
    "risk",
    "sar",
    "site_selection",
    "statistics",
    "suitability",
    "temporal",
    "terrain",
    "transport",
    "urban",
)

_BASE = "app.services.gis_harness.recipe_packs."


def iter_recipe_packs() -> Iterator[CartographyRecipe]:
    """按确定性顺序迭代全部领域包 recipe。"""
    for module_name in PACK_MODULES:
        module = importlib.import_module(_BASE + module_name)
        recipes: List[CartographyRecipe] = getattr(module, "RECIPES", [])
        yield from recipes


def all_pack_recipes() -> List[CartographyRecipe]:
    """一次性物化（parity 测试 / catalog 生成用）。"""
    return list(iter_recipe_packs())


__all__ = ["PACK_MODULES", "iter_recipe_packs", "all_pack_recipes"]
