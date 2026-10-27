"""3D 任务完成判定（G3D04 M6）——科学门 × 显示门 × 渲染证据。

红线（M6）：

- **成功上屏 ≠ 分析正确**：两个门独立判定，verdict 分别披露；
- **无证据不宣称完成**：渲染证据（scene 应用回执/is_compiled）与
  （如有分析）执行证据缺席 → ``blocked``/``degraded``，绝不静默成功；
- 不可用与降级进入最终回答（degradations/notes 是给 Pi 的 typed 披露面）。

科学门复用 ``method_use_contract``（零复制投影）+ 执行证据形状；
显示门复用 ``scene_quality`` 封闭 code 词表（同一权威，不另造词表）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

SCENE3D_VERDICT_SCHEMA = "scene3d_verdict.v1"

#: verdict 词表。
VERDICTS = ("completed", "degraded", "blocked")

#: 显示门 blocking code（scene_quality 词表的 blocking 子集）。
_BLOCKING_DISPLAY_CODES = {
    "SCENE_TERRAIN_SOURCE_REF", "SCENE_EXAGGERATION_OUT_OF_RANGE",
    "SCENE_PITCH_OUT_OF_RANGE",
}


class GateFinding(BaseModel):
    """一门内的一条 typed 发现。"""

    model_config = {"extra": "forbid"}

    gate: str                                  # science | display | render
    code: str
    blocking: bool = False
    detail: str = Field("", max_length=200)


class Scene3DVerdict(BaseModel):
    """3D 任务完成判定（typed；给最终回答的披露面）。"""

    model_config = {"extra": "forbid"}

    schema_version: str = SCENE3D_VERDICT_SCHEMA
    verdict: str                               # VERDICTS
    science_gate: str = "not_applicable"       # passed | degraded | blocked | not_applicable
    display_gate: str = "not_applicable"       # passed | degraded | blocked | not_applicable
    render_evidence: str = "missing"           # confirmed | missing
    findings: List[GateFinding] = Field(default_factory=list, max_length=12)
    evidence_refs: List[str] = Field(default_factory=list, max_length=8)
    summary: str = Field("", max_length=400)

    @property
    def completed(self) -> bool:
        return self.verdict == "completed"


def _scene_quality_codes(mapspec: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """跑既有 scene_quality 门（同一权威；异常按 fail-closed blocking 处理）。

    返回 findings 列表（shape: ``{code, severity, detail[, layer_id]}``）。
    """
    try:
        from app.lib.cartography.scene_quality import evaluate_scene_quality

        report = evaluate_scene_quality(mapspec or {})
        findings = report.get("findings") if isinstance(report, dict) else None
        return [f for f in findings or [] if isinstance(f, dict)]
    except Exception as exc:  # noqa: BLE001 — 显示门崩溃 = blocking（不静默放行）
        return [{"code": "SCENE_QUALITY_GATE_ERROR", "severity": "blocking",
                 "detail": str(exc)[:200]}]


def build_scene3d_verdict(
    *,
    mapspec: Optional[Dict[str, Any]],
    scene_apply_receipt: Optional[Dict[str, Any]] = None,
    analysis_execution: Optional[Dict[str, Any]] = None,
    method_contract: Optional[Dict[str, Any]] = None,
    analysis_goal: str = "",
    degradations: Optional[List[Dict[str, Any]]] = None,
) -> Scene3DVerdict:
    """双门判定（纯函数；输入全部服务端权威证据面）。

    Args:
        mapspec: 当前 MapSpec dict（显示门输入）。
        scene_apply_receipt: ``set_scene`` 回执（is_compiled/mutation_revision/
            warnings——渲染证据面；G3D02 ACK 面合入后可扩展）。
        analysis_execution: ``run_3d_analysis`` 的执行证据
            （result.success/visible_fraction 等）。
        method_contract: ``build_method_use_contract`` 投影（科学门）。
        analysis_goal: 分析目标（"" = 纯表达任务，科学门 not_applicable）。
        degradations: 决策层降级注记（decision.degradations）。
    """
    findings: List[GateFinding] = []
    refs: List[str] = []

    # ── 显示门：既有 scene_quality 权威 ───────────────────────────────
    display_state = "passed"
    scene = (mapspec or {}).get("scene")
    if isinstance(scene, dict) and scene.get("mode") in ("2.5d", "3d"):
        for item in _scene_quality_codes(mapspec):
            code = str(item.get("code") or "")
            severity = str(item.get("severity") or "")
            blocking = severity == "blocking" or code in _BLOCKING_DISPLAY_CODES
            findings.append(GateFinding(
                gate="display", code=code, blocking=blocking,
                detail=str(item.get("detail") or "")[:200],
            ))
            if blocking:
                display_state = "blocked"
            elif severity == "warning" and display_state == "passed":
                display_state = "degraded"
    else:
        display_state = "not_applicable"

    # ── 科学门：MethodUseContract 投影 + 执行证据 ────────────────────
    science_state = "not_applicable"
    if analysis_goal:
        science_state = "passed"
        contract = method_contract or {}
        for finding in contract.get("findings") or []:
            findings.append(GateFinding(
                gate="science", code=f"METHOD_{finding}", blocking=False,
                detail="MethodUseContract findings（科学适用性披露）",
            ))
            science_state = "degraded"
        preconditions = contract.get("scientific_preconditions") or []
        if preconditions:
            findings.append(GateFinding(
                gate="science", code="METHOD_PRECONDITIONS_DISCLOSED",
                blocking=False,
                detail=f"前置条件 {len(preconditions)} 条已随结果披露",
            ))
        if not isinstance(analysis_execution, dict) \
                or not analysis_execution.get("success"):
            findings.append(GateFinding(
                gate="science", code="SCIENCE_NO_EXECUTION_EVIDENCE",
                blocking=True,
                detail="分析执行证据缺席——不得宣称分析完成",
            ))
            science_state = "blocked"

    # ── 渲染证据面：scene 应用回执 ────────────────────────────────────
    render_state = "missing"
    receipt = scene_apply_receipt if isinstance(scene_apply_receipt, dict) else {}
    if receipt.get("is_compiled") or receipt.get("mutation_revision") is not None \
            or receipt.get("success"):
        render_state = "confirmed"
        if receipt.get("mutation_revision") is not None:
            refs.append(f"mapspec:rev:{receipt['mutation_revision']}")
        if receipt.get("checkpoint_id"):
            refs.append(f"checkpoint:{receipt['checkpoint_id']}")
    elif isinstance(scene, dict):
        findings.append(GateFinding(
            gate="render", code="RENDER_EVIDENCE_MISSING",
            blocking=True,
            detail="scene 已在 spec 但无应用回执（is_compiled/mutation_revision）"
                   "——渲染代次未确认，不得宣称已上屏",
        ))

    # 用户显式偏好被降级的注记 → findings（进入最终回答）
    for note in degradations or []:
        findings.append(GateFinding(
            gate="display", code=str(note.get("code") or "SCENE_DEGRADED"),
            blocking=False,
            detail=str(note.get("detail") or "")[:200],
        ))

    # ── 裁决：任一 blocking → blocked；门降级/证据缺失/用户偏好被降级
    # → degraded（诚实降级必须可见，即便 display 门 not_applicable——
    # 用户要 3D 被降到 2d 时 2d 门本身无从报障，降级注记是唯一见证）。
    any_blocking = any(f.blocking for f in findings)
    if any_blocking:
        verdict = "blocked"
    elif (science_state == "degraded" or display_state == "degraded"
            or (render_state != "confirmed" and display_state != "not_applicable")
            or bool(degradations)):
        verdict = "degraded"
    else:
        verdict = "completed"

    parts = []
    if display_state != "not_applicable":
        parts.append(f"显示门={display_state}")
    if science_state != "not_applicable":
        parts.append(f"科学门={science_state}")
    parts.append(f"渲染证据={render_state}")
    summary = "；".join(parts)
    if any_blocking:
        summary = "存在 blocking 发现——" + summary

    return Scene3DVerdict(
        verdict=verdict,
        science_gate=science_state,
        display_gate=display_state,
        render_evidence=render_state,
        findings=findings[:12],
        evidence_refs=refs[:8],
        summary=summary[:400],
    )
