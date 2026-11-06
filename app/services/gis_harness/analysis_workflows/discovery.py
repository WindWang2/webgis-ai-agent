"""Discovery 投影（ADR-0284 / B4）—— 分析工作流族的机读能力目录。

纪律：
- **零 IO 纯投影**：只汇编 families 既有声明（步骤/角色需求/前置/单位/
  可制图 hints）——不复制工具 schema、不灌 200 个工具面给 Pi；
- 资源画像（latency/memory class）经注入的 ``tool_meta`` 查询器获取
  （调用方接 ToolDescriptor；缺省=空串，绝不虚构）；
- blocked 裁决的结构化 next_step（封闭 action 词表 + 具体参数提示），
  替代自由文本建议——LLM 不能靠重复空调用碰运气。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from app.services.gis_harness.analysis_workflows.families import (
    FAMILY_IDS,
    _FAMILY_LABELS,
    _STEPS,
    role_requirements,
    visualization_hints,
)

#: next_step action 封闭词表。
NEXT_STEP_ACTIONS = (
    "bind_role",          # 绑定缺失数据角色（hint=获取通道）
    "switch_family",      # 换族（hint=候选族 id）
    "reduce_input",       # 输入超预算（hint=分块/降采样）
    "fix_role_kind",      # 载荷类型不通（hint=raster/vector 声明）
    "run_workflow",       # 一切就绪（hint=run 工具名）
)

_RUN_HINT = "webgis_analysis_workflow_run"


def _tool_meta_of(tool_meta: Optional[Callable[[str], Dict[str, str]]],
                  tool: str) -> Dict[str, str]:
    if tool_meta is None:
        return {}
    try:
        meta = tool_meta(tool)
    except Exception:  # noqa: BLE001 — 画像缺失=空（不虚构）
        return {}
    return {k: str(v) for k, v in (meta or {}).items()
            if k in ("latency_class", "memory_class", "scale_class")}


def family_catalog(
    family: Optional[str] = None,
    *,
    tool_meta: Optional[Callable[[str], Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """族 → 机读能力目录（步骤/前置/资源画像/可制图性/角色需求）。"""
    families = [family] if family else list(FAMILY_IDS)
    out: List[Dict[str, Any]] = []
    hints_all = {f: visualization_hints(f) for f in FAMILY_IDS}
    for fam in families:
        if fam not in FAMILY_IDS:
            continue
        steps = []
        for step in _STEPS[fam]:
            steps.append({
                "step_id": step.step_id,
                "tool": step.tool_name,
                "capability": step.capability,
                "preconditions": list(step.preconditions),
                "params": dict(step.step_params),
                "unit_contract": dict(step.unit_contract),
                "optional": step.optional,
                "mappable_output": any(
                    step.step_id in h.split("→")[0] or
                    step.step_id.split("_")[0] in h
                    for h in hints_all.get(fam, ())),
                "resource": _tool_meta_of(tool_meta, step.tool_name),
            })
        out.append({
            "family": fam,
            "label_zh": _FAMILY_LABELS.get(fam, fam),
            "roles": [
                {"role": r.role, "required": r.required,
                 "geometry_kinds": list(r.geometry_kinds),
                 "missing_policy": r.missing_policy, "note": r.note}
                for r in role_requirements(fam)],
            "steps": steps,
            "visualization_hints": list(hints_all.get(fam, ())),
        })
    return {"schema": "awf.discovery.v1", "families": out}


def next_step_for_blocked(reason_code: str, *,
                          role: str = "",
                          family: str = "") -> Dict[str, str]:
    """blocked/skipped 裁决 → 结构化下一步（action ∈ NEXT_STEP_ACTIONS）。"""
    code = reason_code.split(":")[0]
    if code == "AWF_BUDGET_EXCEEDED":
        return {"action": "reduce_input",
                "hint": "bbox 裁剪/聚合粒度提升/降采样后重绑（run 期仍硬门）"}
    if code == "AWF_ROLE_KIND_MISMATCH":
        return {"action": "fix_role_kind",
                "hint": f"角色 {role} 需 role_kind=raster 的 ref/路径绑定"}
    if code in ("AWF_DATA_ROLE_MISSING", "AWF_DENOMINATOR_MISSING"):
        return {"action": "bind_role",
                "hint": f"补绑角色 {role}（获取通道见 alternatives）"}
    if code == "AWF_NO_NETWORK_FOR_ACCESSIBILITY":
        return {"action": "bind_role", "role": "network",
                "hint": "本地 OSM 提取/上传路网；欧氏 buffer 是不同科学含义"}
    if code == "AWF_UNKNOWN_FAMILY":
        return {"action": "switch_family", "hint": "/".join(FAMILY_IDS)}
    return {"action": "bind_role", "hint": "按 alternatives 补数据后重新 plan"}


__all__ = [
    "NEXT_STEP_ACTIONS",
    "family_catalog",
    "next_step_for_blocked",
]
