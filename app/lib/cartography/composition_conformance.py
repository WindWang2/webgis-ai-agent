"""Composition Conformance v1 — 组合一致性纯函数报告（ADR-0214 D5）.

把散落各处的组合级义务变成一份**有界、确定性、可序列化**的 issue 清单：

- ``export_parity_gap``（error）：在场组件类型 × 声明导出目标 × renderer
  真值矩阵（``_SUPPORT_MATRIX`` 的只读投影；live 目标查 renderer 通道、
  导出目标查 exporter 通道 —— 矩阵双通道分开登记）；``EXPORT_PARITY_EXEMPT_TYPES``
  豁免单源沿用 ADR-0211，本模块不自建第二份豁免表；
- ``version_incompatible``（error）：契约 ``min_abi_version`` > 当前
  ``COMPONENT_ABI_VERSION``；``abi_older_in_spec``（warning）：spec 身份块
  记录的 ABI 版本落后于当前表；``contract_drift``（warning）：身份块指纹
  与当前契约内容不一致（契约在最近一次 apply 后又被改动）；
- ``slot_zone_invalid``（warning）：**模板创作期**检查 —— slot 的
  position_zone（或任一 fallback_zones）∉ 该槽允许类型的 descriptor
  ``allowed_positions`` 并集。单独暴露
  ``validate_template_slot_zones`` 供 ``CompositionTemplateRegistry.validate``
  创作期接线（存量 seed/pack 必须零 issue，测试锁）；
- ``a11y_undisclosed``（warning）：native 在场类型 descriptor 的
  accessibility.role 为空 —— 诚实披露，不否决；
- 图级结构码转发：``build_component_graph`` + ``validate_component_graph``
  的既有码表（cycle / duplicate_binding / unknown_component_type /
  orphan_binding）原样映射 severity；required 槽缺失在本地按模板槽位
  语义实现（``required_slot_missing``，error）—— 不 import
  composition_validation（那里的组合校验更重，属运行期链路）。

红线：纯函数（只读 spec/contract/template，不改输入）、确定性（issue 按
(severity_rank, code, ids) 全序）、有界（``MAX_CONFORMANCE_ISSUES`` 封顶 +
截断披露）；不做布局计算、不做渲染、不改 MapSpec。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from app.lib.cartography.component_abi import COMPONENT_ABI_VERSION
from app.lib.cartography.component_graph import (
    build_component_graph,
    validate_component_graph,
)
from app.lib.cartography.component_renderers import (
    EXPORT_PARITY_EXEMPT_TYPES,
    LIVE_TARGET,
    get_component_renderer_registry,
)
from app.lib.cartography.composition_contract import (
    CompositionContractV1,
    contract_fingerprint,
    read_composition_identity,
)
from app.lib.cartography.composition_templates import (
    ComponentSlot,
    MapCompositionTemplate,
)

#: 报告级封顶：超出即截断并追加 ``conformance_truncated`` 披露 issue。
MAX_CONFORMANCE_ISSUES = 32

#: 单条 issue 的 ids 封顶（实例清单聚合披露；全量以 message 计数补充）。
_MAX_IDS_PER_ISSUE = 8

#: message 字符封顶（与 graph issue 的披露口径一致）。
_MAX_MESSAGE = 200

#: 未声明导出目标时的默认核查面（png 是最低保真导出义务）。
_DEFAULT_EXPORT_TARGETS: Tuple[str, ...] = ("png",)

_SEVERITY_RANK: Dict[str, int] = {"error": 0, "warning": 1}


class ConformanceIssue(BaseModel):
    """一条 conformance 结论（code + 两档 severity + 有界证据）。"""

    code: str
    severity: Literal["warning", "error"]
    message: str
    ids: List[str] = Field(default_factory=list)


# ── 输入投影（MapSpec dict 只读小助手）──────────────────────────────────


def _components_of(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``layout.components`` 的 dict 项投影（结构损坏 → 空表，不抛）。"""
    layout = spec.get("layout") if isinstance(spec, dict) else None
    if not isinstance(layout, dict):
        return []
    raw = layout.get("components")
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def _present_components(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """enabled 缺省 True；显式 False 才不参与在场判定（与图投影同口径）。"""
    return [c for c in _components_of(spec) if c.get("enabled") is not False]


def _instances_by_type(spec: Dict[str, Any]) -> Dict[str, List[str]]:
    """type → 有序实例 id 清单（确定性聚合）。"""
    by_type: Dict[str, List[str]] = {}
    for component in _components_of(spec):
        ctype = str(component.get("type") or "")[:32]
        if ctype:
            by_type.setdefault(ctype, []).append(str(component.get("id") or "")[:48])
    return {ctype: sorted(ids) for ctype, ids in by_type.items()}


# ── D5-1：导出 parity（真值矩阵单源）────────────────────────────────────


def _export_targets_of(
    contract: Optional[CompositionContractV1],
    export_targets: Optional[Tuple[str, ...]],
) -> Tuple[str, ...]:
    """显式参数 > 契约声明 > 默认 png（与设计文档同优先级）。"""
    if export_targets is not None:
        return tuple(str(t) for t in export_targets)[:6]
    if contract is not None and contract.export_targets:
        return tuple(contract.export_targets)[:6]
    return _DEFAULT_EXPORT_TARGETS


def _type_supports_target(renderer_reg: Any, ctype: str, target: str) -> bool:
    """type × target 的真值矩阵支持判定（单源：component_renderers）。

    live 目标（``interactive``）查 renderer 支持面、导出目标查 exporter
    支持面 —— 真值矩阵把两条通道分开登记（exporter 列表按构造永不含
    ``interactive``），混查会把所有 live 契约误报成 gap。
    """
    if target == LIVE_TARGET:
        return renderer_reg.has_renderer(ctype, target)
    return renderer_reg.has_exporter(ctype, target)


def _export_parity_issues(
    spec: Dict[str, Any],
    contract: Optional[CompositionContractV1],
    export_targets: Optional[Tuple[str, ...]],
) -> List[ConformanceIssue]:
    # Review P1-1：parity 只对**在场**（enabled is not False）实例负责 ——
    # 用户显式关闭的组件没有呈现/导出义务（与 viewport_export.assess_export_parity
    # 同口径）；矩阵外类型跳过（unknown_component_type 已由图转发披露）；
    # 矩阵声明为空（renderers=[]/exporters=[]，如 label_layer 经图层子通道
    # 渲染 —— ADR-0154）= 组件自声明不经此通道，降为披露级 warning，
    # 不构成 error 阻断（否则合法会话被永久 fail-closed）。
    present = _present_components(spec)
    by_type: Dict[str, List[str]] = {}
    for component in present:
        ctype = str(component.get("type") or "")[:32]
        if ctype:
            by_type.setdefault(ctype, []).append(
                str(component.get("id") or "")[:48])
    if not by_type:
        return []
    by_type = {ctype: sorted(ids) for ctype, ids in by_type.items()}
    targets = _export_targets_of(contract, export_targets)
    renderer_reg = get_component_renderer_registry()
    exempt = set(EXPORT_PARITY_EXEMPT_TYPES)
    issues: List[ConformanceIssue] = []
    for ctype in sorted(by_type):
        if ctype in exempt:
            continue  # 豁免单源（ADR-0211）：basemap 由导出管线自身消费
        support = renderer_reg.support_for(ctype)
        if support is None:
            continue  # 矩阵外类型：unknown_component_type（warning）已覆盖
        if not support.renderers and not support.exporters:
            ids = by_type[ctype]
            issues.append(ConformanceIssue(
                code="export_channel_indirect", severity="warning",
                message=(f"组件类型 {ctype} 经其他渲染/导出子通道消费"
                         f"（矩阵诚实声明为空；{', '.join(ids[:_MAX_IDS_PER_ISSUE])}）")
                [:_MAX_MESSAGE],
                ids=ids[:_MAX_IDS_PER_ISSUE],
            ))
            continue
        for target in targets:
            if _type_supports_target(renderer_reg, ctype, target):
                continue
            ids = by_type[ctype]
            shown = ids[:_MAX_IDS_PER_ISSUE]
            suffix = "" if len(ids) <= _MAX_IDS_PER_ISSUE else f" 等 {len(ids)} 实例"
            issues.append(ConformanceIssue(
                code="export_parity_gap", severity="error",
                message=(f"组件类型 {ctype} 无 {target} 导出器消费"
                         f"（{', '.join(shown)}{suffix}）")[:_MAX_MESSAGE],
                ids=shown,
            ))
    return issues


# ── D5-2：版本兼容（契约 min_abi / 身份块 ABI / 契约指纹漂移）──────────


def _version_issues(
    spec: Dict[str, Any],
    contract: Optional[CompositionContractV1],
) -> List[ConformanceIssue]:
    issues: List[ConformanceIssue] = []
    if contract is not None and contract.min_abi_version > COMPONENT_ABI_VERSION:
        issues.append(ConformanceIssue(
            code="version_incompatible", severity="error",
            message=(f"契约 {contract.contract_id} 要求 min_abi="
                     f"{contract.min_abi_version} > 当前 ABI "
                     f"{COMPONENT_ABI_VERSION}")[:_MAX_MESSAGE],
            ids=[contract.contract_id[:48]],
        ))
    identity = read_composition_identity(spec)
    if identity is None:
        return issues
    if identity.component_abi_version < COMPONENT_ABI_VERSION:
        issues.append(ConformanceIssue(
            code="abi_older_in_spec", severity="warning",
            message=(f"spec 身份块 ABI v{identity.component_abi_version} 落后于"
                     f"当前 v{COMPONENT_ABI_VERSION}（建议重新 apply 契约）")[:_MAX_MESSAGE],
            ids=["layout.composition"],
        ))
    if (identity.contract_fingerprint and contract is not None
            and identity.contract_fingerprint != contract_fingerprint(contract)):
        issues.append(ConformanceIssue(
            code="contract_drift", severity="warning",
            message=(f"spec 身份块指纹与契约 {contract.contract_id} 当前内容"
                     f"不一致（契约在最近一次 apply 后被改动）")[:_MAX_MESSAGE],
            ids=[(identity.contract_id or contract.contract_id)[:48]],
        ))
    return issues


# ── D5-3：模板创作期 slot zone 检查（CompositionTemplateRegistry 接线）──


def validate_template_slot_zones(
    template: MapCompositionTemplate,
) -> List[ConformanceIssue]:
    """模板创作期：slot zone 必须 ∈ 允许类型 descriptor 位置并集。

    语义：``position_zone == "none"`` 的槽位不检查（zone 裁决交给
    descriptor default_position / 运行期）；其余槽位要求 position_zone
    **或任一 fallback_zones** 条目落在该槽 allowed_component_types 的
    descriptor ``allowed_positions`` 并集内（有合法退路即可放置）。
    类型不可解析的槽位在此跳过（registry validate 已报 unknown type，
    不重复否决）。确定性：按模板槽位声明序输出。
    """
    from app.lib.cartography.component_registry import get_component_registry

    comp_reg = get_component_registry()
    issues: List[ConformanceIssue] = []
    for slot in template.component_slots:
        if not _slot_zone_checkable(slot):
            continue
        allowed = _slot_allowed_positions(comp_reg, slot)
        if not allowed:
            continue
        zones = [slot.position_zone, *slot.fallback_zones]
        if any(zone in allowed for zone in zones):
            continue
        issues.append(ConformanceIssue(
            code="slot_zone_invalid", severity="warning",
            message=(f"槽位 zone {slot.position_zone}（fallback: "
                     f"{','.join(slot.fallback_zones) or '无'}）不在允许类型"
                     f"位置并集 {sorted(allowed)} 内")[:_MAX_MESSAGE],
            ids=[slot.id[:48]],
        ))
    return issues


def _slot_zone_checkable(slot: ComponentSlot) -> bool:
    return slot.position_zone != "none"


def _slot_allowed_positions(comp_reg: Any, slot: ComponentSlot) -> set:
    allowed: set = set()
    for ctype in slot.allowed_component_types:
        desc = comp_reg.get_by_type(str(ctype))
        if desc is not None:
            allowed.update(str(p) for p in desc.allowed_positions)
    return allowed


# ── D5-4：a11y 诚实披露（不否决）────────────────────────────────────────


def _a11y_issues(spec: Dict[str, Any]) -> List[ConformanceIssue]:
    from app.lib.cartography.component_registry import get_component_registry

    comp_reg = get_component_registry()
    # 与 parity 同口径：只披露在场（enabled）实例 —— 关闭的组件无呈现面。
    present_by_type: Dict[str, List[str]] = {}
    for component in _present_components(spec):
        ctype = str(component.get("type") or "")[:32]
        if ctype:
            present_by_type.setdefault(ctype, []).append(
                str(component.get("id") or "")[:48])
    issues: List[ConformanceIssue] = []
    for ctype, ids in sorted(present_by_type.items()):
        desc = comp_reg.get_by_type(ctype)
        if desc is None or desc.runtime_status != "native":
            continue
        role = desc.accessibility.role if desc.accessibility is not None else ""
        if role:
            continue
        shown = ids[:_MAX_IDS_PER_ISSUE]
        suffix = "" if len(ids) <= _MAX_IDS_PER_ISSUE else f" 等 {len(ids)} 实例"
        issues.append(ConformanceIssue(
            code="a11y_undisclosed", severity="warning",
            message=(f"组件类型 {ctype} 的 accessibility.role 未披露"
                     f"（{', '.join(shown)}{suffix}）")[:_MAX_MESSAGE],
            ids=shown,
        ))
    return issues


# ── D5-5：图级结构码转发 + required 槽位（本地槽位语义）─────────────────


def _graph_issues(spec: Dict[str, Any]) -> List[ConformanceIssue]:
    graph = build_component_graph(spec)
    issues: List[ConformanceIssue] = []
    for gi in validate_component_graph(graph):
        issues.append(ConformanceIssue(
            code=gi.code,
            severity="error" if gi.severity == "error" else "warning",
            message=gi.message[:_MAX_MESSAGE],
            ids=[str(x)[:48] for x in gi.ids[:_MAX_IDS_PER_ISSUE]],
        ))
    return issues


def _required_slot_issues(
    template: MapCompositionTemplate,
    spec: Dict[str, Any],
) -> List[ConformanceIssue]:
    """required 槽位无在场（enabled）实例 → error（模板槽位语义，本地实现；
    不 import composition_validation —— 那是运行期更重的组合校验链路）。"""
    present_types = {
        str(c.get("type") or "")
        for c in _present_components(spec)
    }
    issues: List[ConformanceIssue] = []
    for slot in template.component_slots:
        if slot.cardinality != "required" or not slot.allowed_component_types:
            continue
        if present_types & set(slot.allowed_component_types):
            continue
        issues.append(ConformanceIssue(
            code="required_slot_missing", severity="error",
            message=(f"必选槽位 {slot.id} 无在场实例"
                     f"（allowed: {slot.allowed_component_types}）")[:_MAX_MESSAGE],
            ids=[slot.id[:48]],
        ))
    return issues


# ── 报告装配（确定性全序 + 有界封顶）────────────────────────────────────


def conformance_report(
    spec: Dict[str, Any],
    *,
    contract: Optional[CompositionContractV1] = None,
    template: Optional[MapCompositionTemplate] = None,
    export_targets: Optional[Tuple[str, ...]] = None,
) -> List[ConformanceIssue]:
    """MapSpec（+ 可选契约/模板）→ 有界 conformance issue 清单。

    纯函数：只读输入；确定性：issue 按 (severity_rank, code, ids) 全序；
    有界：超过 ``MAX_CONFORMANCE_ISSUES`` 截断并追加
    ``conformance_truncated`` 披露（总数恰为封顶值，不静默丢弃计数）。
    """
    if not isinstance(spec, dict):
        spec = {}
    issues: List[ConformanceIssue] = []
    issues.extend(_export_parity_issues(spec, contract, export_targets))
    issues.extend(_version_issues(spec, contract))
    if template is not None:
        issues.extend(validate_template_slot_zones(template))
        issues.extend(_required_slot_issues(template, spec))
    issues.extend(_a11y_issues(spec))
    issues.extend(_graph_issues(spec))
    issues.sort(key=lambda i: (_SEVERITY_RANK[i.severity], i.code, i.ids))
    if len(issues) > MAX_CONFORMANCE_ISSUES:
        dropped = len(issues) - (MAX_CONFORMANCE_ISSUES - 1)
        issues = issues[:MAX_CONFORMANCE_ISSUES - 1] + [ConformanceIssue(
            code="conformance_truncated", severity="warning",
            message=(f"conformance 报告超过 {MAX_CONFORMANCE_ISSUES} 上限："
                     f"截断 {dropped} 条（修复头部 issue 后重跑）")[:_MAX_MESSAGE],
            ids=[],
        )]
    return issues


def report_to_dicts(issues: List[ConformanceIssue]) -> List[Dict[str, Any]]:
    """工具载荷投影（有界：message ≤160、ids ≤8、条数 ≤ 封顶）。"""
    return [
        {
            "code": issue.code[:48],
            "severity": issue.severity,
            "message": issue.message[:160],
            "ids": [str(x)[:48] for x in issue.ids[:_MAX_IDS_PER_ISSUE]],
        }
        for issue in list(issues)[:MAX_CONFORMANCE_ISSUES]
    ]


__all__ = [
    "MAX_CONFORMANCE_ISSUES",
    "ConformanceIssue",
    "conformance_report",
    "validate_template_slot_zones",
    "report_to_dicts",
]
