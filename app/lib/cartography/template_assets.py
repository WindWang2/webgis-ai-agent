"""TemplateAsset —— 组合模板资产的引用/载荷/物化契约（G04）.

ADR-0214 在非目标里显式留白「用户自定义 composition template 的 DB 存储面
（存储面是独立方向，需 scope/租户/退役路径完整设计）」；本模块就是该存储面的
**lib 契约层**（scope/租户/退役在 services 层，DB 行在 models 层）：

- ``TemplateAssetPayload``：资产存储载荷。**变体优先于复制** —— 引用式
  （``base_template_id`` + ``slot_overrides`` 参数绑定覆写）或 inline
  （完整 ``MapCompositionTemplate`` dict，保存自会话/从零创作）。二者恰居其一。
- ``TemplateAssetRecord``：可解析资产的 lib 视图（builtin 代码资产或 DB 行
  投影；由服务层构造，本模块不做 IO）。
- ``materialize_template``：record → ``MapCompositionTemplate``。base 从既有
  ``CompositionTemplateRegistry`` 取槽位真值再深合并覆写 —— 不复制模板文件、
  不建第二模板真相；物化结果 id 强制 ``asset.*`` 命名空间（与系统
  ``composition.*`` 物理不可撞）。
- ``derive_contract``：物化模板 → ``CompositionContractV1``（contract_id=
  ``contract.asset.<asset_id>@r<revision>``；契约只**引用**槽位，不复制语义；
  不进全局 ContractRegistry —— apply 时构造，避免注册表无界增长）。
- ``validate_asset_template``：单模板 fail-closed 校验（与注册表创作期校验
  同口径：类型词表/preferred/模型可解析/zone conformance）。发布门消费。
- ``asset_fingerprint``：canonical JSON sha256（模式同 contract_fingerprint）。
- ``BUILTIN_TEMPLATE_ASSETS``：内置 4 族资产（代码定义、revision=1、走同一条
  解析/物化/应用链路）——典型制图成果结构的**参数绑定变体**，不是模板复制。
- ``TemplateAssetReference``：有界引用 DTO（agent 工具载荷）。

红线：纯函数、确定性、有界；lib 不 import services（ADR-0216）；组件类型
词表不因资产扩容（ADR-0101 —— 覆写只能引用既有类型）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from app.lib.cartography.composition_contract import (
    CompositionContractV1,
    ContractSlot,
)
from app.lib.cartography.composition_templates import MapCompositionTemplate

#: 资产命名空间（系统 seed/pack 用 composition.*；物理不可撞）。
ASSET_ID_PREFIX = "asset."
#: 内置资产命名空间（代码定义，不可被 DB 行覆盖）。
BUILTIN_ASSET_PREFIX = "asset.builtin."

ASSET_PAYLOAD_SCHEMA_VERSION = 1

#: 有界封顶（载荷纪律；与契约级封顶同族）。
MAX_ASSET_ID = 64
MAX_ASSET_SLOT_OVERRIDES = 24
MAX_ASSET_TAGS = 8
MAX_ASSET_NAME = 80
MAX_ASSET_DESCRIPTION = 400
MAX_ASSET_TEMPLATE_SLOTS = 48

#: slot 覆写允许的字段（ComponentSlot 除 id 外全部 —— id 是槽位身份，
#: 改 id 等于换槽位，不是覆写）。合并后仍过 validate_asset_template。
_OVERRIDABLE_SLOT_FIELDS = frozenset({
    "category", "required", "cardinality", "min_count", "max_count",
    "preferred_templates", "allowed_component_types", "position_zone",
    "fallback_zones", "bind_role", "bind_scope", "conditions", "stack_behavior",
})

#: 资产生命周期状态词表（asset 与 version 共用；迁移语义见 services 层）。
ASSET_STATUS_VALUES = ("draft", "published", "retired")


class TemplateAssetError(ValueError):
    """资产载荷/物化的结构性失败（fail-closed；message 携带 reason code 前缀）。"""


class TemplateAssetPayload(BaseModel):
    """资产存储载荷（DB version 行 / builtin 定义共用）。"""

    schema_version: int = ASSET_PAYLOAD_SCHEMA_VERSION
    name: str = ""
    description: str = ""
    #: 引用式变体：既有 composition.* 模板 id（与 inline_template 互斥）。
    base_template_id: str = ""
    #: slot 级参数绑定覆写（slot_id -> ComponentSlot 字段子集）。
    slot_overrides: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    #: inline 完整模板（保存自会话/从零创作；dict 形式的 MapCompositionTemplate）。
    inline_template: Optional[Dict[str, Any]] = None
    #: 顶层覆写（空 = 继承 base / inline 原值）。
    compatible_map_models: List[str] = Field(default_factory=list)
    output_targets: List[str] = Field(default_factory=list)
    layout_profile: str = ""
    tags: List[str] = Field(default_factory=list)

    def mode(self) -> str:
        """``variant``（引用式）或 ``inline``；两者皆空/皆有 = 结构错误。"""
        has_base = bool(self.base_template_id)
        has_inline = self.inline_template is not None
        if has_base == has_inline:
            raise TemplateAssetError(
                "asset_payload_error: exactly one of "
                "base_template_id / inline_template required")
        return "variant" if has_base else "inline"

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": ("inline" if self.inline_template is not None
                     else "variant" if self.base_template_id else ""),
            "name": self.name[:MAX_ASSET_NAME],
            "description": self.description[:160],
            "base_template_id": self.base_template_id[:MAX_ASSET_ID],
            "slot_override_count": len(self.slot_overrides),
            "slot_overrides": {
                str(k)[:32]: dict(v) if isinstance(v, dict) else {}
                for k, v in list(self.slot_overrides.items())[
                    :MAX_ASSET_SLOT_OVERRIDES]
            },
            "compatible_map_models": [
                m[:32] for m in self.compatible_map_models[:8]],
            "output_targets": [t[:16] for t in self.output_targets[:5]],
            "layout_profile": self.layout_profile[:16],
            "tags": [t[:24] for t in self.tags[:MAX_ASSET_TAGS]],
        }


class TemplateAssetRecord(BaseModel):
    """可解析资产的 lib 视图（builtin 或 DB 行投影；服务层构造，无 IO）。"""

    asset_id: str
    revision: int = 1
    status: str = "published"
    payload: TemplateAssetPayload
    builtin: bool = False
    #: 内容指纹（服务层落库时计算缓存；空 = 由 asset_fingerprint 现算）。
    fingerprint: str = ""


class TemplateAssetReference(BaseModel):
    """有界引用 DTO（agent 工具/HTTP 列表载荷；revision=0 表示 current）。"""

    asset_id: str
    revision: int = 0
    status: str = "published"
    builtin: bool = False
    fingerprint: str = ""
    template_id: str = ""
    name: str = ""
    compatible_map_models: List[str] = Field(default_factory=list)
    output_targets: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id[:MAX_ASSET_ID],
            "revision": self.revision,
            "status": self.status[:12],
            "builtin": self.builtin,
            "fingerprint": self.fingerprint[:80],
            "template_id": self.template_id[:MAX_ASSET_ID],
            "name": self.name[:MAX_ASSET_NAME],
            "compatible_map_models": [
                m[:32] for m in self.compatible_map_models[:6]],
            "output_targets": [t[:16] for t in self.output_targets[:5]],
        }


def asset_reference_of(record: TemplateAssetRecord,
                       tpl: MapCompositionTemplate) -> TemplateAssetReference:
    """record + 物化模板 → 有界引用（列表/发现面共用投影）。"""
    return TemplateAssetReference(
        asset_id=record.asset_id,
        revision=record.revision,
        status=record.status,
        builtin=record.builtin,
        fingerprint=record.fingerprint or asset_fingerprint(record),
        template_id=tpl.id,
        name=record.payload.name or tpl.name,
        compatible_map_models=tpl.compatible_map_models,
        output_targets=tpl.output_targets,
    )


# ── 指纹 ───────────────────────────────────────────────────────────────────


def asset_fingerprint(record: TemplateAssetRecord) -> str:
    """canonical JSON sha256（模式同 contract_fingerprint；内容单一指纹）。"""
    payload = json.dumps(
        {
            "asset_id": record.asset_id,
            "revision": record.revision,
            "payload": record.payload.model_dump(),
        },
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"asset-sha256:{hashlib.sha256(payload).hexdigest()}"


# ── 物化 ───────────────────────────────────────────────────────────────────


def _assert_asset_id(asset_id: str) -> str:
    tid = str(asset_id or "").strip()
    if not tid.startswith(ASSET_ID_PREFIX):
        raise TemplateAssetError(
            f"asset_template_error: id_namespace {tid[:32]}")
    if len(tid) > MAX_ASSET_ID:
        raise TemplateAssetError("asset_template_error: id_too_long")
    return tid


def _apply_top_overrides(tpl: MapCompositionTemplate,
                         p: TemplateAssetPayload) -> MapCompositionTemplate:
    """顶层非空覆写（name/description/models/outputs/profile/tags）。"""
    if p.name:
        tpl.name = p.name[:MAX_ASSET_NAME]
    if p.description:
        tpl.description = p.description[:MAX_ASSET_DESCRIPTION]
    if p.compatible_map_models:
        tpl.compatible_map_models = [
            str(m)[:32] for m in p.compatible_map_models[:8]]
    if p.output_targets:
        tpl.output_targets = [str(t)[:16] for t in p.output_targets[:5]]
    if p.layout_profile:
        tpl.layout_profile = p.layout_profile  # Literal 词表由 pydantic 校验
    if p.tags:
        tpl.tags = [str(t)[:24] for t in p.tags[:MAX_ASSET_TAGS]]
    return tpl


def materialize_template(record: TemplateAssetRecord) -> MapCompositionTemplate:
    """record → MapCompositionTemplate（确定性；base 真值 + 覆写深合并）。

    - inline：完整模板 dict 校验后强制 asset.* 命名空间 id；
    - variant：base 模板 model_dump → id 换资产 id → slot_overrides 逐槽合并
      （未知槽位/不可覆写字段 = fail-closed）→ 顶层覆写。
    fallback_template 保持指向 base 的原 fallback 链（系统模板域，不重写）。
    """
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )

    _assert_asset_id(record.asset_id)
    p = record.payload
    mode = p.mode()

    if mode == "inline":
        data = dict(p.inline_template or {})
        data["id"] = record.asset_id
        try:
            tpl = MapCompositionTemplate.model_validate(data)
        except ValidationError as exc:
            raise TemplateAssetError(
                f"asset_template_error: inline_invalid {str(exc)[:120]}") from exc
        return _apply_top_overrides(tpl, p)

    base = get_composition_template_registry().get(p.base_template_id)
    if base is None:
        raise TemplateAssetError(
            f"asset_template_error: base_not_found {p.base_template_id[:48]}")
    if len(p.slot_overrides) > MAX_ASSET_SLOT_OVERRIDES:
        raise TemplateAssetError(
            f"asset_template_error: too_many_overrides "
            f"{len(p.slot_overrides)}>{MAX_ASSET_SLOT_OVERRIDES}")
    data = base.model_dump()
    data["id"] = record.asset_id
    slots = data.get("component_slots") or []
    slot_index = {str(s.get("id") or ""): i for i, s in enumerate(slots)}
    for slot_id in sorted(p.slot_overrides.keys()):
        ov = p.slot_overrides[slot_id]
        if not isinstance(ov, dict) or not ov:
            raise TemplateAssetError(
                f"asset_template_error: override_not_dict {str(slot_id)[:32]}")
        idx = slot_index.get(str(slot_id))
        if idx is None:
            raise TemplateAssetError(
                f"asset_template_error: slot_not_in_base {str(slot_id)[:32]}")
        for field_name in ov:
            if field_name not in _OVERRIDABLE_SLOT_FIELDS:
                raise TemplateAssetError(
                    f"asset_template_error: slot_field_not_overridable "
                    f"{str(field_name)[:24]}")
        merged = dict(slots[idx])
        merged.update(ov)
        slots[idx] = merged
    try:
        tpl = MapCompositionTemplate.model_validate(data)
    except ValidationError as exc:
        raise TemplateAssetError(
            f"asset_template_error: variant_invalid {str(exc)[:120]}") from exc
    return _apply_top_overrides(tpl, p)


def derive_contract(record: TemplateAssetRecord,
                    tpl: MapCompositionTemplate) -> CompositionContractV1:
    """物化模板 → 组合契约（只引用槽位，不复制语义；不进全局注册表）。"""
    if tpl.id != record.asset_id:
        raise TemplateAssetError(
            f"asset_contract_error: template_id_mismatch {tpl.id[:48]}")
    slots = tuple(
        ContractSlot(slot_id=str(s.id))
        for s in tpl.component_slots[:24]
    )
    return CompositionContractV1(
        contract_id=f"contract.asset.{record.asset_id}@r{record.revision}",
        contract_version="1.0.0",
        template_id=tpl.id,
        template_version=f"asset-r{record.revision}",
        slots=slots,
        links=(),
        export_targets=tuple(tpl.output_targets[:5]),
        compatible_map_models=tuple(tpl.compatible_map_models[:8]),
        description=(record.payload.description or tpl.name)[:160],
    )


# ── 校验（发布门消费；与注册表创作期校验同口径）────────────────────────────


def validate_asset_template(tpl: MapCompositionTemplate) -> List[str]:
    """单模板 fail-closed 校验（问题清单，空 = 通过）。

    复用 ``CompositionTemplateRegistry.validate_template`` 的单模板口径
    （fallback/类型词表/preferred/模型可解析/zone conformance）——资产与
    系统模板走同一道创作期闸，不另立第二校验器。
    """
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )
    if len(tpl.component_slots) > MAX_ASSET_TEMPLATE_SLOTS:
        return [
            f"asset {tpl.id}: slot count "
            f"{len(tpl.component_slots)}>{MAX_ASSET_TEMPLATE_SLOTS}"]
    return get_composition_template_registry().validate_template(tpl)


# ── 内置资产（代码定义；与 DB 资产同一条解析/物化/应用链路）─────────────────

BUILTIN_TEMPLATE_ASSETS: Tuple[TemplateAssetRecord, ...] = (
    # 1) 点分布 + 热力 + 各区柱状统计组合（热力密度基底 + 图表绑定）。
    TemplateAssetRecord(
        asset_id="asset.builtin.heat_stats_portfolio",
        revision=1,
        payload=TemplateAssetPayload(
            name="热力+统计图表组合",
            description=(
                "点分布/热力主图 + 连续色条 + 统计面板与柱状图表绑定的"
                "探索组合资产（density_map 参数绑定变体）。"),
            base_template_id="composition.density_map",
            slot_overrides={
                "statistics_panel": {
                    "cardinality": "recommended",
                    "preferred_templates": ["statistics-panel/kpi"],
                },
                "chart_panel": {
                    "cardinality": "recommended",
                    "max_count": 2,
                    "preferred_templates": ["chart-panel/default"],
                },
            },
            tags=["heat", "statistics", "portfolio"],
        ),
        builtin=True,
    ),
    # 2) 分级设色 + 图例/统计（统计报告基底：图例升级必备 + 学术图例绑定）。
    TemplateAssetRecord(
        asset_id="asset.builtin.choropleth_graduated_report",
        revision=1,
        payload=TemplateAssetPayload(
            name="分级设色+图例统计报告",
            description=(
                "行政区分级设色报告版式：分级图例必备（学术样式绑定）+ "
                "统计面板推荐 + 图表上限 2（statistical_report 变体）。"),
            base_template_id="composition.statistical_report",
            slot_overrides={
                "legend": {
                    "cardinality": "required",
                    "required": True,
                    "preferred_templates": ["legend/academic"],
                },
                "chart_panel": {"max_count": 2},
            },
            # 基底 statistical_report 携带 export_layout 槽位 → 仅 png/pdf
            # （interactive 目标下 conformance 正确拒绝 export-only 版式）
            output_targets=["png", "pdf"],
            tags=["choropleth", "graduated", "report"],
        ),
        builtin=True,
    ),
    # 3) 时序对比双图（时相变化报告基底：图表升级必备承载双时相对比）。
    TemplateAssetRecord(
        asset_id="asset.builtin.temporal_change_pair",
        revision=1,
        payload=TemplateAssetPayload(
            name="时序对比双图",
            description=(
                "时序变化报告版式：副标题推荐 + 图表必备（1-2 个，承载"
                "双时相对比）（temporal_change_report 变体）。"),
            base_template_id="composition.temporal_change_report",
            slot_overrides={
                "subtitle": {"cardinality": "recommended"},
                "chart_panel": {
                    "cardinality": "required",
                    "required": True,
                    "min_count": 1,
                    "max_count": 2,
                    "preferred_templates": ["chart-panel/report"],
                },
            },
            # 基底携带 export_layout 槽位（报告版式）→ 仅 png/pdf 目标
            # （interactive 目标下 conformance 正确拒绝 export-only 版式）
            output_targets=["png", "pdf"],
            tags=["temporal", "change", "comparison"],
        ),
        builtin=True,
    ),
    # 4) 遥感分类/变化专题图（分类版式基底：出版图例绑定；**不含
    # interactive** —— 基底槽位携带 export_layout，interactive 目标下
    # conformance 正确拒绝（export-only 版式）。发布目标保持 png/pdf。
    TemplateAssetRecord(
        asset_id="asset.builtin.rs_classification_change",
        revision=1,
        payload=TemplateAssetPayload(
            name="遥感分类/变化专题",
            description=(
                "遥感分类专题版式：分类图例（紧凑样式绑定）+ 经纬网可选 + "
                "png/pdf 出版目标（rs_classification 变体）。"),
            base_template_id="composition.rs_classification",
            slot_overrides={
                "legend": {"preferred_templates": ["legend/compact"]},
                "graticule": {
                    "cardinality": "optional",
                    "preferred_templates": ["graticule/light"],
                },
            },
            tags=["remote-sensing", "classification", "publication"],
        ),
        builtin=True,
    ),
)

_BUILTIN_BY_ID = {rec.asset_id: rec for rec in BUILTIN_TEMPLATE_ASSETS}


def get_builtin_asset(asset_id: str) -> Optional[TemplateAssetRecord]:
    """内置资产只读检索（确定性；DB 层不可覆盖 builtin 命名空间）。"""
    return _BUILTIN_BY_ID.get(asset_id)


__all__ = [
    "ASSET_ID_PREFIX",
    "BUILTIN_ASSET_PREFIX",
    "ASSET_PAYLOAD_SCHEMA_VERSION",
    "ASSET_STATUS_VALUES",
    "MAX_ASSET_SLOT_OVERRIDES",
    "TemplateAssetError",
    "TemplateAssetPayload",
    "TemplateAssetRecord",
    "TemplateAssetReference",
    "BUILTIN_TEMPLATE_ASSETS",
    "asset_fingerprint",
    "asset_reference_of",
    "materialize_template",
    "derive_contract",
    "validate_asset_template",
    "get_builtin_asset",
]
