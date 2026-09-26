"""Composition Conformance v1 契约测试（ADR-0214 D5）.

覆盖：导出 parity 正/负例（真值矩阵单源 + EXEMPT 豁免）、版本兼容
（version_incompatible / abi_older_in_spec / contract_drift）、slot zone
创作期检查（存量注册表零 issue + 构造坏模板报警）、a11y 诚实披露、
required 槽位、图级码转发（cycle）、确定性、码表封顶截断、有界载荷。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.lib.cartography.component_abi import COMPONENT_ABI_VERSION
from app.lib.cartography.component_renderers import EXPORT_PARITY_EXEMPT_TYPES
from app.lib.cartography.composition_conformance import (
    MAX_CONFORMANCE_ISSUES,
    ConformanceIssue,
    conformance_report,
    report_to_dicts,
    validate_template_slot_zones,
)
from app.lib.cartography.composition_contract import (
    CompositionContractV1,
    apply_contract,
)
from app.lib.cartography.composition_templates import (
    ComponentSlot,
    CompositionTemplateRegistry,
    MapCompositionTemplate,
    get_composition_template_registry,
)

pytestmark = pytest.mark.cartography


def _spec(*components, links=None):
    layout: dict = {"components": list(components)}
    if links is not None:
        layout["component_links"] = links
    return {"layers": [{"id": "layer-primary"}], "layout": layout}


def _codes(issues):
    return [i.code for i in issues]


# ── 导出 parity（真值矩阵单源；EXEMPT 豁免单源）─────────────────────────


class TestExportParity:
    def test_svg_gap_on_png_capable_type(self):
        # export_layout 有 png/pdf 导出器、无 svg —— 声明 svg 才是 gap
        spec = _spec({"id": "export-layout", "type": "export_layout"})
        svg_report = conformance_report(spec, export_targets=("svg",))
        gaps = [i for i in svg_report if i.code == "export_parity_gap"]
        assert gaps and all(i.severity == "error" for i in gaps)
        assert all("svg" in i.message for i in gaps)
        assert gaps[0].ids == ["export-layout"]
        # png 目标下同一 spec 干净（正例：有 png 导出器）
        assert _codes(conformance_report(spec, export_targets=("png",))) == []

    def test_contract_export_targets_drive_check(self):
        spec = _spec({"id": "export-layout", "type": "export_layout"})
        contract = CompositionContractV1(
            contract_id="contract.test.svg",
            template_id="composition.standard_analysis",
            export_targets=("png", "svg"),
        )
        gaps = [i for i in conformance_report(spec, contract=contract)
                if i.code == "export_parity_gap"]
        assert gaps and all("svg" in i.message for i in gaps)

    def test_default_target_is_png(self):
        # 未声明目标 → 默认核查 png（legend 有 png 导出器 → 干净）
        spec = _spec({"id": "legend-main", "type": "legend"})
        assert conformance_report(spec) == []

    def test_exempt_type_not_reported(self):
        assert "basemap" in EXPORT_PARITY_EXEMPT_TYPES
        spec = _spec({"id": "basemap", "type": "basemap"})
        gaps = [i for i in conformance_report(spec, export_targets=("svg",))
                if i.code == "export_parity_gap"]
        assert gaps == []

    def test_ids_aggregate_instances_of_type(self):
        spec = _spec(
            {"id": "chart-a", "type": "export_layout"},
            {"id": "chart-b", "type": "export_layout"},
        )
        gaps = [i for i in conformance_report(spec, export_targets=("svg",))
                if i.code == "export_parity_gap"]
        assert gaps[0].ids == ["chart-a", "chart-b"]


# ── 版本兼容（契约 min_abi / 身份块 ABI / 契约指纹漂移）─────────────────


class TestVersionCompat:
    def test_version_incompatible_error(self):
        contract = CompositionContractV1(
            contract_id="contract.test.future",
            template_id="composition.standard_analysis",
            min_abi_version=COMPONENT_ABI_VERSION + 1,
        )
        issues = conformance_report(_spec(), contract=contract)
        assert [i.code for i in issues] == ["version_incompatible"]
        assert issues[0].severity == "error"
        assert issues[0].ids == ["contract.test.future"]

    def test_abi_older_in_spec_warning(self, monkeypatch):
        # read_composition_identity 把 falsy/0 宽容回填为当前 ABI（契约
        # 模块单一事实，不在本侧改），故「旧 spec」场景按 ABI bump 后的
        # 未来态构造：当前 v1 spec 遇 ABI v2 运行时 → warning。
        from app.lib.cartography import composition_conformance as mod

        spec = _spec({"id": "title", "type": "title"})
        spec["layout"]["composition"] = {
            "template_id": "composition.standard_analysis",
            "template_version": "1.0.0",
            "component_abi_version": 1,
        }
        monkeypatch.setattr(mod, "COMPONENT_ABI_VERSION", 2)
        issues = conformance_report(spec)
        older = [i for i in issues if i.code == "abi_older_in_spec"]
        assert len(older) == 1 and older[0].severity == "warning"

    def test_contract_drift_detected_and_clean_when_fresh(self):
        from app.lib.cartography.composition_contract import get_contract_registry

        contract = get_contract_registry().get("contract.core.basic_thematic")
        assert contract is not None
        spec, _report = apply_contract(
            _spec({"id": "title", "type": "title"}), contract)
        # 新鲜 apply → 指纹一致，无 drift
        assert _codes(conformance_report(spec, contract=contract)) == []
        # 契约内容被改动（模拟 apply 后演进）→ 身份块指纹落后 → drift
        drifted = contract.model_copy(update={"description": "改动后的描述"})
        issues = conformance_report(spec, contract=drifted)
        drift = [i for i in issues if i.code == "contract_drift"]
        assert len(drift) == 1 and drift[0].severity == "warning"

    def test_no_drift_when_identity_has_empty_fingerprint(self):
        contract = CompositionContractV1(
            contract_id="contract.test.x",
            template_id="composition.standard_analysis",
        )
        spec = _spec()
        spec["layout"]["composition"] = {
            "template_id": "composition.standard_analysis",
            "template_version": "1.0.0",
            "contract_fingerprint": "",
        }
        assert _codes(conformance_report(spec, contract=contract)) == []


# ── slot zone 创作期检查（registry validate 接线 + 直接构造）────────────


def _bad_zone_template() -> MapCompositionTemplate:
    """title 只允许 top-center/top-left/none —— bottom-left 必报警。"""
    return MapCompositionTemplate(
        id="composition.bad_zone_fixture",
        component_slots=[
            ComponentSlot(id="title", allowed_component_types=["title"],
                          position_zone="bottom-left"),
        ],
    )


class TestSlotZones:
    def test_bad_template_reports_slot_zone_invalid(self):
        issues = validate_template_slot_zones(_bad_zone_template())
        assert [i.code for i in issues] == ["slot_zone_invalid"]
        assert issues[0].severity == "warning"
        assert issues[0].ids == ["title"]

    def test_fallback_zone_rescues_primary(self):
        tpl = MapCompositionTemplate(
            id="composition.fallback_ok_fixture",
            component_slots=[
                ComponentSlot(id="title", allowed_component_types=["title"],
                              position_zone="bottom-left",
                              fallback_zones=["top-left"]),
            ],
        )
        assert validate_template_slot_zones(tpl) == []

    def test_none_zone_skipped(self):
        tpl = MapCompositionTemplate(
            id="composition.none_zone_fixture",
            component_slots=[
                ComponentSlot(id="map_border",
                              allowed_component_types=["map_border"],
                              position_zone="none"),
            ],
        )
        assert validate_template_slot_zones(tpl) == []

    def test_local_registry_instance_flags_bad_template(self):
        # 局部实例注入坏模板（不动全局单例、不永久注册）
        reg = CompositionTemplateRegistry()
        reg.load_builtins()
        reg.register(_bad_zone_template())
        issues = reg.validate()
        assert issues, "创作期接线应产出 slot zone issue"
        assert any(
            "composition.bad_zone_fixture slot title" in line for line in issues)

    def test_pristine_registry_validate_clean(self):
        assert get_composition_template_registry().validate() == []


# ── a11y 诚实披露（monkeypatch stub descriptor）─────────────────────────


class _StubRegistry:
    """get_by_type 返回无 a11y role 的 native stub（仅测试用；属性面覆盖
    图投影 _project_node 的 descriptor 消费键）。"""

    def __init__(self, role: str = "") -> None:
        self._role = role

    def get(self, descriptor_id: str):
        return None  # 图投影的 by-id 查询路径：stub 无 by-id 目录

    def get_by_type(self, component_type: str):
        return SimpleNamespace(
            type=component_type,
            runtime_status="native",
            accessibility=SimpleNamespace(role=self._role),
            priority=50,
            semantic_role="",
            collision_class="panel",
            dependencies=(),
        )


class TestA11yDisclosure:
    def test_undisclosed_role_warning_capped_ids(self, monkeypatch):
        from app.lib.cartography import component_registry

        monkeypatch.setattr(
            component_registry, "get_component_registry",
            lambda: _StubRegistry(role=""))
        spec = _spec(*[
            {"id": f"mystery-{n}", "type": "mystery_panel"} for n in range(10)
        ])
        issues = [i for i in conformance_report(spec)
                  if i.code == "a11y_undisclosed"]
        assert len(issues) == 1, "同型实例聚合为单条"
        assert issues[0].severity == "warning"
        assert issues[0].ids == [f"mystery-{n}" for n in range(8)]

    def test_disclosed_role_no_issue(self, monkeypatch):
        from app.lib.cartography import component_registry

        monkeypatch.setattr(
            component_registry, "get_component_registry",
            lambda: _StubRegistry(role="img"))
        spec = _spec({"id": "mystery-0", "type": "mystery_panel"})
        # role 已披露 → 无 a11y issue；matrix 外类型（mystery_panel）不再
        # 产生 error 级 export_parity_gap（review P1-1：误报会永久阻断
        # 合法 apply；未知类型由图转发的 unknown_component_type warning
        # 披露）。
        assert "export_parity_gap" not in _codes(conformance_report(spec))
        assert "a11y_undisclosed" not in _codes(conformance_report(spec))


    def test_label_layer_indirect_channel_not_error(self):
        """label_layer（矩阵诚实声明空 = 经图层子通道）→ 披露级 warning，
        不是 error 级 parity gap（review P1-1：不得永久阻断合法 apply）。"""
        spec = _spec({"id": "labels-1", "type": "label_layer"})
        issues = conformance_report(spec)
        codes = _codes(issues)
        assert "export_parity_gap" not in codes
        assert "export_channel_indirect" in codes
        assert all(i.severity == "warning" for i in issues
                   if i.code == "export_channel_indirect")

    def test_disabled_instances_have_no_parity_obligation(self):
        """enabled=False 的实例不产生 parity gap（与生产 validator 同口径）。"""
        spec = _spec({"id": "export-layout", "type": "export_layout",
                      "enabled": False})
        issues = conformance_report(spec, export_targets=("svg",))
        assert "export_parity_gap" not in _codes(issues)


# ── required 槽位 + 图级码转发 ───────────────────────────────────────────


def _required_title_template() -> MapCompositionTemplate:
    return MapCompositionTemplate(
        id="composition.required_title_fixture",
        component_slots=[
            ComponentSlot(id="title", allowed_component_types=["title"],
                          cardinality="required", required=True,
                          position_zone="top-center"),
        ],
    )


class TestRequiredSlotsAndGraph:
    def test_required_slot_missing_error(self):
        tpl = _required_title_template()
        spec = _spec({"id": "legend-main", "type": "legend"})
        issues = [i for i in conformance_report(spec, template=tpl)
                  if i.code == "required_slot_missing"]
        assert len(issues) == 1 and issues[0].severity == "error"
        assert issues[0].ids == ["title"]

    def test_required_slot_present_clean(self):
        tpl = _required_title_template()
        spec = _spec({"id": "title", "type": "title"})
        assert _codes(conformance_report(spec, template=tpl)) == []

    def test_disabled_instance_does_not_fill_required_slot(self):
        tpl = _required_title_template()
        spec = _spec({"id": "title", "type": "title", "enabled": False})
        assert "required_slot_missing" in _codes(
            conformance_report(spec, template=tpl))

    def test_graph_cycle_forwarded_as_error(self):
        spec = _spec(
            {"id": "arrow-a", "type": "north_arrow"},
            {"id": "bar-b", "type": "scale_bar"},
            links=[
                {"src": "arrow-a", "dst": "bar-b", "type": "under",
                 "dst_kind": "component"},
                {"src": "bar-b", "dst": "arrow-a", "type": "under",
                 "dst_kind": "component"},
            ],
        )
        cycles = [i for i in conformance_report(spec) if i.code == "cycle"]
        assert len(cycles) == 1 and cycles[0].severity == "error"
        assert set(cycles[0].ids) == {"arrow-a", "bar-b"}

    def test_unknown_component_type_forwarded_as_warning(self):
        spec = _spec({"id": "ghost", "type": "ghost_panel"})
        unknown = [i for i in conformance_report(spec)
                   if i.code == "unknown_component_type"]
        assert len(unknown) == 1 and unknown[0].severity == "warning"

    def test_empty_spec_clean(self):
        assert conformance_report(_spec()) == []


# ── 确定性 / 封顶 / 有界载荷 ─────────────────────────────────────────────


class TestDeterminismAndBounds:
    def test_deterministic_two_calls_equal(self):
        spec = _spec(
            {"id": "export-layout", "type": "export_layout"},
            {"id": "ghost", "type": "ghost_panel"},
            links=[{"src": "ghost", "dst": "export-layout", "type": "under",
                    "dst_kind": "component"}],
        )
        first = conformance_report(spec, export_targets=("svg",))
        second = conformance_report(spec, export_targets=("svg",))
        assert [i.model_dump() for i in first] == \
            [i.model_dump() for i in second]
        # 全序：(severity_rank, code, ids)
        rank = {"error": 0, "warning": 1}
        keys = [(rank[i.severity], i.code, i.ids) for i in first]
        assert keys == sorted(keys)

    def test_cap_truncation_with_disclosure(self):
        spec = _spec(*[
            {"id": f"ghost-{n:02d}", "type": f"ghost_type_{n:02d}"}
            for n in range(MAX_CONFORMANCE_ISSUES + 1)
        ])
        issues = conformance_report(spec)
        assert len(issues) == MAX_CONFORMANCE_ISSUES
        assert issues[-1].code == "conformance_truncated"
        assert issues[-1].severity == "warning"
        real = [i for i in issues if i.code != "conformance_truncated"]
        assert len(real) == MAX_CONFORMANCE_ISSUES - 1

    def test_report_to_dicts_bounded(self):
        spec = _spec(*[
            {"id": f"m-{n:02d}", "type": f"mystery_type_{n:02d}"}
            for n in range(MAX_CONFORMANCE_ISSUES + 4)
        ])
        payload = report_to_dicts(conformance_report(spec))
        assert len(payload) <= MAX_CONFORMANCE_ISSUES
        for item in payload:
            assert set(item) == {"code", "severity", "message", "ids"}
            assert len(item["message"]) <= 160
            assert len(item["ids"]) <= 8

    def test_non_dict_spec_fail_closed_empty(self):
        assert conformance_report({}) == []  # type: ignore[arg-type]
        assert conformance_report(None) == []  # type: ignore[arg-type]


# ── 与注册表协同（创作期 + 运行期一体）──────────────────────────────────


class TestRegistryCoherence:
    def test_conformance_issue_model_bounded(self):
        issue = ConformanceIssue(
            code="x", severity="warning", message="m", ids=["a"])
        assert issue.model_dump()["ids"] == ["a"]

    def test_pristine_pack_templates_pass_zone_check(self):
        reg = get_composition_template_registry()
        for tpl in reg.all_templates():
            assert validate_template_slot_zones(tpl) == [], tpl.id
