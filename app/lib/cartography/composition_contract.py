"""Composition Contract v1 — 模板组合契约（ADR-0214 D2/D3/D4）.

把「Template = Component Graph + Constraint Set + Style Tokens + Slots」
装订成一份 **versioned、可序列化、可 diff、可确定性重放** 的对象：

- ``CompositionContractV1`` **引用** MapCompositionTemplate（slot 语义的
  单一事实），只携带 versioned 绑定 + slot 级图骨架 + 覆写；不复制槽位
  定义，不做第二模板真相。
- ``contract_fingerprint``：canonical JSON sha256（模式同
  TemplateSpecRegistry 指纹）——契约内容变化可被上层指纹链捕获。
- ``diff_contracts``：有界结构 diff（slots/links/version/abi），reason
  codes，不自由文本。
- ``apply_contract``：**lock-aware 确定性 replay** —— 只填空槽、只保留
  未锁实例的全部用户编辑、锁实例零触碰（``component_locked:user_wins``）、
  显式链接幂等合并、写入 ``layout.composition`` 身份块（版本 → 既有
  ``cartographic_fingerprint`` 自动捕获）。幂等：同一 spec 重复 apply
  结果逐位一致。
- 用户锁（D4）：锁的单一事实是 W15 workbench doc 的
  ``lockedComponentIds``（引擎守卫全量执行）；本模块提供锁集读取
  （``locked_component_ids_of``）与 apply 的槽位级零触碰语义。
  锁的置/解走 SetWorkbenchStateIntent 既有通道 —— 本模块不造第二套锁。

红线：纯函数、确定性、有界载荷；图例族 per-layer 展开语义单一事实仍在
harness composer（本模块只创建单实例并如实披露）；不改 grammar 裁决、
不做布局计算、不做渲染。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.lib.cartography.component_abi import (
    COMPONENT_ABI_VERSION,
    instance_id_for_type,
    versions_projection,
)

CONTRACT_SCHEMA_VERSION = 1

#: 契约级有界封顶。
MAX_CONTRACT_SLOTS = 24
MAX_CONTRACT_LINKS = 16
MAX_APPLY_DISCLOSURES = 16
MAX_COMPONENT_VERSIONS = 32
_MAX_ID_ATTR = 48

#: 用户锁 / provenance 的 reason code 词表（工具层转发；测试锁）。
LOCK_REASON_USER_WINS = "component_locked:user_wins"

#: slot 级图骨架允许的边型（组件图 LINK_TYPES 的 slot 级子集；under/groups
#: 在 slot 语义里同样成立）。
CONTRACT_LINK_TYPES = ("requires", "under", "annotates", "groups")

#: style token preset 词表（style_tokens.STYLE_PRESET_IDS 的本地冻结投影；
#: 改词表必须双侧同步 —— 测试锁）。
_TOKEN_PRESETS = ("screen", "publication")
#: 导出目标词表（descriptor supported_outputs 同表）。
_EXPORT_TARGETS = ("interactive", "png", "pdf", "svg", "print")


class ContractSlot(BaseModel):
    """契约槽位：**引用** composition 模板同名槽位，只携带覆写。"""

    slot_id: str
    #: 覆写 component 模板偏好（component_templates id）；空 = 用模板槽位
    #: 自带 preferred_templates。必须指向 allowed_component_types 内类型。
    preferred_template: str = ""
    #: 模板作者的锁建议（advisory）：apply 只披露建议，不代用户置锁
    #:（锁的单一事实 = W15 workbench 锁集，置锁是用户显式动作）。
    locked_default: bool = False


class ContractLink(BaseModel):
    """slot 级图骨架边（apply 时解析为实例边并入 component_links）。"""

    src_slot: str
    dst_slot: str
    type: str = "annotates"       # CONTRACT_LINK_TYPES


class CompositionContractV1(BaseModel):
    """versioned 组合契约（canonical 可序列化）。"""

    schema_version: int = CONTRACT_SCHEMA_VERSION
    contract_id: str                       # "contract.core.basic_thematic"
    contract_version: str = "1.0.0"        # 契约语义版本（破坏性变化 bump）
    template_id: str                       # 引用 MapCompositionTemplate
    template_version: str = "1.0.0"        # 契约锚定的模板语义版本
    purpose: str = ""                      # 机器可读目的（presets 键对齐）
    slots: Tuple[ContractSlot, ...] = ()
    links: Tuple[ContractLink, ...] = ()
    style_token_preset: str = ""           # _TOKEN_PRESETS；空 = 不绑定
    theme_profile: str = ""
    export_targets: Tuple[str, ...] = ()
    min_abi_version: int = 1
    compatible_map_models: Tuple[str, ...] = ()
    description: str = ""
    deprecated_by: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        """有界投影（agent 工具载荷）。"""
        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id[:_MAX_ID_ATTR],
            "contract_version": self.contract_version[:16],
            "template_id": self.template_id[:_MAX_ID_ATTR],
            "template_version": self.template_version[:16],
            "purpose": self.purpose[:32],
            "slot_count": len(self.slots),
            "link_count": len(self.links),
            "slots": [s.slot_id[:32] for s in self.slots[:MAX_CONTRACT_SLOTS]],
            "style_token_preset": self.style_token_preset[:16],
            "export_targets": list(self.export_targets[:5]),
            "min_abi_version": self.min_abi_version,
            "deprecated_by": self.deprecated_by[:_MAX_ID_ATTR],
        }


class CompositionIdentity(BaseModel):
    """layout.composition 身份块（ADR-0214 D3；随 layout 入指纹）。"""

    template_id: str
    template_version: str
    contract_id: str = ""
    contract_fingerprint: str = ""
    component_abi_version: int = COMPONENT_ABI_VERSION
    component_versions: Dict[str, str] = Field(default_factory=dict)
    applied_revision: int = 0

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "template_id": self.template_id[:_MAX_ID_ATTR],
            "template_version": self.template_version[:16],
            "contract_id": self.contract_id[:_MAX_ID_ATTR],
            "contract_fingerprint": self.contract_fingerprint[:80],
            "component_abi_version": self.component_abi_version,
            "component_versions": dict(
                sorted(self.component_versions.items())[:MAX_COMPONENT_VERSIONS]),
            "applied_revision": self.applied_revision,
        }


class ContractApplyError(ValueError):
    """契约应用的结构性失败（fail-closed；message 即 reason code 前缀）。"""


class ApplyReport(BaseModel):
    """apply 结果（有界、可序列化、reason 齐全）。"""

    contract_id: str = ""
    created: List[str] = Field(default_factory=list)
    preserved: List[str] = Field(default_factory=list)
    locked_skipped: List[str] = Field(default_factory=list)
    links_added: int = 0
    disclosures: List[str] = Field(default_factory=list)
    identity: Optional[CompositionIdentity] = None

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id[:_MAX_ID_ATTR],
            "created": [c[:_MAX_ID_ATTR] for c in self.created[:16]],
            "created_count": len(self.created),
            "preserved": [p[:_MAX_ID_ATTR] for p in self.preserved[:16]],
            "locked_skipped": [lk[:_MAX_ID_ATTR] for lk in self.locked_skipped[:8]],
            "links_added": self.links_added,
            "disclosures": [d[:160] for d in self.disclosures[:MAX_APPLY_DISCLOSURES]],
            "identity": self.identity.to_bounded_dict() if self.identity else None,
        }


# ── 用户锁（D4）——单一事实 = W15 workbench doc 锁集 ─────────────────────

_MAX_LOCK_IDS = 64


def locked_component_ids_of(spec: Dict[str, Any]) -> List[str]:
    """组件锁集读取（W15 单一事实：``spec.workbench.lockedComponentIds``）。

    与 ``lifecycle_engine.locked_component_ids_of`` 同一存储、同一口径
    （缺席/非法 → 空，有界 64）；lib 层不 import services，本地纯读。
    锁的**执行**仍在引擎守卫（guard_intent_locks）—— 本模块只消费锁集
    做槽位级跳过决策；锁的**置/解**走 SetWorkbenchStateIntent 既有通道，
    本模块不提供（agent 不得代替用户置锁 —— user-wins）。
    """
    if not isinstance(spec, dict):
        return []
    wb = spec.get("workbench")
    if not isinstance(wb, dict):
        return []
    locked = wb.get("lockedComponentIds", [])
    if not isinstance(locked, list):
        return []
    return [x for x in locked[:_MAX_LOCK_IDS] if isinstance(x, str) and x]


# ── 指纹 / diff ───────────────────────────────────────────────────────────


def contract_fingerprint(contract: CompositionContractV1) -> str:
    """canonical JSON sha256（契约内容单一指纹；模式同 TemplateSpecRegistry）。"""
    payload = json.dumps(
        contract.model_dump(),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return f"contract-sha256:{hashlib.sha256(payload).hexdigest()}"


def diff_contracts(
    old: CompositionContractV1, new: CompositionContractV1
) -> Dict[str, Any]:
    """有界结构 diff（确定性排序；reason code 而非自由文本）。"""
    old_slots = {s.slot_id: s for s in old.slots}
    new_slots = {s.slot_id: s for s in new.slots}
    slots_added = sorted(set(new_slots) - set(old_slots))
    slots_removed = sorted(set(old_slots) - set(new_slots))
    slots_changed = sorted(
        sid for sid in set(old_slots) & set(new_slots)
        if (old_slots[sid].preferred_template, old_slots[sid].locked_default)
        != (new_slots[sid].preferred_template, new_slots[sid].locked_default)
    )
    old_links = {(lk.src_slot, lk.dst_slot, lk.type) for lk in old.links}
    new_links = {(lk.src_slot, lk.dst_slot, lk.type) for lk in new.links}
    disclosures: List[str] = []
    if old.contract_version != new.contract_version:
        disclosures.append(
            f"version_changed:{old.contract_version}->{new.contract_version}")
    if old.template_id != new.template_id:
        disclosures.append(
            f"template_changed:{old.template_id}->{new.template_id}")
    if old.template_version != new.template_version:
        disclosures.append(
            f"template_version_changed:{old.template_version}->{new.template_version}")
    if old.min_abi_version != new.min_abi_version:
        disclosures.append(
            f"min_abi_changed:{old.min_abi_version}->{new.min_abi_version}")
    return {
        "slots_added": [s[:32] for s in slots_added[:12]],
        "slots_removed": [s[:32] for s in slots_removed[:12]],
        "slots_changed": [s[:32] for s in slots_changed[:12]],
        "links_added": sorted(f"{a}:{b}:{t}" for a, b, t in new_links - old_links)[:12],
        "links_removed": sorted(f"{a}:{b}:{t}" for a, b, t in old_links - new_links)[:12],
        "disclosures": disclosures[:8],
    }


# ── apply（lock-aware 确定性 replay）──────────────────────────────────────


def apply_contract(
    spec: Dict[str, Any],
    contract: CompositionContractV1,
    *,
    revision: int = 0,
) -> Tuple[Dict[str, Any], ApplyReport]:
    """把契约确定性重放到 MapSpec（纯函数：不改输入，返回新 spec + 报告）。

    语义（ADR-0214 D2/D4）：
    - 已在场实例（含 disabled）= 槽位已满足：**零触碰**（用户编辑不重置）；
    - ``user_lock=True`` 的实例：零触碰并披露（防御语义，正常路径已满足）；
    - 空槽：按（契约覆写 → 模板槽位 preferred_templates → descriptor 默认）
      物化单实例；bind_scope=all_thematic 的 per-layer 展开仍归 harness
      composer 主链路（本模块只建 primary 单实例并披露）；
    - links：slot → 实例解析后并入 ``component_links``（同端点同型幂等）；
    - 写 ``layout.composition`` 身份块（版本入指纹）。
    """
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )
    from app.lib.cartography.component_templates import (
        get_component_template_registry,
    )

    tpl = get_composition_template_registry().get(contract.template_id)
    if tpl is None:
        raise ContractApplyError(
            f"contract_apply_error: template_not_found {contract.template_id[:_MAX_ID_ATTR]}")

    new_spec = copy.deepcopy(spec)
    if not isinstance(new_spec.get("layout"), dict):
        new_spec["layout"] = {}
    layout = new_spec["layout"]
    raw_components = layout.get("components")
    components: List[Dict[str, Any]] = (
        [c for c in raw_components if isinstance(c, dict)]
        if isinstance(raw_components, list) else [])
    by_id: Dict[str, Dict[str, Any]] = {}
    for c in components:
        cid = str(c.get("id") or "")
        if cid:
            by_id.setdefault(cid, c)

    report = ApplyReport(contract_id=contract.contract_id)
    comp_reg = get_component_registry()
    tmpl_reg = get_component_template_registry()
    slot_overrides = {s.slot_id: s for s in contract.slots}

    def _disclose(msg: str) -> None:
        if len(report.disclosures) < MAX_APPLY_DISCLOSURES:
            report.disclosures.append(msg[:160])

    slot_to_instance: Dict[str, str] = {}
    locked_ids = set(locked_component_ids_of(spec))
    for cslot in contract.slots:
        tslot = next((s for s in tpl.component_slots if s.id == cslot.slot_id), None)
        if tslot is None:
            raise ContractApplyError(
                f"contract_apply_error: slot_not_in_template {cslot.slot_id[:32]}")
        allowed = set(tslot.allowed_component_types)
        present = [c for c in components
                   if str(c.get("type") or "") in allowed]
        if present:
            first = sorted(
                present,
                key=lambda c: (str(c.get("id") or "") not in locked_ids,
                               str(c.get("id") or "")))[0]
            slot_to_instance[cslot.slot_id] = str(first.get("id") or "")
            for c in present:
                cid = str(c.get("id") or "")
                report.preserved.append(cid)
                if cid in locked_ids:
                    report.locked_skipped.append(cid)
                    _disclose(f"{LOCK_REASON_USER_WINS}: {cid[:_MAX_ID_ATTR]} 槽位 "
                              f"{cslot.slot_id[:32]} 零触碰")
            continue
        # 空槽物化（forbidden/max_count=0 槽位不得物化）
        if getattr(tslot, "cardinality", "") == "forbidden" or int(
                getattr(tslot, "max_count", 1) or 1) <= 0:
            continue
        if not allowed:
            continue
        # 类型选择：声明序首个（模板槽位的 allowed_component_types 顺序
        # 即偏好序 —— 与 composer/resolver 的主型选择同口径），不排序。
        ctype = tslot.allowed_component_types[0]
        override = slot_overrides.get(cslot.slot_id)
        preferred = ""
        if override is not None and override.preferred_template:
            preferred = override.preferred_template
        elif tslot.preferred_templates:
            preferred = tslot.preferred_templates[0]
        options: Dict[str, Any] = {}
        style: Dict[str, Any] = {}
        variant = ""
        if preferred:
            ct = tmpl_reg.get(preferred)
            if ct is not None and ct.component_type == ctype:
                if ct.runtime_status == "native":
                    variant = ct.variant
                    options = dict(ct.default_options)
                    style = dict(ct.default_style)
                else:
                    _disclose(
                        f"preferred_template_not_native: {preferred[:_MAX_ID_ATTR]} "
                        f"退化为 descriptor 默认")
            else:
                _disclose(
                    f"preferred_template_unresolvable: {preferred[:_MAX_ID_ATTR]}")
        desc = comp_reg.get_by_type(ctype)
        position = tslot.position_zone if tslot.position_zone != "none" else (
            desc.default_position if desc is not None else "none")
        priority = desc.priority if desc is not None else 50
        base_id = instance_id_for_type(ctype)
        cid = base_id
        n = 1
        while cid in by_id and by_id[cid].get("type") != ctype:
            cid = f"{base_id}-{n}"
            n += 1
        if cid in by_id:
            # 同型实例在场但类型词表不同路（罕见别名），保先不重复。
            slot_to_instance[cslot.slot_id] = cid
            report.preserved.append(cid)
            continue
        if cid in locked_ids:
            # Review P2-2：分配到的实例 id 命中用户锁集（W15）—— 空槽
            # 物化同样避让（库级直调也有锁保护；工具层另有引擎守卫兜底）。
            report.locked_skipped.append(cid)
            _disclose(f"{LOCK_REASON_USER_WINS}: 空槽 {cslot.slot_id[:32]} "
                      f"默认实例 id {cid[:_MAX_ID_ATTR]} 被锁 —— 跳过物化")
            continue
        instance: Dict[str, Any] = {
            "id": cid, "type": ctype, "enabled": True,
            "position": position, "priority": priority,
            "style": style, "options": options,
        }
        if variant:
            instance["variant"] = variant
        if override is not None and override.locked_default:
            # 契约作者的锁建议是**advisory**：锁的单一事实在 workbench 锁集
            # （W15），置锁是用户显式动作 —— agent/契约不得代替用户置锁。
            _disclose(f"locked_default_suggested: {cid[:_MAX_ID_ATTR]} "
                      f"(contract {contract.contract_id[:32]})")
        # provenance（extra="allow" 自由域；有界、确定性 —— 无时间戳）
        instance["provenance"] = {
            "origin": "template",
            "source_template": tpl.id[:_MAX_ID_ATTR],
            "contract": contract.contract_id[:_MAX_ID_ATTR],
        }
        if getattr(tslot, "bind_scope", "primary") == "all_thematic":
            _disclose(
                f"bind_scope_all_thematic: {cid[:_MAX_ID_ATTR]} 仅建 primary 实例；"
                f"per-layer 展开由 composer 主链路负责")
        components.append(instance)
        by_id[cid] = instance
        slot_to_instance[cslot.slot_id] = cid
        report.created.append(cid)

    # links：slot 级骨架 → 实例边（幂等合并）
    links_raw = layout.get("component_links")
    links: List[Dict[str, Any]] = (
        [lk for lk in links_raw if isinstance(lk, dict)]
        if isinstance(links_raw, list) else [])
    existing_link_keys = {
        (str(lk.get("src") or ""), str(lk.get("dst") or ""), str(lk.get("type") or ""))
        for lk in links}
    for cl in contract.links:
        src = slot_to_instance.get(cl.src_slot, "")
        dst = slot_to_instance.get(cl.dst_slot, "")
        if not src or not dst or src == dst:
            _disclose(f"link_unresolved: {cl.src_slot[:32]}->{cl.dst_slot[:32]}")
            continue
        key = (src, dst, cl.type)
        if key in existing_link_keys:
            continue
        if len(links) >= 32:  # mapspec_schema.MAX_COMPONENT_LINKS 同值
            _disclose("link_capacity: component_links 已达 32 上限，截断")
            break
        links.append({"src": src, "dst": dst, "type": cl.type, "dst_kind": "component"})
        existing_link_keys.add(key)
        report.links_added += 1
    layout["component_links"] = links

    # 身份块（D3：版本变化 → cartographic_fingerprint 捕获）
    identity = CompositionIdentity(
        template_id=tpl.id,
        template_version=contract.template_version,
        contract_id=contract.contract_id,
        contract_fingerprint=contract_fingerprint(contract),
        component_abi_version=COMPONENT_ABI_VERSION,
        component_versions=versions_projection(
            [str(c.get("type") or "") for c in components],
            cap=MAX_COMPONENT_VERSIONS),
        applied_revision=int(revision),
    )
    layout["composition"] = identity.model_dump()
    report.identity = identity

    layout["components"] = sorted(
        components,
        key=lambda c: (
            int(c["priority"]) if isinstance(c.get("priority"), (int, float))
            and not isinstance(c.get("priority"), bool) else 50,
            str(c.get("id") or "")))
    return new_spec, report


def read_composition_identity(spec: Dict[str, Any]) -> Optional[CompositionIdentity]:
    """宽容读取身份块（缺型/缺键 → None；不抛）。"""
    layout = spec.get("layout") if isinstance(spec, dict) else None
    raw = layout.get("composition") if isinstance(layout, dict) else None
    if not isinstance(raw, dict):
        return None
    try:
        raw_versions = raw.get("component_versions")
        versions: Dict[str, str] = {}
        if isinstance(raw_versions, dict):
            for k in sorted(raw_versions.keys())[:MAX_COMPONENT_VERSIONS]:
                versions[str(k)[:32]] = str(raw_versions[k])[:16]
        return CompositionIdentity(
            template_id=str(raw.get("template_id") or ""),
            template_version=str(raw.get("template_version") or ""),
            contract_id=str(raw.get("contract_id") or ""),
            contract_fingerprint=str(raw.get("contract_fingerprint") or ""),
            component_abi_version=int(raw.get("component_abi_version") or COMPONENT_ABI_VERSION),
            component_versions=versions,
            applied_revision=int(raw.get("applied_revision") or 0),
        )
    except Exception:  # 防御性：身份块损坏不阻塞读路径
        return None


# ── 契约注册表（确定性；错引用 fail-closed）───────────────────────────────

SEED_CONTRACTS: Tuple[CompositionContractV1, ...] = (
    CompositionContractV1(
        contract_id="contract.core.basic_thematic",
        template_id="composition.standard_analysis",
        purpose="basic_thematic",
        slots=(
            ContractSlot(slot_id="title"),
            ContractSlot(slot_id="subtitle"),
            ContractSlot(slot_id="legend"),
            ContractSlot(slot_id="north_arrow"),
            ContractSlot(slot_id="scale_bar"),
            ContractSlot(slot_id="attribution"),
            ContractSlot(slot_id="statistics_panel"),
            ContractSlot(slot_id="map_border"),
        ),
        links=(ContractLink(src_slot="subtitle", dst_slot="title", type="annotates"),),
        style_token_preset="screen",
        export_targets=("interactive", "png"),
        compatible_map_models=(
            "visual_heatmap", "administrative_choropleth", "aggregate_grid",
            "proportional_symbol"),
        description="基础专题图：标准分析版式的 versioned 组合契约。",
    ),
    CompositionContractV1(
        contract_id="contract.core.heat_distribution_stats",
        template_id="composition.density_map",
        purpose="heat_distribution_stats",
        slots=(
            ContractSlot(slot_id="title"),
            ContractSlot(slot_id="colorbar"),
            ContractSlot(slot_id="north_arrow"),
            ContractSlot(slot_id="scale_bar"),
            ContractSlot(slot_id="attribution"),
            ContractSlot(slot_id="statistics_panel"),
            ContractSlot(slot_id="chart_panel"),
        ),
        links=(),
        style_token_preset="screen",
        export_targets=("interactive", "png", "pdf"),
        compatible_map_models=("visual_heatmap", "raster_surface"),
        description="热力/点分布+统计：连续色条 + 统计/图表可选的 versioned 契约。",
    ),
    CompositionContractV1(
        contract_id="contract.core.change_comparison",
        template_id="composition.temporal_change_report",
        purpose="change_comparison",
        slots=(
            ContractSlot(slot_id="title"),
            ContractSlot(slot_id="subtitle"),
            ContractSlot(slot_id="legend"),
            ContractSlot(slot_id="north_arrow"),
            ContractSlot(slot_id="scale_bar"),
            ContractSlot(slot_id="attribution"),
            ContractSlot(slot_id="statistics_panel"),
            ContractSlot(slot_id="chart_panel"),
            ContractSlot(slot_id="map_border"),
            ContractSlot(slot_id="export_layout"),
        ),
        links=(ContractLink(src_slot="subtitle", dst_slot="title", type="annotates"),),
        style_token_preset="publication",
        export_targets=("png", "pdf"),
        description="变化图：时相变化报告版式的 versioned 组合契约（publication tokens）。",
    ),
)


def _contract_slot_cycle(contract: CompositionContractV1) -> List[str]:
    """契约 slot 链接环检测（确定性 DFS；返回环路径，无环返回空表）。

    全部边型（requires/under/annotates/groups）按 src→dst 有向边参与 ——
    任一语义下的环都使 apply 的实例边不可满足或自引用。"""
    adjacency: Dict[str, List[str]] = {}
    for link in contract.links:
        adjacency.setdefault(link.src_slot, []).append(link.dst_slot)
    for slot_id in {s.slot_id for s in contract.slots}:
        adjacency.setdefault(slot_id, [])
    for nid in adjacency:
        adjacency[nid] = sorted(set(adjacency[nid]))

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in adjacency}
    path: List[str] = []

    def dfs(u: str) -> Optional[List[str]]:
        color[u] = GRAY
        path.append(u)
        for v in adjacency.get(u, ()):
            if color.get(v, BLACK) == GRAY:
                return path[path.index(v):] + [v]
            if color.get(v, BLACK) == WHITE:
                found = dfs(v)
                if found:
                    return found
        path.pop()
        color[u] = BLACK
        return None

    for nid in sorted(adjacency):
        if color[nid] == WHITE:
            cycle = dfs(nid)
            if cycle:
                return cycle
    return []


class ContractRegistry:
    """模块级确定性契约注册表（错引用 fail-closed 校验）。"""

    def __init__(self) -> None:
        self._by_id: Dict[str, CompositionContractV1] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        for contract in SEED_CONTRACTS:
            if contract.contract_id in self._by_id:
                raise ValueError(f"duplicate contract id: {contract.contract_id}")
            self._by_id[contract.contract_id] = contract
        # ADR-0214 D8：core purposes 域包契约在 seed 之后确定性载入
        # （与 composition 注册表消费 COMPOSITION_PACK_TEMPLATES 同构）。
        from app.lib.cartography.composition_packs.core_purposes import (
            CORE_PURPOSE_CONTRACTS,
        )
        for contract in CORE_PURPOSE_CONTRACTS:
            if contract.contract_id in self._by_id:
                raise ValueError(f"duplicate contract id: {contract.contract_id}")
            self._by_id[contract.contract_id] = contract

    def register(self, contract: CompositionContractV1) -> None:
        if contract.contract_id in self._by_id:
            raise ValueError(f"duplicate contract id: {contract.contract_id}")
        self._by_id[contract.contract_id] = contract

    def get(self, contract_id: str) -> Optional[CompositionContractV1]:
        return self._by_id.get(contract_id)

    def has(self, contract_id: str) -> bool:
        return contract_id in self._by_id

    def all_contracts(self) -> List[CompositionContractV1]:
        return sorted(self._by_id.values(), key=lambda c: c.contract_id)

    def for_purpose(self, purpose: str) -> List[CompositionContractV1]:
        return [c for c in self.all_contracts() if c.purpose == purpose]

    def validate(self) -> List[str]:
        """fail-closed：引用完整性（模板/槽位/组件模板/链接端点/词表/模型）。"""
        issues: List[str] = []
        try:
            from app.lib.cartography.composition_templates import (
                get_composition_template_registry,
            )
            from app.lib.cartography.component_templates import (
                get_component_template_registry,
            )
            from app.lib.cartography.model_library import get_map_model_registry
            comp_reg = get_composition_template_registry()
            ct_reg = get_component_template_registry()
            model_reg = get_map_model_registry()
            for contract in self._by_id.values():
                cid = contract.contract_id
                tpl = comp_reg.get(contract.template_id)
                if tpl is None:
                    issues.append(f"contract {cid}: template {contract.template_id} 未注册")
                    continue
                tslots = {s.id: s for s in tpl.component_slots}
                for slot in contract.slots:
                    if slot.slot_id not in tslots:
                        issues.append(f"contract {cid}: slot {slot.slot_id} 不在模板中")
                        continue
                    if slot.preferred_template:
                        ct = ct_reg.get(slot.preferred_template)
                        if ct is None:
                            issues.append(
                                f"contract {cid}: preferred_template "
                                f"{slot.preferred_template} 未注册")
                        elif ct.component_type not in tslots[slot.slot_id].allowed_component_types:
                            issues.append(
                                f"contract {cid}: preferred_template "
                                f"{slot.preferred_template} 类型不在槽位允许清单")
                for link in contract.links:
                    if link.type not in CONTRACT_LINK_TYPES:
                        issues.append(f"contract {cid}: link 未知类型 {link.type}")
                    if link.src_slot not in tslots or link.dst_slot not in tslots:
                        issues.append(
                            f"contract {cid}: link 端点悬空 "
                            f"{link.src_slot}->{link.dst_slot}")
                # review P2-7：slot 链接环在创作期拒绝 —— 否则首个 apply 写入
                # 环后，该会话此后每次 apply 都被转发 cycle error fail-closed
                # （自锁会话）。
                cycle = _contract_slot_cycle(contract)
                if cycle:
                    issues.append(
                        f"contract {cid}: slot link 成环: "
                        f"{' -> '.join(cycle[:6])}")
                if contract.style_token_preset and contract.style_token_preset not in _TOKEN_PRESETS:
                    issues.append(
                        f"contract {cid}: style_token_preset "
                        f"{contract.style_token_preset} 不在词表")
                for target in contract.export_targets:
                    if target not in _EXPORT_TARGETS:
                        issues.append(f"contract {cid}: export target {target} 不在词表")
                for mid in contract.compatible_map_models:
                    if model_reg.resolve(mid) is None:
                        issues.append(f"contract {cid}: map model {mid} 未注册")
                if contract.min_abi_version > COMPONENT_ABI_VERSION:
                    issues.append(
                        f"contract {cid}: min_abi {contract.min_abi_version} > "
                        f"当前 ABI {COMPONENT_ABI_VERSION}")
        except Exception as e:  # pragma: no cover - 防御性
            issues.append(f"contract validation error: {e}")
        return issues


_registry: Optional[ContractRegistry] = None


def get_contract_registry() -> ContractRegistry:
    global _registry
    if _registry is None:
        _registry = ContractRegistry()
        _registry.load_builtins()
    return _registry


def reset_contract_registry() -> None:
    global _registry
    _registry = None


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "CONTRACT_LINK_TYPES",
    "LOCK_REASON_USER_WINS",
    "ContractSlot",
    "ContractLink",
    "CompositionContractV1",
    "CompositionIdentity",
    "ContractApplyError",
    "ApplyReport",
    "ContractRegistry",
    "SEED_CONTRACTS",
    "get_contract_registry",
    "reset_contract_registry",
    "contract_fingerprint",
    "diff_contracts",
    "apply_contract",
    "read_composition_identity",
    "locked_component_ids_of",
]
