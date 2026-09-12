"""Templates V9 —— 版本化 / 继承覆盖 / 失效迁移 / 组件约束校验（P4）。

任务书 §2 P4：

- **版本化**：每次变更落不可变快照（``TemplateVersion``，version = max+1），
  主表 payload 恒指向"当前版"（兼容既有 apply_template 读路径）；
- **继承**：``parent_version_id`` 链（可跨模板 —— 子模板继承父模板某版），
  覆盖语义 = 子 payload **深合并**于父 effective payload（dict 递归合并、
  标量/列表整体覆盖）；环检测 + 深度帽（≤16）；
- **失效迁移**：``deprecated_at`` 标记而非物理删 —— 兼容读取继续可用，
  响应显式携带 ``deprecated=True``（迁移期兼容语义）；
- **Cartography V7 对齐**：模板 payload 引用的组件 id 必须在组件注册表
  （``component_registry``）存在且可用；引用快照随版本落库。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.template_version import TemplateVersion

logger = logging.getLogger(__name__)

_MAX_CHAIN_DEPTH = 16
_MAX_VIOLATIONS = 16

#: layout 类模板的布尔开关 → 组件 id（有界映射表；V7 注册表对齐的桥）。
_LAYOUT_SWITCH_COMPONENTS: Dict[str, str] = {
    "showLegend": "legend",
    "showNorthArrow": "north_arrow",
    "showScaleBar": "scale_bar",
    "showGraticule": "graticule",
    "showMapBorder": "map_border",
}


class TemplateVersionError(Exception):
    """版本操作非法（环/缺模板/缺版本）——路由层映射 4xx。"""


# ── 组件引用提取与校验 ───────────────────────────────────────────────


def extract_component_refs(payload: Dict[str, Any]) -> List[str]:
    """payload → 组件 id 引用集（显式 components 列表 + layout 开关映射）。"""
    refs: List[str] = []
    seen: Set[str] = set()

    def _add(cid: Any) -> None:
        text = str(cid or "")[:64]
        if text and text not in seen:
            seen.add(text)
            refs.append(text)

    explicit = payload.get("components")
    if isinstance(explicit, (list, tuple)):
        for item in explicit[:32]:
            if isinstance(item, dict):
                _add(item.get("id") or item.get("component") or item.get("type"))
            else:
                _add(item)
    deps = payload.get("component_dependencies")
    if isinstance(deps, (list, tuple)):
        for item in deps[:32]:
            _add(item)
    for switch, component in _LAYOUT_SWITCH_COMPONENTS.items():
        if payload.get(switch) is True:
            _add(component)
    return refs[:32]


def validate_component_refs(refs: List[str]) -> Dict[str, Any]:
    """引用 → 校验结果（V7 注册表：缺席/不可用 = violation；deprecated = warning）。"""
    from app.lib.cartography.component_registry import get_component_registry

    registry = get_component_registry()
    valid: List[str] = []
    violations: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    for cid in refs:
        desc = registry.get(cid)
        if desc is None:
            violations.append({"component": cid, "reason": "unknown_component"})
            continue
        runtime = str(getattr(desc, "runtime_status", "native"))
        if runtime == "unavailable":
            violations.append({"component": cid, "reason": "runtime_unavailable"})
            continue
        if getattr(desc, "deprecated", False):
            warnings.append({"component": cid, "reason": "component_deprecated"})
        valid.append(cid)
    return {
        "refs": refs,
        "valid": valid,
        "violations": violations[:_MAX_VIOLATIONS],
        "warnings": warnings[:_MAX_VIOLATIONS],
        "ok": not violations,
    }


# ── 深合并（继承覆盖语义） ───────────────────────────────────────────


def deep_merge(parent: Dict[str, Any], child: Dict[str, Any]) -> Dict[str, Any]:
    """child 覆盖 parent：dict 递归合并；标量/列表/None 整体覆盖。"""
    out = dict(parent or {})
    for key, value in (child or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# ── 版本 CRUD ────────────────────────────────────────────────────────


def create_version(
    db: Any,
    template_id: str,
    payload: Dict[str, Any],
    *,
    parent_version_id: Optional[str] = None,
    created_by: Optional[str] = None,
    commit_current: bool = True,
) -> TemplateVersion:
    """落新版本快照（version = max+1；component 校验快照；主表前移）。

    ``parent_version_id`` 缺席时，若模板已有版本 → 继承当前最新版
    （线性链）；首版无父。
    """
    from app.models.db_model import CartographyTemplate

    template = db.get(CartographyTemplate, template_id)
    if template is None:
        raise TemplateVersionError(f"template '{template_id}' not found")

    if parent_version_id is not None:
        parent = db.get(TemplateVersion, parent_version_id)
        if parent is None:
            raise TemplateVersionError("parent_version not found")
        _assert_acyclic(db, parent_version_id, new_template_id=template_id)

    latest = (
        db.query(TemplateVersion)
        .filter_by(template_id=template_id)
        .order_by(TemplateVersion.version.desc())
        .first()
    )
    next_version = (latest.version + 1) if latest is not None else 1
    if parent_version_id is None and latest is not None:
        parent_version_id = latest.id

    refs = extract_component_refs(payload)
    validation = validate_component_refs(refs)
    row = TemplateVersion(
        template_id=str(template_id)[:255],
        version=next_version,
        payload=payload,
        parent_version_id=parent_version_id,
        component_refs=refs,
        component_violations=validation["violations"] or None,
        created_by=(str(created_by)[:255] if created_by else None),
    )
    db.add(row)
    if commit_current:
        # 主表 payload 前移到 **effective**（继承链深合并）—— 既有
        # apply_template 读路径零感知，看到的是合并后的当前版（兼容语义）。
        effective, _chain = resolve_payload(db, row)
        template.payload = effective
        template.version = next_version
    db.commit()
    return row


def _assert_acyclic(db: Any, parent_version_id: str, *, new_template_id: str) -> None:
    """新版本行指向既有行时拓扑上不可能成环（新行尚无子边）——此守卫
    实际承担**深度帽**与父链可见性校验（防御脏数据手工构造的环）。"""
    seen: Set[str] = set()
    cursor: Optional[str] = parent_version_id
    depth = 0
    while cursor is not None and depth <= _MAX_CHAIN_DEPTH:
        if cursor in seen:
            raise TemplateVersionError("inheritance cycle detected")
        seen.add(cursor)
        node = db.get(TemplateVersion, cursor)
        if node is None:
            break
        cursor = node.parent_version_id
        depth += 1
    if depth > _MAX_CHAIN_DEPTH:
        raise TemplateVersionError(f"inheritance chain > {_MAX_CHAIN_DEPTH}")


def resolve_payload(db: Any, version: TemplateVersion) -> Tuple[Dict[str, Any], List[str]]:
    """版本 → effective payload（沿 parent 链深合并，子覆盖父）+ 链上模板 id。"""
    chain: List[TemplateVersion] = []
    seen: Set[str] = set()
    cursor: Optional[TemplateVersion] = version
    while cursor is not None and len(chain) <= _MAX_CHAIN_DEPTH:
        if cursor.id in seen:
            raise TemplateVersionError("inheritance cycle detected")
        seen.add(cursor.id)
        chain.append(cursor)
        cursor = db.get(TemplateVersion, cursor.parent_version_id) \
            if cursor.parent_version_id else None
    effective: Dict[str, Any] = {}
    for node in reversed(chain):          # 祖先 → 自身；后者覆盖前者
        if isinstance(node.payload, dict):
            effective = deep_merge(effective, node.payload)
    return effective, [n.template_id for n in reversed(chain)]


def get_version(db: Any, template_id: str, version: Optional[int] = None) -> TemplateVersion:
    stmt = db.query(TemplateVersion).filter_by(template_id=template_id)
    row = (
        stmt.order_by(TemplateVersion.version.desc()).first()
        if version is None else stmt.filter_by(version=int(version)).first()
    )
    if row is None:
        raise TemplateVersionError("version not found")
    return row


def deprecate_version(
    db: Any,
    template_id: str,
    version: Optional[int],
    *,
    note: str = "",
    actor: Optional[str] = None,
) -> TemplateVersion:
    """失效标记（不物理删 —— 迁移期兼容读取：resolve 仍可用，响应带标记）。"""
    row = get_version(db, template_id, version)
    row.deprecated_at = datetime.utcnow()
    row.deprecation_note = str(note or "")[:500]
    db.commit()
    return row


__all__ = [
    "TemplateVersionError",
    "extract_component_refs",
    "validate_component_refs",
    "deep_merge",
    "create_version",
    "resolve_payload",
    "get_version",
    "deprecate_version",
]
