"""别名 canonicalization 契约（R2 架构审查 MAJOR 修复的锁定测试）.

MapModelRegistry.resolve() 别名感知，但 resolver/composition 的键匹配是
集合成员判定 —— 入口不 canonicalize 会让 resolve("choropleth") 与
resolve("administrative_choropleth") 静默分叉（别名路径丢图例槽）。
本文件锁定：全量别名 × output target 的选型必须与 canonical 完全一致。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.model_library import get_map_model_registry
from app.services.gis_harness.component_composer import ComponentComposer
from app.services.gis_harness.component_resolver import ComponentResolver

pytestmark = pytest.mark.cartography

OUTPUT_TARGETS = ("interactive", "png", "pdf")


def _alias_inventory() -> list:
    reg = get_map_model_registry()
    pairs = []
    for alias, target in sorted(reg._alias.items()):
        canonical = reg.resolve(target)
        if canonical is not None:
            pairs.append((alias, canonical.id))
    return pairs


def test_alias_selection_matches_canonical_for_all_outputs() -> None:
    resolver = ComponentResolver()
    checked = 0
    for alias, canonical in _alias_inventory():
        for output in OUTPUT_TARGETS:
            a = resolver.resolve(map_model_id=alias, output_target=output)
            c = resolver.resolve(map_model_id=canonical, output_target=output)
            assert a.composition_template_id == c.composition_template_id, (
                f"别名 {alias}@{output}: {a.composition_template_id} != "
                f"canonical {canonical} 的 {c.composition_template_id}"
            )
            assert a.selected == c.selected
            assert a.component_templates == c.component_templates
            checked += 1
    assert checked >= len(_alias_inventory()) * len(OUTPUT_TARGETS)


def test_alias_compose_matches_canonical() -> None:
    resolver = ComponentResolver()
    composer = ComponentComposer()
    for alias, canonical in _alias_inventory()[:8]:
        a = resolver.resolve(map_model_id=alias, output_target="interactive")
        c = resolver.resolve(map_model_id=canonical, output_target="interactive")
        ca = composer.compose(
            a, title_text="别名 parity", layer_bindings={"primary": "layer-main"},
            layer_model_ids={"layer-main": canonical},
        )
        cc = composer.compose(
            c, title_text="别名 parity", layer_bindings={"primary": "layer-main"},
            layer_model_ids={"layer-main": canonical},
        )
        sig_a = [(x.id, x.type, x.position) for x in ca]
        sig_c = [(x.id, x.type, x.position) for x in cc]
        assert sig_a == sig_c, f"别名 {alias}: compose 结果分叉"


def test_alias_layer_bindings_get_legends() -> None:
    """别名路径不得再静默降级丢图例（R2 MAJOR 的原始反例）。"""
    resolver = ComponentResolver()
    sel = resolver.resolve(
        map_model_id="isochrone_overlay",  # service_area_overlay 的别名
        composition_template_id="composition.service_area_report",
        output_target="pdf",
    )
    assert sel.composition_template_id == "composition.service_area_report"
    assert sel.component_templates.get("legend") is not None
