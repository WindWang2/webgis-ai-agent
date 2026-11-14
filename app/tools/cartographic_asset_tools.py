"""G40 制图符号资产工具（WP10）——list / create / preview / apply / unbind。

与 UI 面板同一领域服务（cartographic_assets.catalog / binding）、同一预览
构建器、同一单笔 CAS 提交语义。工具回执**有界**：不返回完整规范形/全量
目录进上下文（列表截断、内容按 digest 引用）；apply 带 expected_revision；
side_effect 如实声明。资产身份 = asset_id + revision + content_digest（名字
只是显示名）；常用路径传资产/层 refs 与有限参数 —— 不接受模型生成 SVG 源码
绕开校验（svg_base64 仍走受控子集导入器）。
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_MAX_TOOL_LIST = 24
_MAX_SVG_B64_CHARS = 700_000  # ~512KB 原始字节


class ListArgs(BaseModel):
    kind: Optional[str] = Field(
        None, description="过滤：point_symbol | area_texture；缺省 = 全部")
    limit: int = Field(default=12, ge=1, le=24)


class CreateArgs(BaseModel):
    kind: str = Field(..., description="point_symbol | area_texture")
    name: str = Field(default="", max_length=120)
    svg_base64: str = Field(
        ..., max_length=_MAX_SVG_B64_CHARS,
        description="受控 SVG 子集字节（base64；script/外链/url()/text 等拒绝）")
    license_note: str = Field(default="", max_length=300)
    publish: bool = Field(default=True)


class PlanArgs(BaseModel):
    layer_id: str = Field(..., min_length=1, max_length=200)
    plan: Dict[str, Any] = Field(
        ...,
        description=(
            "typed 绑定计划（schema_version=1）：asset_id（cartoasset.*）+ "
            "revision + params（点符号 size_px/anchor/rotation_deg/opacity/"
            "color；面纹理 spacing_px/angle_deg/stroke_width_px/fg_color/"
            "bg_color/pattern_scale，均有上下限）+ 可选 category_binding "
            "{field, key_type: number|string|bool, map:[{key, asset_id, "
            "revision, params}], other_mode}（typed 匹配，不字符串化并类）"))
    session_id: str = ""


class ApplyArgs(PlanArgs):
    expected_revision: int = Field(
        ..., ge=0,
        description="提交时的 MapSpec 修订（CAS）；不匹配返回 superseded")


class UnbindArgs(BaseModel):
    layer_id: str = Field(..., min_length=1, max_length=200)
    expected_revision: int = Field(..., ge=0)
    session_id: str = ""


def _caller_identity():
    from app.services.templates.composition_assets import tool_caller_identity

    return tool_caller_identity()


def register_cartographic_asset_tools(registry: ToolRegistry) -> None:
    """注册 G40 工具（只读×1 + 受控写×4）。"""
    from app.services.cartographic_assets.binding import (
        SymbolBindingError,
        apply_binding,
        inspect_layer_binding,
        preview_binding,
        unbind_layer,
    )

    async def _guard_binding(coro):
        try:
            return await coro
        except SymbolBindingError as exc:
            payload = exc.payload()
            payload["success"] = False
            return payload
        except Exception:  # noqa: BLE001 - 工具错误契约
            logger.warning("[carto_assets] tool failed", exc_info=True)
            return {
                "success": False, "code": "SYMBOL_ASSET_TOOL_FAILED",
                "message": "symbol asset operation failed",
                "correction_hint": "Check the layer id, plan shape and revision.",
            }

    def _bound_receipt(result: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "success": bool(result.get("success")),
            "layer_id": result.get("layer_id"),
            "committed": bool(result.get("committed")),
        }
        if result.get("asset"):
            out["asset"] = result["asset"]
        if result.get("params"):
            out["params"] = result["params"]
        if result.get("category_binding"):
            out["category_binding"] = result["category_binding"]
        if result.get("binding_fingerprint"):
            out["binding_fingerprint"] = result["binding_fingerprint"]
        for key in ("revision", "can_undo", "deduplicated", "unbound",
                    "restored_paint", "mapspec_fingerprint"):
            if result.get(key) is not None:
                out[key] = result[key]
        if result.get("preview"):
            prev = dict(result["preview"])
            sw = prev.get("swatch")
            if isinstance(sw, dict):
                prev["swatch"] = {
                    "asset_id": sw.get("asset_id"),
                    "revision": sw.get("revision"),
                    "content_digest": sw.get("content_digest"),
                }
            out["preview"] = prev
        if result.get("disclosures"):
            out["disclosures"] = list(result["disclosures"])[:8]
        return out

    # ── webgis_symbol_asset_list ──────────────────────────────────────────
    async def _list_assets(kind: Optional[str], limit: int) -> Dict[str, Any]:
        from app.core.database import SessionLocal
        from app.lib.cartography.symbol_asset import builtin_asset_index
        from app.services.cartographic_assets.catalog import list_assets

        user_id, org_id = _caller_identity()
        builtin = [dict(i) for i in builtin_asset_index()
                   if not kind or i.get("kind") == kind]
        db_assets: list = []
        total = 0

        def _db():
            nonlocal db_assets, total
            db = SessionLocal()
            try:
                db_assets, total = list_assets(
                    db, user_id=user_id, org_id=org_id, kind=kind,
                    limit=limit)
            finally:
                db.close()

        import asyncio

        try:
            await asyncio.to_thread(_db)
        except Exception:  # noqa: BLE001 - DB 缺席 → builtin-only（匿名同语义）
            db_assets, total = [], 0
        merged = (builtin + db_assets)[:max(1, min(limit, _MAX_TOOL_LIST))]
        return {
            "success": True,
            "assets": [
                {"asset_id": a.get("asset_id"), "kind": a.get("kind"),
                 "name": a.get("name"), "revision": a.get("revision")
                 or a.get("current_revision"),
                 "content_digest": a.get("content_digest"),
                 "builtin": bool(a.get("builtin"))}
                for a in merged
            ],
            "total_listed": len(merged),
        }

    registry.register(
        "webgis_symbol_asset_list",
        "列出可用制图符号资产（内置点符号/面纹理 + 当前身份可见的库资产）。"
        "返回 asset_id/revision/content_digest —— 绑定计划引用这三元组。",
        _list_assets,
        args_model=ListArgs,
        tier=2, domains=["cartography", "mapspec"],
        side_effect="cacheable_read",
        tags=("符号资产", "目录", "symbol assets"),
        output_semantic_type="json",
        result_size_policy="bounded",
        failure_modes=("invalid_args", "permission_denied"),
    )

    # ── webgis_symbol_asset_create ────────────────────────────────────────
    async def _create_asset(kind: str, name: str, svg_base64: str,
                            license_note: str, publish: bool) -> Dict[str, Any]:
        from app.core.database import SessionLocal
        from app.lib.cartography.svg_import import import_svg_asset
        from app.lib.cartography.symbol_asset import SymbolAssetError
        from app.services.cartographic_assets.catalog import (
            CartographicAssetError, create_asset,
        )

        user_id, org_id = _caller_identity()
        if user_id is None:
            return {
                "success": False, "code": "ASSET_FORBIDDEN",
                "message": "工具上下文无用户身份 —— 不能创建库资产（可用内置资产）",
            }
        try:
            svg_bytes = base64.b64decode(svg_base64, validate=True)
            document = import_svg_asset(
                svg_bytes, kind=kind, name=name, license_note=license_note)
        except (SymbolAssetError, ValueError) as exc:
            code = getattr(exc, "code", "SVG_IMPORT_FAILED")
            return {
                "success": False, "code": code,
                "message": str(exc)[:300],
                "correction_hint": "受控子集：无 script/外链/CSS url()/text/"
                                   "clip/filter/渐变；viewBox 显式 4-256。",
            }
        import asyncio

        def _db():
            db = SessionLocal()
            try:
                asset, version = create_asset(
                    db, document=document, creator_id=user_id, org_id=org_id,
                    publish=publish)
                db.commit()
                return {
                    "asset_id": asset.id, "revision": version.revision,
                    "content_digest": version.content_digest,
                    "kind": asset.kind, "name": asset.name,
                }
            except CartographicAssetError:
                db.rollback()
                raise
            finally:
                db.close()

        try:
            created = await asyncio.to_thread(_db)
        except CartographicAssetError as exc:
            return {"success": False, "code": exc.reason.upper(),
                    "message": str(exc)[:300]}
        created["success"] = True
        return created

    registry.register(
        "webgis_symbol_asset_create",
        "从受控 SVG 子集字节导入创建点符号/面纹理资产（base64；服务端单一"
        "清洗口径，非法元素 typed 拒绝）。返回钉版本三元组供绑定。",
        _create_asset,
        args_model=CreateArgs,
        tier=2, domains=["cartography", "mapspec"],
        side_effect="artifact_creation",
        tags=("符号资产", "SVG 导入", "symbol assets"),
        output_semantic_type="json",
        result_size_policy="bounded",
        failure_modes=("invalid_args", "svg_rejected", "permission_denied"),
    )

    # ── webgis_symbol_layer_inspect（复用 list 语义 + 层资格） ────────────
    async def _inspect_layer(layer_id: str, session_id: str = "") -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "session_id required"}
        try:
            result = await inspect_layer_binding(session_id, layer_id)
        except SymbolBindingError as exc:
            return exc.payload()
        except Exception:  # noqa: BLE001
            logger.warning("[carto_assets] inspect failed", exc_info=True)
            return {"success": False, "code": "SYMBOL_ASSET_TOOL_FAILED",
                    "message": "inspect failed"}
        builtin = result.get("builtin_assets") or []
        return {
            "success": True,
            "layer_id": layer_id,
            "revision": result.get("revision"),
            "eligibility": result.get("eligibility"),
            "current_binding": result.get("current_binding"),
            "builtin_assets": [
                {"asset_id": a.get("asset_id"), "kind": a.get("kind"),
                 "revision": a.get("revision"),
                 "content_digest": a.get("content_digest")}
                for a in builtin[:_MAX_TOOL_LIST]
            ],
        }

    registry.register(
        "webgis_symbol_layer_inspect",
        "检查图层的符号资产绑定资格与当前绑定（层型/修订/内置目录）。",
        _inspect_layer,
        args_model=PlanArgs,
        tier=2, domains=["cartography", "mapspec"],
        side_effect="cacheable_read",
        tags=("符号资产", "图层", "symbol assets"),
        output_semantic_type="json",
        result_size_policy="bounded",
        failure_modes=("invalid_args", "missing_data"),
    )

    # ── webgis_symbol_layer_preview ───────────────────────────────────────
    async def _preview(layer_id: str, plan: Dict[str, Any],
                       session_id: str = "") -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "session_id required"}
        user_id, org_id = _caller_identity()
        return _bound_receipt(await _guard_binding(preview_binding(
            session_id, layer_id, plan, user_id=user_id, org_id=org_id)))

    registry.register(
        "webgis_symbol_layer_preview",
        "预览符号资产绑定（零 mutation）：typed 校验 + 层资格 + 绑定指纹 + "
        "披露；与 apply 同一构建器。",
        _preview,
        args_model=PlanArgs,
        tier=2, domains=["cartography", "mapspec"],
        side_effect="cacheable_read",
        tags=("符号资产", "预览", "symbol assets"),
        output_semantic_type="json",
        result_size_policy="bounded",
        failure_modes=("invalid_args", "validation_error", "missing_data"),
    )

    # ── webgis_symbol_layer_apply ─────────────────────────────────────────
    async def _apply(layer_id: str, plan: Dict[str, Any],
                     expected_revision: int, session_id: str = "") -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "session_id required"}
        user_id, org_id = _caller_identity()
        from app.services.cartographic_assets.binding import plan_digest_of

        mutation_id = None
        try:
            mutation_id = f"symbolasset:{plan_digest_of(plan)}:r{expected_revision}"
        except Exception:  # noqa: BLE001 - 指纹失败不阻塞（服务端仍 CAS）
            mutation_id = None
        return _bound_receipt(await _guard_binding(apply_binding(
            session_id, layer_id, plan, expected_revision=expected_revision,
            origin="agent", actor="pi:symbol_layer_apply",
            mutation_id=mutation_id, user_id=user_id, org_id=org_id)))

    registry.register(
        "webgis_symbol_layer_apply",
        "应用符号资产绑定到图层（单笔 MapSpec CAS + checkpoint + 可 undo）。"
        "资产钉 revision+content_digest；锁层由生命周期引擎拒绝；层上其余"
        "表达（label/bivariate/scale_profile）逐字保留。",
        _apply,
        args_model=ApplyArgs,
        tier=2, domains=["cartography", "mapspec"],
        side_effect="state_mutation",
        tags=("符号资产", "提交", "symbol assets"),
        output_semantic_type="json",
        result_size_policy="bounded",
        required_context=("map_state",),
        map_mutations=("symbol_asset",),
        failure_modes=("invalid_args", "superseded", "validation_error",
                       "missing_data", "asset_unavailable"),
    )

    # ── webgis_symbol_layer_unbind ────────────────────────────────────────
    async def _unbind(layer_id: str, expected_revision: int,
                      session_id: str = "") -> Dict[str, Any]:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "session_id required"}
        return _bound_receipt(await _guard_binding(unbind_layer(
            session_id, layer_id, expected_revision=expected_revision,
            origin="agent", actor="pi:symbol_layer_unbind")))

    registry.register(
        "webgis_symbol_layer_unbind",
        "解除图层符号资产绑定并恢复绑定前的 paint 快照（单笔 CAS）。",
        _unbind,
        args_model=UnbindArgs,
        tier=2, domains=["cartography", "mapspec"],
        side_effect="state_mutation",
        tags=("符号资产", "解绑", "symbol assets"),
        output_semantic_type="json",
        result_size_policy="bounded",
        required_context=("map_state",),
        map_mutations=("symbol_asset",),
        failure_modes=("invalid_args", "superseded", "missing_data"),
    )
