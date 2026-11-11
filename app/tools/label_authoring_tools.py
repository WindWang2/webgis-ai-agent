"""要素级标注 authoring 工具族（G16，webgis_label_*）。

薄工具面：参数校验 + 会话读取 + 统一写路径（apply_gis_mutation →
PatchLayerLabelAuthoringIntent，与 UI 手势同一 CAS/撤销栈/user-wins
通道 —— 绝不出现第二执行入口）。inspect 是只读投影：

- why-not-shown 词表带**来源**（authoring_policy / data / identity），
  可证的才说，不可证的显式 not_evaluated / not_observed —— 离线预测
  不冒充 live 观察（MapLibre 无逐要素碰撞原因权威接口）；
- placement 族原因（collision/obstacle/budget）默认 not_evaluated
  （需要确定性导出求解或 live 观察），不给猜测。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.lib.cartography import label_authoring as _la
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

_MAX_INSPECT_FEATURES = 64


class LabelInspectArgs(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    layer_id: Optional[str] = Field(None, max_length=200,
                                    description="层 id（缺省=首个带 label 的层）")
    feature_ids: Optional[List[str]] = Field(
        None, max_length=64,
        description="目标 featureId 集（缺省=当前覆写全部目标）")
    zoom: Optional[float] = Field(None, ge=0, le=24,
                                  description="当前缩放（below_scale 判定用）")


class LabelAuthorArgs(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    layer_id: str = Field(..., min_length=1, max_length=200)
    ops: List[Dict[str, Any]] = Field(
        default_factory=list, max_length=200,
        description="[{featureId, op: set|reset, set?: {...}, scope?: visibility|position|text|all}]")
    labels_config: Optional[Dict[str, Any]] = Field(
        None,
        description="MapLabelConfig 补丁（仅 obstaclePolicy/forbiddenZones）")
    preview_only: bool = Field(False, description="只预览不提交（before/after 回执）")
    expected_revision: Optional[int] = Field(None, ge=0, description="CAS 基线")


def _layer_label_summary(layer: Dict[str, Any]) -> Dict[str, Any]:
    label = layer.get("label") if isinstance(layer.get("label"), dict) else {}
    strategy = {}
    for key in ("mode", "topN", "priorityField", "sizeRatio", "haloMode"):
        if label.get(key) is not None:
            strategy[key] = label[key]
    bands = label.get("zoomBands")
    if isinstance(bands, list):
        strategy["zoomBands"] = len(bands)
    return {"field": label.get("field"), "strategy": strategy}


def _band_excludes(zoom: Optional[float], label: Dict[str, Any]) -> Optional[str]:
    """zoom 是否落在声明档外（authoring_policy 面）。None = 不适用。"""
    bands = label.get("zoomBands")
    if zoom is None or not isinstance(bands, list) or not bands:
        return None
    for band in bands:
        if not isinstance(band, dict):
            continue
        lo = band.get("minZoom")
        hi = band.get("maxZoom")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            if lo <= zoom < hi:
                return None
    return "below_scale"


def _why_not_shown(
    *,
    override: Optional[Dict[str, Any]],
    status: str,
    layer_label: Dict[str, Any],
    zoom: Optional[float],
) -> List[Dict[str, str]]:
    """有来源的未显示理由（可证的才说；不可证 → 显式未知口径）。"""
    reasons: List[Dict[str, str]] = []
    if override is not None and override.get("hidden") is True:
        reasons.append({"reason": "hidden_by_user", "source": "authoring_policy"})
    if status == "orphan":
        reasons.append({"reason": "orphan_override", "source": "identity"})
    elif status == "stale":
        reasons.append({"reason": "stale_override", "source": "identity"})
    elif status == "field_missing":
        reasons.append({"reason": "field_missing", "source": "data"})
    if not reasons:
        band = _band_excludes(zoom, layer_label)
        if band:
            reasons.append({"reason": "below_scale", "source": "authoring_policy"})
    if not reasons:
        # 没有可证理由：placement 族（碰撞/障碍/预算）不猜测。
        reasons.append({
            "reason": "not_evaluated",
            "source": "not_evaluated",
            "detail": (
                "placement-family reasons (collision/obstacle/budget) need "
                "the deterministic export solve or a live observation; "
                "MapLibre has no per-feature collision-reason API"
            ),
        })
    return reasons


def register_label_authoring_tools(registry: ToolRegistry) -> None:
    """注册要素级标注工具族（inspect / author）。"""

    @tool(
        registry,
        name="webgis_label_inspect",
        capabilities=["map_label_authoring"],
        description=(
            "读取图层的要素级标注状态：覆写（隐藏/固定/偏移/文字/优先级）+ "
            "每目标失效状态（active/stale/orphan）+ 有来源的未显示理由"
            "（hidden_by_user/below_scale/field_missing/stale_override），"
            "placement 族原因显式 not_evaluated（不冒充 live 观察）。"
        ),
        tier=2,
        domains=["map", "cartography"],
        args_model=LabelInspectArgs,
        side_effect="pure",
        deterministic=False,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        output_semantic_type="json",
        result_size_policy="inline_small",
        tags=("标注", "标签", "label", "inspect", "为什么没显示"),
    )
    async def webgis_label_inspect(
        session_id: str,
        layer_id: Optional[str] = None,
        feature_ids: Optional[List[str]] = None,
        zoom: Optional[float] = None,
    ) -> dict:
        from app.services.mapspec_store import mapspec_store

        spec = await mapspec_store.get_mapspec(session_id)
        if not isinstance(spec, dict):
            return {"success": False, "message": "Session MapSpec unavailable."}
        layers = spec.get("layers") if isinstance(spec.get("layers"), list) else []

        def _has_label(ly: Any) -> bool:
            return (
                isinstance(ly, dict)
                and isinstance(ly.get("label"), dict)
                and bool(ly["label"].get("field"))
            )

        layer = None
        if layer_id:
            layer = next((ly for ly in layers if isinstance(ly, dict)
                          and ly.get("id") == layer_id), None)
            if layer is None:
                return {"success": False,
                        "message": f"Layer {layer_id} not found.",
                        "correction_hint": "Re-read MapSpec for valid layer ids."}
        else:
            layer = next((ly for ly in layers if _has_label(ly)), None)
        if layer is None or not _has_label(layer):
            return {"success": False,
                    "message": "No layer with label config found.",
                    "correction_hint": "Enable labels on a layer first."}

        lid = str(layer.get("id"))
        label_cfg = layer["label"]
        authoring = label_cfg.get("authoring") if isinstance(
            label_cfg.get("authoring"), dict) else {}
        overrides = [o for o in (authoring.get("overrides") or [])
                     if isinstance(o, dict)]

        # 源数据（在场才做身份复核；ref 源 → unverified 如实披露）。
        sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
        src = sources.get(layer.get("source"))
        feats = None
        rev_now = None
        if isinstance(src, dict):
            rev_raw = src.get("content_revision")
            if isinstance(rev_raw, (int, float)) and not isinstance(rev_raw, bool):
                rev_now = float(rev_raw)
            data = src.get("inlineData")
            if isinstance(data, dict) and isinstance(data.get("features"), list):
                feats = data["features"]

        targets: List[Dict[str, Any]] = []
        wanted = [str(f) for f in feature_ids] if feature_ids else \
            [str(o.get("featureId")) for o in overrides]
        wanted = wanted[:_MAX_INSPECT_FEATURES]
        for fid in wanted:
            override = next((o for o in overrides
                             if str(o.get("featureId")) == fid), None)
            props = None
            found = False
            if feats is not None:
                hit = _la.find_feature_overrides(feats, [fid])
                found = fid in hit
                if found:
                    props = feats[hit[fid][0]].get("properties")
            # R1-P3：ref 源（数据不可复核）→ unverified 如实披露
            # （与 ADR-0289 §7 消费侧权威口径一致；此前误报 active）。
            if feats is None:
                status = "unverified" if override is not None else "unverified"
            elif override is not None:
                status = _la.override_status(
                    override, feature_found=found,
                    current_revision=rev_now,
                    label_field=label_cfg.get("field"),
                    feature_properties=props,
                )
            else:
                status = "active" if found else "orphan"
            entry: Dict[str, Any] = {
                "featureId": fid,
                "status": status,
            }
            if override is not None:
                entry["override"] = {
                    k: override.get(k) for k in (
                        "hidden", "pinned", "offset", "text", "callout",
                        "priority", "origin", "sourceRevision", "note")
                    if override.get(k) is not None
                }
                entry["why_not_shown"] = _why_not_shown(
                    override=override, status=status,
                    layer_label=label_cfg, zoom=zoom)
            elif status == "orphan":
                entry["why_not_shown"] = [
                    {"reason": "orphan_override", "source": "identity"}]
            targets.append(entry)

        # 有界摘要（情境投影口径：锁定数/冲突数不展开全列表）。
        pinned_n = sum(1 for o in overrides if o.get("pinned") is True)
        hidden_n = sum(1 for o in overrides if o.get("hidden") is True)
        layout = spec.get("layout") if isinstance(spec.get("layout"), dict) else {}
        labels_c = layout.get("labels") if isinstance(
            layout.get("labels"), dict) else {}
        return {
            "success": True,
            "layer_id": lid,
            "label": _layer_label_summary(layer),
            "source_revision": rev_now,
            "labels_config": {
                k: labels_c.get(k) for k in (
                    "collision", "maxLabels", "obstaclePolicy")
                if labels_c.get(k) is not None
            },
            "summary": {
                "overrides": len(overrides),
                "pinned": pinned_n,
                "hidden": hidden_n,
            },
            "targets": targets,
        }

    @tool(
        registry,
        name="webgis_label_author",
        capabilities=["map_label_authoring"],
        description=(
            "预览/提交要素级标注覆写：隐藏/固定/偏移（em，相对字号）/"
            "显式文字/优先级/reset（visibility|position|text|all 作用域互斥）。"
            "与 UI 拖拽共用同一事务（CAS + 撤销栈 + user-wins）；原子 —— "
            "任一 op 拒绝整笔失败并附 per-op findings。"
        ),
        tier=2,
        domains=["map", "cartography"],
        args_model=LabelAuthorArgs,
        side_effect="state_mutation",
        deterministic=False,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        output_semantic_type="json",
        result_size_policy="inline_small",
        tags=("标注", "标签", "label", "author", "pin", "hide", "offset",
              "文字覆写", "reset"),
    )
    async def webgis_label_author(
        session_id: str,
        layer_id: str,
        ops: Optional[List[Dict[str, Any]]] = None,
        labels_config: Optional[Dict[str, Any]] = None,
        preview_only: bool = False,
        expected_revision: Optional[int] = None,
    ) -> dict:
        from app.services.mapspec_store import mapspec_store
        from app.services.gis_world_state import apply_gis_mutation
        from app.services.mapspec.intents import PatchLayerLabelAuthoringIntent

        ops = ops or []
        if not ops and not labels_config:
            return {"success": False,
                    "message": "ops and/or labels_config required."}

        if preview_only:
            spec = await mapspec_store.get_mapspec(session_id)
            if not isinstance(spec, dict):
                return {"success": False, "message": "Session MapSpec unavailable."}
            layers = spec.get("layers") if isinstance(spec.get("layers"), list) else []
            layer = next((ly for ly in layers if isinstance(ly, dict)
                          and ly.get("id") == layer_id), None)
            if layer is None:
                return {"success": False,
                        "message": f"Layer {layer_id} not found.",
                        "correction_hint": "Re-read MapSpec for valid layer ids."}
            label_cfg = layer.get("label") if isinstance(layer.get("label"), dict) else {}
            if not label_cfg.get("field"):
                return {"success": False,
                        "message": f"Layer {layer_id} has no label config.",
                        "correction_hint": (
                            "Label authoring requires layer.label.field — "
                            "enable labels for the layer first."
                        )}
            current = []
            authoring = label_cfg.get("authoring")
            if isinstance(authoring, dict) and isinstance(authoring.get("overrides"), list):
                current = [dict(o) for o in authoring["overrides"]
                           if isinstance(o, dict)]
            sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
            src = sources.get(layer.get("source"))
            known = None
            if isinstance(src, dict):
                data = src.get("inlineData")
                if isinstance(data, dict) and isinstance(data.get("features"), list):
                    known = data["features"]
            pv = _la.preview_authoring_ops(
                current, ops, layer_id=layer_id, known_features=known)
            return {
                "success": not pv.findings and not any(
                    r.action == "rejected" for r in pv.reports),
                "layer_id": layer_id,
                "preview": {
                    "reports": [
                        {"featureId": r.feature_id, "action": r.action,
                         "idKey": r.id_key,
                         "before": r.before, "after": r.after,
                         "findings": r.findings}
                        for r in pv.reports
                    ][:_MAX_INSPECT_FEATURES],
                    "findings": pv.findings,
                    "unresolved_targets": pv.unresolved_targets,
                },
                "summary": "preview only — nothing committed",
            }

        intent = PatchLayerLabelAuthoringIntent(
            layer_id=layer_id, ops=ops,
            labels_config=labels_config or None)
        try:
            result = await apply_gis_mutation(
                session_id, intent, origin="agent", actor="label_author_tool",
                expected_revision=expected_revision,
            )
        except Exception as exc:  # noqa: BLE001 — 工具边界如实透传
            logger.warning("webgis_label_author failed: %s", exc)
            return {"success": False, "message": str(exc)[:300]}
        out: Dict[str, Any] = {
            "success": bool(result.success if hasattr(result, "success") else
                            not result.is_error),
        }
        for key in ("mutation_revision", "mapspec_fingerprint", "checkpoint_id",
                    "correction_hint", "message"):
            val = getattr(result, key, None)
            if val is not None:
                out[key] = val
        if getattr(result, "is_error", False):
            out["message"] = getattr(result, "error_msg", "") or out.get("message")
            out["success"] = False
        else:
            spec_after = getattr(result, "mapspec", None)
            if isinstance(spec_after, dict):
                # 有界回执：新覆写摘要（不回发全 spec —— 结果封套已有）。
                for ly in spec_after.get("layers", []) or []:
                    if isinstance(ly, dict) and ly.get("id") == layer_id:
                        a = (ly.get("label") or {}).get("authoring") or {}
                        out["overrides_applied"] = len(a.get("overrides") or [])
                        break
            out["summary"] = f"label authoring committed on {layer_id}"
        return out
