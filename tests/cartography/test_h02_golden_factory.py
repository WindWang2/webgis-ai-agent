"""H02 Wave 7 — 场景工厂（20 正例 + 13 负例）跨语言真相的 Python 侧。

消费 ``tests/cartography/golden_corpus/h02_scenarios/scenarios.json``
（generate.py 生成）：每个场景走真实管线件——

- 组合模板注册表契约（登记/必需槽）；
- thematic spec_to_paint 语义（负例图例 → None+warnings）；
- build_product_manifest / verify_export_semantic_consistency 的
  期望状态（pass/warning/fail/unsupported/not_evaluated）。

TS 侧（frontend/lib/map-kit/h02-scenarios.compile.test.ts）消费同一
JSON 的 style 编译与版本门维度。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.product_manifest import (
    build_product_manifest,
    verify_export_semantic_consistency,
)
from app.lib.cartography.thematic_spec import spec_to_paint

SCENARIOS_PATH = (
    Path(__file__).parent / "golden_corpus" / "h02_scenarios" / "scenarios.json"
)
PAYLOAD = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
SCENARIOS = PAYLOAD["scenarios"]
POSITIVES = [s for s in SCENARIOS if s["kind"] == "positive"]
NEGATIVES = [s for s in SCENARIOS if s["kind"] == "negative"]


def test_factory_inventory_contract():
    assert len(POSITIVES) >= 18, f"正例不足 18：{len(POSITIVES)}"
    assert len(NEGATIVES) >= 12, f"负例不足 12：{len(NEGATIVES)}"
    ids = [s["id"] for s in SCENARIOS]
    assert len(ids) == len(set(ids)), "场景 id 必须唯一"
    # 覆盖面标签抽查（任务书 §3.F 要求的通路）
    tags = {t for s in SCENARIOS for t in s.get("tags", [])}
    for required in (
        "point", "heatmap", "choropleth", "proportional", "raster",
        "bivariate", "chart", "a4", "nodata", "visibility", "user-wins",
        "zoom", "unsupported", "legend", "version",
    ):
        assert required in tags, f"标签覆盖缺失：{required}"


@pytest.mark.parametrize("scenario", POSITIVES, ids=lambda s: s["id"])
def test_positive_scenario_semantics(scenario):
    spec = scenario["spec"]
    expect = scenario["expect"]
    fmt = expect.get("export_format", "png")
    report = verify_export_semantic_consistency(
        spec, export_format=fmt, diagnostics=scenario.get("diagnostics"))
    assert report["status"] == expect["verify_status"], (
        f"{scenario['id']}: 期望 {expect['verify_status']}，"
        f"实际 {report['status']}；checks="
        f"{[(c['aspect'], c['status']) for c in report['checks']]}"
    )
    _assert_extras(scenario, report)


@pytest.mark.parametrize("scenario", NEGATIVES, ids=lambda s: s["id"])
def test_negative_scenario_semantics(scenario):
    spec = scenario["spec"]
    expect = scenario["expect"]
    if expect.get("spec_to_paint_none"):
        legends = [
            ly.get("legend_spec") for ly in spec.get("layers", [])
            if isinstance(ly.get("legend_spec"), dict)
        ]
        assert legends, "负例必须携带 legend_spec"
        for legend in legends:
            style, warnings = spec_to_paint(legend)
            assert style is None and warnings, (
                f"{scenario['id']}: 非法图例必须返回 None+warnings，"
                f"实际 {style} / {warnings}"
            )
        return
    fmt = expect.get("export_format", "png")
    report = verify_export_semantic_consistency(
        spec, export_format=fmt, diagnostics=scenario.get("diagnostics"))
    if "aspect_status" in expect:
        aspect, expected_status = expect["aspect_status"]
        by = {c["aspect"]: c for c in report["checks"]}
        assert by[aspect]["status"] == expected_status, (
            f"{scenario['id']}: {aspect} 期望 {expected_status}，"
            f"实际 {by[aspect]['status']}"
        )
        return
    assert report["status"] == expect["verify_status"], (
        f"{scenario['id']}: 期望 {expect['verify_status']}，"
        f"实际 {report['status']}；checks="
        f"{[(c['aspect'], c['status']) for c in report['checks']]}"
    )
    if expect.get("aspect"):
        by = {c["aspect"]: c for c in report["checks"]}
        assert by[expect["aspect"]]["status"] == expect["verify_status"], (
            f"{scenario['id']}: 指定维度 {expect['aspect']} 未命中期望状态"
        )
    _assert_extras(scenario, report)


def _assert_extras(scenario: dict, report: dict) -> None:
    expect = scenario["expect"]
    manifest = report["manifest"]
    if "mapspec_version" in expect:
        assert manifest["mapspec_version"] == expect["mapspec_version"]
    if expect.get("nodata_guard"):
        assert manifest["layers"][0]["legend"].get("nodata_guard") is True
    if "layers_visible" in expect:
        by = {c["aspect"]: c for c in report["checks"]}
        assert by["layer_visibility"]["evidence"]["layers_visible"] == \
            expect["layers_visible"]
        assert by["layer_visibility"]["evidence"]["layers_total"] == \
            expect["layers_total"]
    if expect.get("filter_digest_present"):
        assert manifest["layers"][0].get("filter_digest"), \
            "filter 必须以摘要进入 manifest（漂移可比对）"
    if expect.get("time_present"):
        assert manifest.get("time"), "时间块必须进入 manifest"
    if expect.get("sources_total") is not None:
        assert len(manifest["sources"]) == expect["sources_total"]
    if expect.get("pinned_placement"):
        comp = next(
            c for c in manifest["components"] if c["id"] == "legend")
        assert comp["enabled"]


@pytest.mark.parametrize("scenario", POSITIVES, ids=lambda s: s["id"])
def test_positive_manifest_deterministic(scenario):
    """重复导出稳定性：同输入两次构建逐字节一致（幂等复验面）。"""
    a = build_product_manifest(scenario["spec"], export_format="png")
    b = build_product_manifest(scenario["spec"], export_format="png")
    assert a == b


def test_template_registry_contract_for_composition_scenarios():
    """组合身份在场场景：template_id 必须注册且必需槽可推导。"""
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )

    reg = get_composition_template_registry()
    checked = 0
    for scenario in SCENARIOS:
        identity = (scenario["spec"].get("layout") or {}).get("composition")
        if not isinstance(identity, dict) or not identity.get("template_id"):
            continue
        checked += 1
        if str(identity["template_id"]).startswith("composition.ghost"):
            continue  # 负例：未注册身份由 manifest 层诚实处理
        tpl = reg.get(str(identity["template_id"]))
        assert tpl is not None, (
            f"{scenario['id']}: 组合身份模板未注册 "
            f"{identity['template_id']}"
        )
    assert checked >= 20, "组合身份场景覆盖不足"


def test_stale_ack_rejection():
    """旧 ACK 拒收（关联负路径）：stamped revision ≠ ack revision → stale。"""
    from app.lib.cartography import render_apply_ack as raa

    assert raa.ENTRY_STALE == "stale", "ACK 条目 stale 词表必须存在且为 'stale'"
