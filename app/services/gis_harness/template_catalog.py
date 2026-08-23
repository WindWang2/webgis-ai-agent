"""TemplateCatalog —— Harness 的统一模板查询门面（§Phase G）。

不物理合并两个 registry（ProductTemplateRegistry = 产品结构；TemplateRegistry
= 样式预设/组合），但 Harness 通过本目录统一查询与校验，不再只看
ProductTemplateRegistry。进程内 registry 直查，O(1)/有界扫描；DB 用户模板
仍由 list_templates 工具面按需合并（本目录不做每请求 DB 查询）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.schemas.template_schema import TemplateCompatibility
from app.services.gis_harness.product_templates import (
    MapProductTemplate,
    get_product_template_registry,
)

_STYLE_KINDS = {"basemap", "symbology", "layout", "thematic", "composite"}


def _infer_style_compatibility(entry: Dict[str, Any]) -> TemplateCompatibility:
    """为没有显式 compatibility 键的样式模板推导默认兼容元数据。

    确定性、纯 dict 操作：按 kind/payload.variant/payload.method 推导
    map model 亲和；推导不出来的字段留空（= 不约束）。
    """
    kind = str(entry.get("kind") or "")
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    variant = str(payload.get("variant") or "")
    method = str(payload.get("method") or "")
    kw = " ".join(str(k) for k in (entry.get("keywords") or []))

    models: List[str] = []
    geometry: List[str] = []
    if kind == "thematic":
        if variant == "heatmap":
            models = ["visual_heatmap"]
            geometry = ["point"]
        elif "lisa" in str(entry.get("id") or ""):
            models = ["hotspot_overlay"]
        elif variant == "choropleth":
            models = ["administrative_choropleth", "aggregate_grid",
                      "administrative_aggregation"]
            geometry = ["polygon"]
        elif method:
            models = ["administrative_choropleth", "aggregate_grid"]
            geometry = ["polygon"]
        else:  # categorical 家族（zoning/soil/geology/facility_type…）
            models = ["categorical_thematic"]
    elif kind == "symbology":
        geo = str(payload.get("geometry") or "")
        geometry = [geo] if geo else []
        if geo == "point":
            models = ["simple_point_map", "point_overlay", "proportional_symbol"]
        elif geo == "polygon":
            models = ["administrative_aggregation", "proximity_overlay"]
        elif geo == "line":
            models = ["categorical_thematic"]
    return TemplateCompatibility(
        compatible_map_models=models,
        geometry_types=geometry,
        output_targets=["interactive_map"],
        renderer_support=["maplibre"],
        style_profile="academic" if ("学术" in kw or "academic" in kw) else "",
    )


class TemplateCatalog:
    """统一查询入口：产品模板 + 样式模板（含 composite）。"""

    def __init__(self) -> None:
        self.products = get_product_template_registry()

    # ── 单查（O(1)）───────────────────────────────────────────────────
    def get_product_template(self, template_id: str) -> Optional[MapProductTemplate]:
        return self.products.get(template_id)

    def get_style_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        if not template_id:
            return None
        from app.schemas.template_registry import get_template_registry
        return get_template_registry().get(template_id)

    # ── 候选检索 ──────────────────────────────────────────────────────
    def find_product_candidates(self, recipe_id: str) -> List[MapProductTemplate]:
        """该 recipe 的全部产品模板（注册序，确定性）。"""
        return [t for t in self.products.values() if t.recipe_id == recipe_id]

    def style_compatibility(self, entry: Dict[str, Any]) -> TemplateCompatibility:
        """显式 compatibility 优先；否则按 kind/payload 推导（不修改注册表）。"""
        raw = entry.get("compatibility")
        if isinstance(raw, dict) and raw:
            try:
                return TemplateCompatibility.model_validate(raw)
            except Exception:  # noqa: BLE001 - 显式键损坏时退到推导并让 validate 报告
                pass
        return _infer_style_compatibility(entry)

    def find_style_candidates(
        self,
        *,
        map_model: str = "",
        kinds: Optional[List[str]] = None,
        geometry: str = "",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """按 MapModel/几何/kind 过滤样式模板（有界、确定性排序）。"""
        from app.schemas.template_registry import get_template_registry
        registry = get_template_registry()
        pool: List[Dict[str, Any]] = []
        for kind in (kinds or sorted(_STYLE_KINDS)):
            pool.extend(registry.by_kind(kind))
        if not map_model and not geometry:
            pool.sort(key=lambda e: (str(e.get("kind")), str(e.get("id"))))
            return pool[:limit]
        matched: List[Dict[str, Any]] = []
        for entry in pool:
            compat = self.style_compatibility(entry)
            if compat.deprecated:
                continue
            if map_model and map_model not in compat.compatible_map_models:
                continue
            if geometry and compat.geometry_types and geometry not in compat.geometry_types:
                continue
            matched.append(entry)
        matched.sort(key=lambda e: (str(e.get("kind")), str(e.get("id"))))
        return matched[:limit]

    # ── composite ─────────────────────────────────────────────────────
    def resolve_composite(
        self, composite_id: str,
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        from app.schemas.template_registry import get_template_registry
        return get_template_registry().expand_composite(composite_id)

    # ── 校验（交叉引用，空列表 = 通过）─────────────────────────────────
    def validate(self) -> List[str]:
        from app.schemas.template_registry import get_template_registry
        issues: List[str] = list(get_template_registry().validate())
        # 产品模板的 style_slot 引用必须真实存在
        for tpl in self.products.values():
            tid = tpl.id
            for role in tpl.layer_roles:
                if role.style_slot and self.get_style_template(role.style_slot) is None:
                    issues.append(
                        f"product template {tid}: layer role style_slot "
                        f"'{role.style_slot}' not found in style registry")
        return issues


_catalog: Optional[TemplateCatalog] = None


def get_template_catalog() -> TemplateCatalog:
    global _catalog
    if _catalog is None:
        _catalog = TemplateCatalog()
    return _catalog


def reset_template_catalog() -> None:
    global _catalog
    _catalog = None


__all__ = [
    "TemplateCatalog",
    "get_template_catalog",
    "reset_template_catalog",
]
