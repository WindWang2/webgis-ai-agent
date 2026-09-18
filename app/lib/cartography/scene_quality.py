<<<<<<< HEAD
"""Deterministic scene quality gate (ADR-0201 M7).
=======
"""Deterministic scene quality gate (ADR-0199 M7).
>>>>>>> origin/cartography/multiscale-scene-intelligence-v1

场景质量门（纯函数、可回放、零 LLM 依赖）：把 3D 场景的**可确定性判定**
质量面收敛为一个 findings 清单。VLM 视觉轴（遮挡/可辨性）由 ADR-0185 冻结
的 5 轴契约覆盖（spatial_alignment 等），本模块只做确定性可判的：

- 数据面完整性：terrain 源悬空/类型错误（blocking —— 与 pre-compile 校验
  同口径双闸）。
- 垂直语义：exaggeration 越界（blocking）/ 失真披露（info）、未知垂直单位
  （warning）。
- 证据诚实：无高度证据的 fill-extrusion 层（warning —— 保留渲染但披露，
  不静默放行也不粗暴拒绝）。
- 相机安全：pitch 越硬上限（blocking）/ 超 60°（warning）。
- 模式一致性：mode=3d 但无任何垂直内容（warning）。
- 图例同源性：场景切换前后逐层 legend_spec digest 对比（检测漂移）。

severity 词表：blocking（拒绝/必须修复）> warning（放行 + 披露）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

#: 质量门 reason code 词表（封闭集合；测试锁定）。
SCENE_QUALITY_CODES = (
    "SCENE_TERRAIN_SOURCE_REF",
    "SCENE_TERRAIN_SOURCE_TYPE",
    "SCENE_EXAGGERATION_RANGE",
    "SCENE_EXAGGERATION_DISTORTS_SCALE",
    "SCENE_EXTRUSION_NO_EVIDENCE",
    "SCENE_MODE_EMPTY_3D",
    "SCENE_PITCH_EXTREME",
    "SCENE_VERTICAL_UNIT_UNKNOWN",
    "SCENE_LEGEND_DRIFT",
)

#: pitch 硬上限（MapLibre 合法域）与产品建议上限。
_PITCH_HARD_MAX = 85.0
_PITCH_ADVICE_MAX = 60.0

#: 失真披露阈（>1.5 需披露 —— scene_planning 同口径单源）。
_EXAGGERATION_DISCLOSURE = 1.5

#: finding 形状键（有界、可序列化）。
_FINDING_KEYS = {"code", "severity", "detail", "layer_id"}


def _finding(code: str, severity: str, detail: str, layer_id: Optional[str] = None) -> Dict[str, Any]:
    f = {"code": code, "severity": severity, "detail": detail}
    if layer_id:
        f["layer_id"] = layer_id
    return f


def _legend_digest(legend_spec: Any) -> str:
    """legend_spec 的稳定 digest（排序键序列化 —— 语义相等即同 digest）。"""
    if legend_spec is None:
        return ""
    return json.dumps(legend_spec, sort_keys=True, ensure_ascii=False, default=str)


def check_legend_invariance(
    before_layers: List[Dict[str, Any]],
    after_layers: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """场景切换前后逐层 legend_spec digest 对比（Oracle G1 的确定性判据）。

    图层集合或任一专题层的图例 digest 变化 → SCENE_LEGEND_DRIFT finding。
    返回 None = 同源不漂移。
    """
    before_by_id = {lyr.get("id"): lyr for lyr in before_layers if isinstance(lyr, dict)}
    after_by_id = {lyr.get("id"): lyr for lyr in after_layers if isinstance(lyr, dict)}
    for layer_id in sorted(before_by_id):
        if layer_id not in after_by_id:
            return _finding(
                "SCENE_LEGEND_DRIFT", "blocking",
                f"layer '{layer_id}' removed during scene switch",
                layer_id=layer_id,
            )
        before_digest = _legend_digest(before_by_id[layer_id].get("legend_spec"))
        after_digest = _legend_digest(after_by_id[layer_id].get("legend_spec"))
        if before_digest != after_digest:
            return _finding(
                "SCENE_LEGEND_DRIFT", "blocking",
                f"legend_spec digest changed for layer '{layer_id}'",
                layer_id=layer_id,
            )
    return None


def evaluate_scene_quality(spec: Dict[str, Any]) -> Dict[str, Any]:
    """spec → 场景质量报告 {passed, blocking_count, findings}。

    findings 按严重度排序（blocking 在前）；无 scene 的 spec 直接通过
    （存量 2d 语义零门槛 —— 向后兼容是 Oracle 条款）。
    """
    findings: List[Dict[str, Any]] = []
    scene = spec.get("scene")

    if not isinstance(scene, dict):
        return {"passed": True, "blocking_count": 0, "findings": []}

    mode = scene.get("mode")
    terrain = scene.get("terrain")
    camera = scene.get("camera")
    sources = spec.get("sources") or {}
    layers = spec.get("layers") or []

    # ── terrain 数据面完整性（与 coordinator.validate 双闸同口径）────────
    if isinstance(terrain, dict):
        src_id = terrain.get("source")
        src = sources.get(src_id) if isinstance(src_id, str) else None
        if src is None:
            findings.append(_finding(
                "SCENE_TERRAIN_SOURCE_REF", "blocking",
                f"scene.terrain references missing source '{src_id}'",
            ))
        elif src.get("type") != "raster-dem":
            findings.append(_finding(
                "SCENE_TERRAIN_SOURCE_TYPE", "blocking",
                f"terrain source '{src_id}' is '{src.get('type')}', expected 'raster-dem'",
            ))
        # ── 垂直语义 ──────────────────────────────────────────────────────
        ex = terrain.get("exaggeration")
        if ex is not None:
            try:
                ex_f = float(ex)
            except (TypeError, ValueError):
                ex_f = None
            if ex_f is None or not (0 < ex_f <= 10.0):
                findings.append(_finding(
                    "SCENE_EXAGGERATION_RANGE", "blocking",
                    f"terrain.exaggeration {ex!r} outside (0, 10]",
                ))
            elif ex_f > _EXAGGERATION_DISCLOSURE:
                findings.append(_finding(
                    "SCENE_EXAGGERATION_DISTORTS_SCALE", "info",
                    f"exaggeration {ex_f} distorts vertical scale; disclose "
                    "alongside any scale representation",
                ))
        unit = terrain.get("vertical_unit")
        if unit is not None and unit != "m":
            findings.append(_finding(
                "SCENE_VERTICAL_UNIT_UNKNOWN", "warning",
                f"vertical_unit {unit!r} has no conversion support; elevation "
                "readings assume meters",
            ))

    # ── 相机安全 ──────────────────────────────────────────────────────────
    if isinstance(camera, dict):
        pitch = camera.get("pitch")
        if isinstance(pitch, (int, float)) and not isinstance(pitch, bool):
            if pitch > _PITCH_HARD_MAX or pitch < 0:
                findings.append(_finding(
                    "SCENE_PITCH_EXTREME", "blocking",
                    f"camera pitch {pitch} outside [0, {_PITCH_HARD_MAX}]",
                ))
            elif pitch > _PITCH_ADVICE_MAX:
                findings.append(_finding(
                    "SCENE_PITCH_EXTREME", "warning",
                    f"camera pitch {pitch} > {_PITCH_ADVICE_MAX}: polygons compress "
                    "into slivers; consider <= 60",
                ))

    # ── 证据诚实：无证据挤出 ─────────────────────────────────────────────
    has_vertical_content = False
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        if layer.get("type") != "fill-extrusion":
            continue
        has_vertical_content = True
        paint = layer.get("paint") or {}
        contract = layer.get("extrusion") or {}
        has_height_evidence = bool(
            paint.get("fill-extrusion-height")
            or paint.get("height")
            or contract.get("height_field")
        )
        if not has_height_evidence:
            findings.append(_finding(
                "SCENE_EXTRUSION_NO_EVIDENCE", "warning",
                f"fill-extrusion layer '{layer.get('id')}' declares no height "
                "evidence (no paint height, no extrusion contract); heights may "
                "be fabricated by the renderer — attach extrusion.height_field",
                layer_id=str(layer.get("id")),
            ))

    # ── 模式一致性 ────────────────────────────────────────────────────────
    if mode == "3d" and not has_vertical_content and terrain is None:
        findings.append(_finding(
            "SCENE_MODE_EMPTY_3D", "warning",
            "scene.mode=3d but no extrusion layer and no terrain; mode claim "
            "is empty",
        ))

    blocking = sum(1 for f in findings if f["severity"] == "blocking")
    findings.sort(key=lambda f: 0 if f["severity"] == "blocking" else 1)
    return {
        "passed": blocking == 0,
        "blocking_count": blocking,
        "findings": findings,
    }


def scene_quality_report_safe(spec: Any) -> Dict[str, Any]:
    """永不抛版本（供生命周期评审段消费 —— 与 critic_engine fail-closed 同款）。"""
    try:
        if not isinstance(spec, dict):
            return {"passed": True, "blocking_count": 0, "findings": []}
        report = evaluate_scene_quality(spec)
        # 有界 + 键白名单（防上游注入任意键扩散到 evidence 通道）
        findings = [
            {k: f[k] for k in f if k in _FINDING_KEYS} for f in report["findings"][:64]
        ]
        return {"passed": report["passed"], "blocking_count": report["blocking_count"], "findings": findings}
    except Exception:  # noqa: BLE001 — 门禁绝不影响主流程（诚实记录交给调用方）
        return {"passed": True, "blocking_count": 0, "findings": [], "error": "scene_quality_gate_error"}
