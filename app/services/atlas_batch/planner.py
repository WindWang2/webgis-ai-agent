"""Atlas batch planner —— AtlasJobSpec + MapSpec 文档 → 页绑定/指纹/分类摘要。

确定性纯函数：同一 (spec, document) 恒产生同一绑定集与指纹（canonical JSON
sha256，``plan_ir.digest_of`` 同口径）。页面规划 100% 复用
``publication_ir.plan_publication_pages`` —— 本模块只在其上叠加：

- **页级数据身份**：每页绑定要素计数（经渲染面同一过滤事实源
  ``_page_filtered_doc`` 计数，规划与渲染永不各说各话）、过滤描述符、bounds；
- **分类身份**：每页有效文档（含 frames 驱动的 layerOverrides）全部图层的
  ``legend_spec`` canonical digest —— shared scope 下 QA 逐页比对一致性；
- **页指纹**：上述全部 + 纸张/页码/DPI/编译器键 —— 数据或版面任一变化即变，
  是页缓存与"只刷新该页"的单一身份依据。

红线：不携带要素载荷（digest/计数）；typed 失败沿 ``MapSpecSchemaError``。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.lib.cartography.atlas_job_spec import AtlasJobSpec
from app.lib.cartography.mapspec_schema import MapSpecSchemaError
from app.lib.cartography.plan_ir import digest_of
from app.lib.cartography.publication_ir import (
    PUBLICATION_IR_VERSION,
    PublicationIR,
    plan_publication_pages,
)

#: 页编译语义键（指纹输入）：渲染语义（PublicationIR 版本 + SVG 编译面代次 +
#: 页头注入代次）任一变化 → 全部页指纹失效重渲染，杜绝旧缓存误命中。
ATLAS_COMPILER_KEY = f"{PUBLICATION_IR_VERSION}|atlas-svg-v1|pagehdr-v1"

#: 图册结构指纹前缀（与 publication_export._structural_fingerprint 区分命名域）。
_ATLAS_PAGE_FP_PREFIX = "atlaspage-sha256:"


class AtlasPageBinding(BaseModel):
    """单页绑定（数据身份 + 指纹；无载荷）。"""

    model_config = ConfigDict(frozen=True)

    page_id: str = Field(max_length=120)
    title: str = Field(default="", max_length=200)
    page_number: int = Field(ge=1)
    page_count: int = Field(ge=1)
    cover: bool = False
    bounds: Optional[List[float]] = None
    filter_layer_id: str = Field(default="", max_length=120)
    filter_property: str = Field(default="", max_length=120)
    filter_value: str = Field(default="", max_length=160)
    feature_offset: Optional[int] = Field(default=None, ge=0)
    feature_limit: Optional[int] = Field(default=None, ge=1)
    #: 本页实际绑定的目标图层要素数（封面 = 0；与渲染过滤同一事实源）。
    feature_count: int = Field(ge=0)
    #: 页数据内容 digest（过滤后要素 canonical JSON；无过滤页 = 文档级全量
    #: digest）。**数据值变化（含属性覆写）必须轮换指纹** —— 否则内容寻址
    #: 页缓存会误命中旧页（计数/几何不变的属性变更是最小反例）。
    data_digest: str = ""
    #: 本页有效文档全部图层 legend_spec 的 canonical digest（无 legend = None）。
    legend_digest: Optional[str] = None
    #: 目标图层（过滤层或首个带 legend 的层）的分类摘要（manifest 投影用）。
    classification: Dict[str, Any] = Field(default_factory=dict)
    fingerprint: str = Field(default="")


class AtlasPlan(BaseModel):
    """图册计划（绑定集 + 版面 IR + 降级；确定性）。"""

    model_config = ConfigDict(frozen=True)

    ir: PublicationIR
    bindings: List[AtlasPageBinding]
    compiler_key: str = ATLAS_COMPILER_KEY

    @property
    def ir_fingerprint(self) -> str:
        return self.ir.ir_fingerprint()

    @property
    def content_pages(self) -> List[AtlasPageBinding]:
        return [b for b in self.bindings if not b.cover]


def _page_effective_doc(
    document: Dict[str, Any], page: Any, frames_lookup: List[Any],
) -> Dict[str, Any]:
    """页有效文档（帧覆盖 / 过滤；与渲染面同一事实源）。

    过滤路径委托 ``publication_export._page_filtered_doc``（已测、已锁）——
    规划计数与渲染输入必须出自同一函数，否则指纹语义失真。
    """
    if page.frame_index is not None:
        frame = frames_lookup[page.frame_index] if page.frame_index < len(frames_lookup) else None
        from app.services.publication_export import _apply_frame_overrides

        return _apply_frame_overrides(document, frame)
    if page.filter_layer_id:
        from app.services.publication_export import _page_filtered_doc

        return _page_filtered_doc(document, page)
    return document


def _layer_feature_count(doc: Dict[str, Any], layer_id: str) -> int:
    """目标图层内联要素计数（inlineData/data 同键口径；无载荷 → 0）。"""
    if not layer_id:
        return 0
    layers = doc.get("layers")
    sources = doc.get("sources") if isinstance(doc.get("sources"), dict) else {}
    target_sid = None
    if isinstance(layers, list):
        for layer in layers:
            if isinstance(layer, dict) and layer.get("id") == layer_id:
                target_sid = layer.get("source")
                break
    if not isinstance(target_sid, str):
        return 0
    src = sources.get(target_sid)
    if not isinstance(src, dict):
        return 0
    payload = src.get("inlineData")
    if not isinstance(payload, dict):
        payload = src.get("data") if isinstance(src.get("data"), dict) else {}
    feats = payload.get("features")
    return len(feats) if isinstance(feats, list) else 0


def _legend_digest_and_classification(
    doc: Dict[str, Any], layer_id: str,
) -> tuple[Optional[str], Dict[str, Any]]:
    """页有效文档 → (全图层 legend canonical digest, 目标层分类摘要)。

    digest 输入按 layer id 排序（顺序无关）；目标层 = 过滤层（在场）否则
    首个带 legend_spec 的层。摘要字段与 product_manifest 的 legend 语义
    面对齐（type/field/method/palette/class_count），便于跨页比对与披露。
    """
    layers = [ly for ly in (doc.get("layers") or []) if isinstance(ly, dict)]
    legends: Dict[str, Any] = {}
    target: Optional[Dict[str, Any]] = None
    for layer in layers:
        lid = str(layer.get("id") or "")
        legend = layer.get("legend_spec")
        if not isinstance(legend, dict):
            continue
        legends[lid] = legend
        if layer_id and lid == layer_id:
            target = layer
        elif target is None and not layer_id:
            target = layer
    digest: Optional[str] = None
    if legends:
        digest = "atlaslegend-sha256:" + digest_of(legends)[:40]
    summary: Dict[str, Any] = {}
    if target is not None and isinstance(target.get("legend_spec"), dict):
        legend = target["legend_spec"]
        summary = {
            "type": str(legend.get("type") or "")[:24],
            "field": str(legend.get("field") or legend.get("class_field") or "")[:64],
            "method": str(legend.get("method") or "")[:32],
            "palette_id": str(legend.get("palette_id") or "")[:48],
        }
        if isinstance(legend.get("categories"), list):
            summary["class_count"] = len(legend["categories"])
        elif isinstance(legend.get("colors"), list):
            summary["class_count"] = len(legend["colors"])
        elif isinstance(legend.get("breaks"), list):
            summary["class_count"] = len(legend["breaks"]) + 1
    return digest, {k: v for k, v in summary.items() if v != "" or k == "field"}


def _page_fingerprint(
    binding_fields: Dict[str, Any], *, spec: AtlasJobSpec, ir_fingerprint: str,
    structural_fingerprint: str,
) -> str:
    projection = {
        "binding": binding_fields,
        "classification_scope": spec.classification_scope.value,
        "atlas_title": spec.atlas.atlas_title,
        "target_dpi": spec.target_dpi,
        "ir_fingerprint": ir_fingerprint,
        # R1-P1：页有效文档的结构指纹（layers/paint/thresholds/view/scene/
        # scenario_mode + 源描述符与 feature_count —— 同步链 C14 review P2
        # 同款投影）。样式/阈值/视角变化必须轮换页指纹，否则缓存误命中。
        "structural_fingerprint": structural_fingerprint,
        "compiler_key": ATLAS_COMPILER_KEY,
    }
    return _ATLAS_PAGE_FP_PREFIX + digest_of(projection)[:40]


def _inline_feature_collections(doc: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """全部内联 geojson 源 → {source_id: features}（只读引用，不拷贝）。"""
    sources = doc.get("sources") if isinstance(doc.get("sources"), dict) else {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for sid, src in sources.items():
        if not isinstance(src, dict):
            continue
        payload = src.get("inlineData")
        if not isinstance(payload, dict):
            payload = src.get("data") if isinstance(src.get("data"), dict) else {}
        feats = payload.get("features")
        if isinstance(feats, list):
            out[str(sid)] = [f for f in feats if isinstance(f, dict)]
    return out


def _page_features_digest(effective_doc: Dict[str, Any], layer_id: str) -> str:
    """页数据内容 digest（R1-P1 修复：页渲染输入的**全部**数据内容）。

    页面渲染不止画过滤层 —— 其余内联层照常入图，其内容变化必须轮换指纹
    （否则缓存命中旧页）。``effective_doc`` 已由 ``_page_effective_doc``
    把目标源过滤为本页要素，其余源为全量 —— 直接投影全部源即得
    「目标源页粒度 + 其余源全敏感」的正确身份。
    """
    _ = layer_id  # 过滤已发生在 effective_doc（保留签名便于阅读调用意图）
    collections = _inline_feature_collections(effective_doc)
    projection = {sid: feats for sid, feats in sorted(collections.items())}
    return "atlasdata-sha256:" + digest_of(projection)[:40]


def _page_feature_count(effective_doc: Dict[str, Any], layer_id: str) -> int:
    """页要素计数：过滤层（category/feature）否则全部内联源合计（帧/整幅页
    的页数据体量身份 —— 不虚构单目标层语义）。"""
    if layer_id:
        return _layer_feature_count(effective_doc, layer_id)
    return sum(len(v) for v in _inline_feature_collections(effective_doc).values())


def plan_atlas_binding(spec: AtlasJobSpec, document: Dict[str, Any]) -> AtlasPlan:
    """AtlasJobSpec + MapSpec 文档 → :class:`AtlasPlan`（typed 失败）。"""
    if not isinstance(document, dict):
        raise MapSpecSchemaError("atlas_plan_invalid", "MapSpec 文档必须为对象")
    pub_ir = plan_publication_pages(document, atlas=spec.atlas)
    from app.lib.cartography.publication_ir import enabled_frames

    frames_lookup: List[Any] = []
    if spec.atlas.driver == "frames":
        frames_lookup, _ = enabled_frames(document, cap=spec.atlas.page_budget)

    ir_fp = pub_ir.ir_fingerprint()
    bindings: List[AtlasPageBinding] = []
    for page in pub_ir.pages:
        effective = _page_effective_doc(document, page, frames_lookup)
        if page.cover:
            layer_id = ""
            feature_count = 0
            data_digest = ""
        else:
            layer_id = page.filter_layer_id
            feature_count = _page_feature_count(effective, layer_id)
            data_digest = _page_features_digest(effective, layer_id)
        legend_digest, classification = _legend_digest_and_classification(effective, layer_id)
        binding_fields = {
            "page_id": page.page_id,
            "title": page.title,
            "page_number": page.page_number,
            "page_count": page.page_count,
            "cover": page.cover,
            "bounds": page.bounds,
            "paper": {
                "width_mm": page.paper.width_mm,
                "height_mm": page.paper.height_mm,
                "profile": page.paper.profile,
            },
            "filter_layer_id": layer_id,
            "filter_property": page.filter_property,
            "filter_value": page.filter_value,
            "feature_offset": page.feature_offset,
            "feature_limit": page.feature_limit,
            "feature_count": feature_count,
            "data_digest": data_digest,
            "legend_digest": legend_digest,
        }
        bindings.append(AtlasPageBinding(
            **binding_fields,
            classification=classification,
            fingerprint=_page_fingerprint(
                binding_fields, spec=spec, ir_fingerprint=ir_fp,
                structural_fingerprint=_effective_structural_fingerprint(effective)),
        ))
    return AtlasPlan(ir=pub_ir, bindings=bindings)


def _effective_structural_fingerprint(effective_doc: Dict[str, Any]) -> str:
    """页有效文档结构指纹（委托同步链同一投影 —— 单一事实源）。"""
    from app.services.publication_export import _structural_fingerprint

    return _structural_fingerprint(effective_doc)


__all__ = [
    "ATLAS_COMPILER_KEY",
    "AtlasPageBinding",
    "AtlasPlan",
    "plan_atlas_binding",
]
