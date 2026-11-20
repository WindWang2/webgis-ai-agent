"""G40 —— 图层制图符号资产绑定服务（UI 与 Pi 同一入口，WP07）。

候选构建/资格检查/预览/应用与 G33 thematic_authoring 同构：

- **预览零 mutation**：解析钉版本规范形 + typed 参数归一 + 层资格检查，
  返回候选绑定块与 swatch 诊断回执（不写 MapSpec）。
- **单笔 CAS 应用**：同 id 整层 upsert（一次
  ``apply_gis_mutation(UpsertLayerIntent(layer, source_data=None),
  expected_revision)`` —— 一次 CAS / 一个 checkpoint / 一条撤销栈记录）。
  只写本域键（``symbol_asset`` 块 + 有界 prev_paint_snapshot）；label/
  bivariate/scale_profile/visibility/filter/paint 其余键逐字保留。
- **锁纪律**：agent/system origin 命中 workbench.lockedLayerIds 由生命周期
  引擎统一拒绝（layer_locked）；user origin 是用户显式决定（引擎豁免）。
  绑定不改变 visible/其它状态（S22）。
- **解绑**：恢复 prev_paint_snapshot 中的本域前键（无快照 = 仅移除块），
  同样单笔 CAS；恢复操作不删除资产（其它层可继续用）。
- **CAS 冲突** → SUPERSEDED 409 + 差异提示，绝不盲重放旧候选（S20）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.lib.cartography.symbol_asset import (
    SymbolAssetDocument,
    SymbolAssetError,
    normalize_params,
    params_digest,
)
from app.services.cartographic_assets.catalog import (
    CartographicAssetError,
    resolve_version_document,
)

logger = logging.getLogger(__name__)

SYMBOL_ASSET_KEY = "symbol_asset"

#: 点/面绑定的层型资格（开放面诚实披露：其余层型不支持）。
POINT_LAYER_TYPES = ("circle", "symbol")
TEXTURE_LAYER_TYPES = ("fill",)

_MAX_CATEGORY_RULES = 64
#: prev_paint_snapshot 有界（只快照渲染投影将接管的 paint/layout 键）。
_SNAPSHOT_PAINT_KEYS_POINT = ("circle-color", "circle-opacity", "circle-radius",
                              "circle-stroke-color", "circle-stroke-width",
                              "color", "opacity", "radius")
_SNAPSHOT_PAINT_KEYS_TEXTURE = ("fill-color", "fill-opacity", "fill-outline-color",
                                "color", "opacity")


class SymbolBindingError(Exception):
    """绑定操作非法（code 稳定；路由层映射 HTTP）。"""

    def __init__(self, code: str, message: str = "", *, status_code: int = 422,
                 correction_hint: str = "", **extra: Any) -> None:
        self.code = code
        self.message = message or code
        self.status_code = status_code
        self.correction_hint = correction_hint
        self.extra = extra
        super().__init__(f"{code}: {message}")

    def payload(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "success": False, "code": self.code, "message": self.message,
        }
        if self.correction_hint:
            out["correction_hint"] = self.correction_hint
        out.update({k: v for k, v in self.extra.items() if v is not None})
        return out


def http_status(exc: SymbolBindingError) -> int:
    return exc.status_code


# ── 会话/层解析（与 thematic_authoring 同源权威） ─────────────────────────


async def _read_mapspec(session_id: str) -> Optional[Dict[str, Any]]:
    from app.services.mapspec_store import mapspec_store

    return await mapspec_store.get_mapspec(session_id)


async def _current_revision(session_id: str) -> int:
    """CAS 令牌 = 会话状态权威修订（与 G33/G20 同口径）。"""
    from app.services.session_data import session_data_manager

    getter = getattr(session_data_manager, "get_state_field", None)
    raw = await getter(session_id, "_cartographic_mutation_revision") if callable(getter) else None
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _find_layer(mapspec: Optional[Dict[str, Any]],
                layer_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(mapspec, dict):
        return None
    for layer in mapspec.get("layers") or []:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            return layer
    return None


# ── 资格检查 ───────────────────────────────────────────────────────────────


def _check_layer_eligibility(layer: Dict[str, Any], kind: str) -> None:
    ltype = str(layer.get("type") or "")
    allowed = POINT_LAYER_TYPES if kind == "point_symbol" else TEXTURE_LAYER_TYPES
    if ltype not in allowed:
        raise SymbolBindingError(
            "LAYER_TYPE_MISMATCH",
            f"{kind} 绑定要求层型 ∈ {allowed}，实际 {ltype!r}",
            status_code=409,
            correction_hint="换目标层，或先把层重建为点(circle)/面(fill)语义层。")
    src = str(layer.get("source") or "")
    if not src:
        raise SymbolBindingError(
            "MISSING_SOURCE", "目标层缺少 source 引用", status_code=409)


async def inspect_layer_binding(
    session_id: str, layer_id: str, *, owner_token: Optional[str] = None,
) -> Dict[str, Any]:
    """当前绑定 + 层资格 + builtin 目录页（有界；零 mutation）。"""
    from app.lib.cartography.symbol_asset import builtin_asset_index

    mapspec = await _read_mapspec(session_id)
    layer = _find_layer(mapspec, layer_id)
    if layer is None:
        raise SymbolBindingError(
            "UNKNOWN_LAYER", f"会话无图层 {layer_id!r}", status_code=404)
    current = layer.get(SYMBOL_ASSET_KEY)
    eligibility = {
        "layer_type": str(layer.get("type") or ""),
        "supports_point_symbol": str(layer.get("type") or "") in POINT_LAYER_TYPES,
        "supports_area_texture": str(layer.get("type") or "") in TEXTURE_LAYER_TYPES,
    }
    return {
        "success": True,
        "layer_id": layer_id,
        "current_binding": _binding_summary(current),
        "eligibility": eligibility,
        "revision": await _current_revision(session_id),
        "builtin_assets": builtin_asset_index(),
    }


def _binding_summary(binding: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(binding, dict):
        return None
    out = {
        "asset_id": binding.get("asset_id"),
        "revision": binding.get("revision"),
        "kind": binding.get("kind"),
        "content_digest": binding.get("content_digest"),
        "params": binding.get("params"),
    }
    if isinstance(binding.get("category_binding"), dict):
        cb = dict(binding["category_binding"])
        cb["map"] = list(cb.get("map") or [])[:_MAX_CATEGORY_RULES]
        out["category_binding"] = cb
    return out


# ── 候选构建（preview/apply 共用同一确定性构建器） ─────────────────────────


class _Candidate:
    def __init__(self, block: Dict[str, Any], doc: SymbolAssetDocument,
                 meta: Dict[str, Any]) -> None:
        self.block = block
        self.doc = doc
        self.meta = meta


def _resolve_db():
    from app.core.database import SessionLocal

    return SessionLocal()


def _build_candidate(
    db: Any, session_id: str, layer: Dict[str, Any], plan: Dict[str, Any], *,
    user_id: Optional[str], org_id: Any, role: Optional[str],
) -> _Candidate:
    """typed 计划 → 候选绑定块（解析钉版本 + 参数归一 + 类别映射检查）。"""
    asset_id = str(plan.get("asset_id") or "")
    revision = int(plan.get("revision") or 0)
    if not asset_id.startswith(("cartoasset.",)):
        raise SymbolBindingError(
            "INVALID_ASSET_REF", f"asset_id 必须以 cartoasset. 开头: {asset_id!r}")
    if revision < 1:
        raise SymbolBindingError(
            "INVALID_ASSET_REF", "revision 必须 ≥ 1")
    try:
        doc, meta = resolve_version_document(
            db, asset_id, revision, user_id=user_id, org_id=org_id, role=role,
            for_apply=True)
    except CartographicAssetError as exc:
        raise SymbolBindingError(
            "ASSET_" + exc.reason if not exc.reason.startswith("asset_") else exc.reason.upper(),
            str(exc), status_code=404 if exc.reason in (
                "asset_not_found", "asset_revision_not_found") else 409,
        ) from exc

    _check_layer_eligibility(layer, doc.kind)

    try:
        params = normalize_params(doc.kind, plan.get("params"), doc=doc)
    except SymbolAssetError as exc:
        raise SymbolBindingError(
            "INVALID_PARAMS", str(exc), param_code=exc.code) from exc

    block: Dict[str, Any] = {
        "schema_version": 1,
        "asset_id": asset_id,
        "revision": revision,
        "content_digest": str(meta["content_digest"]),
        "kind": doc.kind,
        "params": params,
    }

    category_binding = _build_category_binding(db, layer, plan, doc,
                                               user_id=user_id, org_id=org_id,
                                               role=role)
    if category_binding is not None:
        block["category_binding"] = category_binding

    return _Candidate(block, doc, meta)


def _build_category_binding(
    db: Any, layer: Dict[str, Any], plan: Dict[str, Any], doc: SymbolAssetDocument, *,
    user_id: Optional[str], org_id: Any, role: Optional[str],
) -> Optional[Dict[str, Any]]:
    raw = plan.get("category_binding")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SymbolBindingError("INVALID_CATEGORY_BINDING", "category_binding 必须是对象")
    field = str(raw.get("field") or "").strip()
    if not field:
        raise SymbolBindingError(
            "INVALID_CATEGORY_BINDING", "category_binding.field 必填")
    key_type = str(raw.get("key_type") or "string")
    if key_type not in ("number", "string", "bool"):
        raise SymbolBindingError(
            "INVALID_CATEGORY_BINDING", f"key_type {key_type!r} 非法")
    rules_raw = raw.get("map") or []
    if not isinstance(rules_raw, list) or len(rules_raw) > _MAX_CATEGORY_RULES:
        raise SymbolBindingError(
            "INVALID_CATEGORY_BINDING", f"map 规则数 ∉ [0,{_MAX_CATEGORY_RULES}]")
    rules: List[Dict[str, Any]] = []
    for r in rules_raw:
        if not isinstance(r, dict):
            raise SymbolBindingError("INVALID_CATEGORY_BINDING", "map 项必须是对象")
        key = r.get("key")
        if key_type == "number":
            if isinstance(key, bool) or not isinstance(key, (int, float)):
                raise SymbolBindingError(
                    "INVALID_CATEGORY_BINDING",
                    f"key {key!r} 与 key_type=number 不符（拒绝字符串数值/bool）")
        elif key_type == "bool":
            if not isinstance(key, bool):
                raise SymbolBindingError(
                    "INVALID_CATEGORY_BINDING", f"key {key!r} 与 key_type=bool 不符")
        elif not isinstance(key, str):
            raise SymbolBindingError(
                "INVALID_CATEGORY_BINDING", f"key {key!r} 与 key_type=string 不符")
        rule_asset = str(r.get("asset_id") or "")
        rule_rev = int(r.get("revision") or 0)
        if not rule_asset.startswith("cartoasset."):
            raise SymbolBindingError(
                "INVALID_CATEGORY_BINDING", f"规则 asset_id 非法: {rule_asset!r}")
        try:
            rule_doc, _ = resolve_version_document(
                db, rule_asset, rule_rev, user_id=user_id, org_id=org_id,
                role=role, for_apply=True)
        except CartographicAssetError as exc:
            raise SymbolBindingError(
                "CATEGORY_ASSET_" + exc.reason.upper(), str(exc),
                status_code=404) from exc
        if rule_doc.kind != doc.kind:
            raise SymbolBindingError(
                "CATEGORY_KIND_MISMATCH",
                f"规则资产 {rule_asset} kind={rule_doc.kind} 与主绑定 {doc.kind} 不符")
        try:
            rule_params = normalize_params(
                rule_doc.kind, r.get("params"), doc=rule_doc)
        except SymbolAssetError as exc:
            raise SymbolBindingError(
                "INVALID_PARAMS", f"类别规则: {exc}", param_code=exc.code) from exc
        rules.append({
            "key": key,
            "asset_id": rule_asset,
            "revision": rule_rev,
            "content_digest": rule_doc.content_digest,
            "params": rule_params,
        })
    other_mode = str(raw.get("other_mode") or "keep_current")
    if other_mode not in ("fallback_asset", "keep_current"):
        raise SymbolBindingError(
            "INVALID_CATEGORY_BINDING", f"other_mode {other_mode!r} 非法")
    out: Dict[str, Any] = {
        "field": field, "key_type": key_type, "map": rules,
        "other_mode": other_mode,
    }
    other_asset = raw.get("other_asset")
    if other_mode == "fallback_asset":
        if not isinstance(other_asset, str) or not other_asset.startswith("cartoasset."):
            raise SymbolBindingError(
                "INVALID_CATEGORY_BINDING",
                "other_mode=fallback_asset 需要 other_asset（cartoasset.*）")
        other_rev = int(raw.get("other_revision") or 0)
        try:
            other_doc, _ = resolve_version_document(
                db, other_asset, other_rev, user_id=user_id, org_id=org_id,
                role=role, for_apply=True)
        except CartographicAssetError as exc:
            raise SymbolBindingError(
                "CATEGORY_ASSET_" + exc.reason.upper(), str(exc),
                status_code=404) from exc
        if other_doc.kind != doc.kind:
            raise SymbolBindingError(
                "CATEGORY_KIND_MISMATCH", "other_asset kind 与主绑定不符")
        out["other_asset"] = other_asset
        out["other_revision"] = other_rev
        out["other_content_digest"] = other_doc.content_digest
    elif other_asset is not None:
        raise SymbolBindingError(
            "INVALID_CATEGORY_BINDING",
            "other_asset 仅在 other_mode=fallback_asset 时允许")
    return out


def _receipt(
    session_id: str, layer: Dict[str, Any], candidate: Optional[_Candidate], *,
    committed: bool, revision: Optional[int] = None,
    mapspec_fingerprint: Optional[str] = None,
    can_undo: bool = False, unbound: bool = False,
) -> Dict[str, Any]:
    layer_id = str(layer.get("id") or "")
    out: Dict[str, Any] = {
        "success": True,
        "layer_id": layer_id,
        "committed": committed,
    }
    if candidate is not None:
        block = candidate.block
        params = dict(block.get("params") or {})
        out.update({
            "asset": {
                "asset_id": block["asset_id"],
                "revision": block["revision"],
                "kind": block["kind"],
                "content_digest": block["content_digest"],
                "name": candidate.meta.get("name"),
                "builtin": bool(candidate.meta.get("builtin")),
            },
            "params": params,
            "binding_fingerprint": params_digest(
                block["kind"], {"block": block}),
        })
        if "category_binding" in block:
            out["category_binding"] = {
                "rules": len(block["category_binding"]["map"]),
                "field": block["category_binding"]["field"],
                "other_mode": block["category_binding"]["other_mode"],
            }
    if unbound:
        out["unbound"] = True
    if committed:
        out["revision"] = revision
        out["mapspec_fingerprint"] = mapspec_fingerprint
        out["can_undo"] = can_undo
        out["mutations"] = 1
    return out


# ── 预览 / 应用 / 解绑 ─────────────────────────────────────────────────────


async def preview_binding(
    session_id: str, layer_id: str, plan: Dict[str, Any], *,
    owner_token: Optional[str] = None, user_id: Optional[str] = None,
    org_id: Any = None, role: Optional[str] = None,
) -> Dict[str, Any]:
    """预览：构建候选（与 apply 同一构建器），零 mutation。"""
    mapspec = await _read_mapspec(session_id)
    layer = _find_layer(mapspec, layer_id)
    if layer is None:
        raise SymbolBindingError(
            "UNKNOWN_LAYER", f"会话无图层 {layer_id!r}", status_code=404)
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        candidate = _build_candidate(
            db, session_id, layer, plan, user_id=user_id, org_id=org_id, role=role)
        receipt = _receipt(session_id, layer, candidate, committed=False)
    finally:
        db.close()
    receipt["revision"] = await _current_revision(session_id)
    receipt["preview"] = {
        "swatch": {
            "asset_id": candidate.block["asset_id"],
            "revision": candidate.block["revision"],
            "content_digest": candidate.block["content_digest"],
            "params": candidate.block["params"],
        },
        "disclosures": _disclosures(candidate),
    }
    return receipt


def _disclosures(candidate: _Candidate) -> List[str]:
    notes: List[str] = []
    params = candidate.block.get("params") or {}
    if candidate.block["kind"] == "point_symbol":
        notes.append(
            "点符号旋转为 viewport 对齐（视觉旋转，不随地图 bearing 转动）；"
            "map/pitch 对齐未实现，已拒绝。")
        notes.append(
            "图标默认不参与碰撞省略（icon-allow-overlap=true，如实重叠）；"
            "密集区重叠是预期披露而非数据缺失。")
        if params.get("rotation_deg"):
            notes.append(f"rotation_deg={params['rotation_deg']:g}（viewport 度）")
    else:
        notes.append(
            "纹理间距为屏幕/纸面显示单位合同（不随 zoom 缩放、不暗示地面间距）。")
        if params.get("bg_color") == "transparent":
            notes.append("纹理背景透明（底层内容透出；不混入未知不透明底）。")
    return notes


async def apply_binding(
    session_id: str, layer_id: str, plan: Dict[str, Any], *,
    expected_revision: int, origin: str = "user", actor: str = "symbol_asset_panel",
    mutation_id: Optional[str] = None, owner_token: Optional[str] = None,
    user_id: Optional[str] = None, org_id: Any = None, role: Optional[str] = None,
) -> Dict[str, Any]:
    """单笔提交：同 id 整层 upsert（一次 CAS / 一个 checkpoint）。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent

    mapspec = await _read_mapspec(session_id)
    layer = _find_layer(mapspec, layer_id)
    if layer is None:
        raise SymbolBindingError(
            "UNKNOWN_LAYER", f"会话无图层 {layer_id!r}", status_code=404)
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        candidate = _build_candidate(
            db, session_id, layer, plan, user_id=user_id, org_id=org_id, role=role)
    finally:
        db.close()

    updated_layer = dict(layer)  # 逐键保留兄弟字段（label/bivariate/…）
    prev_binding = layer.get(SYMBOL_ASSET_KEY)
    block = dict(candidate.block)
    # 换绑/重绑：prev_paint_snapshot 只保留**本域首次绑定前**的快照
    #（换版本/换参数不覆盖历史快照 —— 解绑恢复到最初语义）。
    if isinstance(prev_binding, dict) and isinstance(
            prev_binding.get("prev_paint_snapshot"), dict):
        block["prev_paint_snapshot"] = dict(prev_binding["prev_paint_snapshot"])
    else:
        block["prev_paint_snapshot"] = _snapshot_relevant_paint(layer, block["kind"])
    block["applied_by"] = {
        "origin": origin, "actor": actor,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    updated_layer[SYMBOL_ASSET_KEY] = block

    result = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer=updated_layer, source_data=None),  # type: ignore[arg-type]
        origin=origin, actor=actor, expected_revision=int(expected_revision),
        mutation_id=mutation_id,
    )
    if result.superseded:
        raise SymbolBindingError(
            "CAS_SUPERSEDED",
            "MapSpec 修订在提交期间变化；nothing was written",
            status_code=409,
            correction_hint="重新 inspect 取当前 revision，重做计划后重试。")
    if result.is_error:
        raise SymbolBindingError(
            "MUTATION_REJECTED",
            str(result.error_msg or "MapSpec mutation was rejected"),
            status_code=422,
            correction_hint=str(result.correction_hint or ""),
        )
    receipt = _receipt(
        session_id, updated_layer, candidate, committed=True,
        revision=int(result.mutation_revision or expected_revision),
        mapspec_fingerprint=result.mapspec_fingerprint,
        can_undo=True)
    if getattr(result, "duplicate", False):
        receipt["deduplicated"] = True
    return receipt


def _snapshot_relevant_paint(layer: Dict[str, Any], kind: str) -> Dict[str, Any]:
    keys = _SNAPSHOT_PAINT_KEYS_POINT if kind == "point_symbol" \
        else _SNAPSHOT_PAINT_KEYS_TEXTURE
    snap: Dict[str, Any] = {}
    paint = layer.get("paint") if isinstance(layer.get("paint"), dict) else {}
    for k in keys:
        if k in paint:
            snap[k] = paint[k]
    return snap


async def unbind_layer(
    session_id: str, layer_id: str, *, expected_revision: int,
    origin: str = "user", actor: str = "symbol_asset_panel",
    mutation_id: Optional[str] = None, owner_token: Optional[str] = None,
) -> Dict[str, Any]:
    """解绑：移除块 + 恢复本域接管前的 paint 快照（单笔 CAS）。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent

    mapspec = await _read_mapspec(session_id)
    layer = _find_layer(mapspec, layer_id)
    if layer is None:
        raise SymbolBindingError(
            "UNKNOWN_LAYER", f"会话无图层 {layer_id!r}", status_code=404)
    binding = layer.get(SYMBOL_ASSET_KEY)
    if not isinstance(binding, dict):
        raise SymbolBindingError(
            "NO_BINDING", f"图层 {layer_id!r} 无 symbol_asset 绑定", status_code=409)

    updated_layer = dict(layer)
    updated_layer.pop(SYMBOL_ASSET_KEY, None)
    snapshot = binding.get("prev_paint_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        paint = dict(layer.get("paint") or {})
        for k, v in snapshot.items():
            paint[k] = v
        updated_layer["paint"] = paint

    result = await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer=updated_layer, source_data=None),  # type: ignore[arg-type]
        origin=origin, actor=actor, expected_revision=int(expected_revision),
        mutation_id=mutation_id,
    )
    if result.superseded:
        raise SymbolBindingError(
            "CAS_SUPERSEDED", "MapSpec 修订在提交期间变化；nothing was written",
            status_code=409,
            correction_hint="重新 inspect 取当前 revision 后重试解绑。")
    if result.is_error:
        raise SymbolBindingError(
            "MUTATION_REJECTED", str(result.error_msg or "rejected"), status_code=422)
    receipt = _receipt(
        session_id, updated_layer, None, committed=True, unbound=True,
        revision=int(result.mutation_revision or expected_revision),
        mapspec_fingerprint=result.mapspec_fingerprint, can_undo=True)
    receipt["restored_paint"] = bool(isinstance(snapshot, dict) and snapshot)
    return receipt


def plan_digest_of(plan: Dict[str, Any]) -> str:
    """绑定计划确定性指纹（工具 apply 幂等 mutation_id 用；有界）。"""
    import hashlib

    from app.lib.data.fingerprints import canonical_dumps

    return hashlib.sha256(
        canonical_dumps(plan or {}).encode("utf-8")).hexdigest()[:24]
