"""G04 W2（CM-02）：模板→组件契约的全覆盖机读闭环.

35+ 组合模板（seed + 域包）逐一锁定：
- 槽位 ``allowed_component_types`` 闭合于 ComponentRegistry 词表（模板不
  引用不存在的组件类型 —— 契约撒谎在注册面即失败）；
- ``position_zone``/``fallback_zones`` 闭合于 descriptor ``allowed_positions``
  （模板不得要求组件去它声明不支持的位置）；
- ``preferred_templates`` 闭合于 ComponentTemplateRegistry；
- cardinality 语义自洽（required ⟺ min_count ≥ 1；forbidden ⟺
  max_count == 0 —— 禁忌槽的机器可读形态）；
- bind_scope 词表闭合（primary|all_thematic，无第三态）。

这是「模板语法 = ComponentRegistry 可读取契约」的注册面总门 —— 新模板
（含 DB asset upsert）进注册表即被约束（registry.validate fail-closed，
本测试锁其覆盖面不被绕过）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.component_registry import get_component_registry
from app.lib.cartography.component_templates import (
    get_component_template_registry,
)
from app.lib.cartography.composition_templates import (
    get_composition_template_registry,
)

pytestmark = pytest.mark.cartography

_VALID_BIND_SCOPES = frozenset({"primary", "all_thematic"})


def test_every_template_slot_closes_over_component_registry():
    registry = get_component_registry()
    known_types = set(registry.all_ids)
    assert known_types, "component registry must not be empty"
    missing: list[tuple[str, str, str]] = []
    for tpl in get_composition_template_registry().all_templates():
        for slot in tpl.component_slots:
            for ctype in slot.allowed_component_types:
                if ctype not in known_types:
                    missing.append((tpl.id, slot.id, ctype))
    assert not missing, f"slots reference unknown component types: {missing[:8]}"


def test_every_template_zone_closes_over_descriptor_positions():
    registry = get_component_registry()
    for tpl in get_composition_template_registry().all_templates():
        for slot in tpl.component_slots:
            allowed_zones: set[str] = set()
            for ctype in slot.allowed_component_types:
                desc = registry.get_by_type(ctype)
                if desc is None:
                    continue
                allowed_zones.update(desc.allowed_positions or ())
            for zone in (slot.position_zone, *slot.fallback_zones):
                if zone == "none" or not allowed_zones:
                    continue
                assert zone in allowed_zones, (
                    f"{tpl.id}/{slot.id}: zone {zone!r} not in "
                    f"descriptor allowed_positions {sorted(allowed_zones)}"
                )


def test_every_preferred_component_template_resolves():
    comp_templates = get_component_template_registry()
    known = set(comp_templates.all_ids)
    assert known, "component template registry must not be empty"
    for tpl in get_composition_template_registry().all_templates():
        for slot in tpl.component_slots:
            for pref in slot.preferred_templates:
                assert pref in known, (
                    f"{tpl.id}/{slot.id}: preferred template "
                    f"{pref!r} not in component template registry"
                )


def test_cardinality_semantics_are_machine_consistent():
    for tpl in get_composition_template_registry().all_templates():
        for slot in tpl.component_slots:
            if slot.cardinality == "forbidden":
                assert slot.max_count == 0, (
                    f"{tpl.id}/{slot.id}: forbidden 必须同时 max_count=0"
                )
            if slot.cardinality == "required":
                assert slot.min_count >= 1, (
                    f"{tpl.id}/{slot.id}: required 必须 min_count>=1"
                )
            assert slot.max_count == 0 or slot.min_count <= slot.max_count, (
                f"{tpl.id}/{slot.id}: min/max 失自洽"
            )
            assert slot.bind_scope in _VALID_BIND_SCOPES, (
                f"{tpl.id}/{slot.id}: bind_scope {slot.bind_scope!r} "
                "超出词表（primary|all_thematic）"
            )


def test_registry_validate_reports_no_issues_for_registered_set():
    """fail-closed 注册门在全量注册后必须报零 issue（覆盖面不被绕过）。"""
    issues = get_composition_template_registry().validate()
    assert issues == [], issues[:8]
