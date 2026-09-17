"""Scene self-heal mapping — findings → existing mutation intents (ADR-0201 M7).

红线（任务书）：自愈只产生**现有** MapSpec mutation intents。本模块把
scene 质量门的可修复 finding 投影为既有 intent（SetSceneIntent /
SetViewIntent / PatchLayerPresentationIntent），由调用方经既有
``apply_mutation`` 事务管线提交（锁/CAS/回滚免费获得）。

修复映射（仅确定性安全项；不做 LLM 判断）：
- ``SCENE_PITCH_EXTREME``（blocking）→ SetViewIntent(pitch=钳到 55)
- ``SCENE_EXAGGERATION_RANGE``（blocking）→ SetSceneIntent（钳到 1.0 诚实档）
- ``SCENE_TERRAIN_SOURCE_REF`` / ``_TYPE``（blocking）→ SetSceneIntent
  （terrain 置 None：宁无地形不伪地形 —— 降级链披露由 degrade_scene 负责）

不做修复（诚实披露即可）：warning 级 finding 一律不自动改 spec（证据
披露是给 agent/用户的信号，静默改写会掩盖问题）。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List

from app.services.mapspec.lifecycle_engine import (
    PatchLayerPresentationIntent,
    SetSceneIntent,
    SetViewIntent,
)

#: 允许产出的 intent 类型白名单（测试锁定 —— 红线的可执行形式）。
_ALLOWED_INTENT_TYPES = (
    SetSceneIntent,
    SetViewIntent,
    PatchLayerPresentationIntent,
)

#: pitch 修复档（安全带内）。
_REPAIR_PITCH = 55.0


def plan_scene_repairs(
    spec: Dict[str, Any],
    findings: List[Dict[str, Any]],
) -> List[Any]:
    """scene findings → 既有 mutation intents（确定性；healthy → 空表）。

    只消费 blocking finding；warning/info 是披露信号，不自动修复。
    每个 blocking code 至多产出一个 intent（修复幂等，不叠加）。
    """
    intents: List[Any] = []
    scene = spec.get("scene")
    if not isinstance(scene, dict):
        return intents

    codes = {f.get("code") for f in findings if isinstance(f, dict)}

    if "SCENE_PITCH_EXTREME" in codes:
        intents.append(SetViewIntent(pitch=_REPAIR_PITCH))

    scene_needs_rewrite = bool(
        codes & {"SCENE_EXAGGERATION_RANGE", "SCENE_TERRAIN_SOURCE_REF", "SCENE_TERRAIN_SOURCE_TYPE"}
    )
    if scene_needs_rewrite:
        new_scene = copy.deepcopy(scene)
        terrain = new_scene.get("terrain")
        if isinstance(terrain, dict):
            if "SCENE_EXAGGERATION_RANGE" in codes:
                # 钳回诚实档（1.0 = 真实垂直比例）
                terrain["exaggeration"] = 1.0
            if codes & {"SCENE_TERRAIN_SOURCE_REF", "SCENE_TERRAIN_SOURCE_TYPE"}:
                # 源不可核实 → 撤地形声明（宁无地形不伪地形）
                new_scene["terrain"] = None
        if new_scene.get("mode") == "2.5d" and new_scene.get("terrain") is None:
            new_scene["mode"] = "2d"
        intents.append(SetSceneIntent(scene=new_scene))

    return intents
