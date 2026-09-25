"""编译前 obligations 六闸测试（F12 / ADR-0214 D4）。

锁定：blocking 闸（锁冲突 / data refs / 组件词表 / renderer / export /
blueprint）fail-closed；advisory 闸（scale/CRS）只 warn 不阻塞。
"""
import pytest

from app.lib.cartography.plan_ir import (
    ComponentIntent,
    DatasetRef,
    ExportObligation,
    LayerBlueprint,
    LayerIntent,
    MapPlanIR,
    UserLockSnapshot,
)
from app.services.map_plan_compiler.obligations import check_obligations


def _ir(*, layer_intents=(), component_intents=(), exports=(),
        datasets=(), user_locks=None) -> MapPlanIR:
    return MapPlanIR(
        ir_id="mpir-oblig01",
        layer_intents=list(layer_intents),
        component_intents=list(component_intents),
        exports=list(exports),
        datasets=list(datasets),
        user_locks=user_locks or UserLockSnapshot(),
    )


def _present_layer(layer_id="pl-1", source_ref="ds:pop", layer_type="fill",
                   locked=False) -> LayerIntent:
    return LayerIntent(
        intent_id=f"li-{layer_id}", action="present_primary",
        layer_id=layer_id, source_ref=source_ref,
        blueprint=LayerBlueprint(layer_type=layer_type), locked=locked,
    )


@pytest.mark.cartography
def test_clean_ir_passes_all_gates():
    ir = _ir(
        layer_intents=[_present_layer()],
        component_intents=[ComponentIntent(intent_id="ci-1", component_type="legend",
                                           component_id="comp-legend", required=True)],
        exports=[ExportObligation(fmt="png")],
    )
    current = {"sources": {"ds:pop": {"type": "geojson"}}, "layers": []}
    report = check_obligations(ir, current)
    assert report.status == "ok"
    assert report.blocking == []


@pytest.mark.cartography
def test_user_lock_conflict_is_blocking():
    locks = UserLockSnapshot(layer_ids=["pl-locked"], component_ids=["comp-locked"])
    ir = _ir(
        layer_intents=[_present_layer(layer_id="pl-locked")],
        component_intents=[ComponentIntent(intent_id="ci-2", component_type="legend",
                                           component_id="comp-locked", action="patch",
                                           locked=True)],
        user_locks=locks,
    )
    report = check_obligations(ir, {})
    assert report.status == "blocked"
    codes = [f.code for f in report.blocking]
    assert codes.count("PLAN_LOCK_CONFLICT") == 2
    assert any("layer:pl-locked" == f.target for f in report.blocking)


@pytest.mark.cartography
def test_unresolved_data_ref_is_blocking_but_analysis_outputs_count():
    ir = _ir(layer_intents=[_present_layer(source_ref="ds:ghost")])
    report = check_obligations(ir, {"sources": {}, "layers": []})
    assert report.status == "blocked"
    assert any(f.code == "DATA_REF_UNRESOLVED" for f in report.blocking)
    # 运行时解析的 analysis outputs 在场即可解析（obligations 参数通道）
    report2 = check_obligations(ir, {}, analysis_output_ids=["ds:ghost"])
    assert report2.status == "ok"


@pytest.mark.cartography
def test_missing_blueprint_is_blocking():
    li = LayerIntent(intent_id="li-nobp", action="present_primary",
                     layer_id="pl-9", source_ref="ds:pop", blueprint=None)
    report = check_obligations(_ir(layer_intents=[li]),
                               {"sources": {"ds:pop": {}}, "layers": []})
    assert any(f.code == "BLUEPRINT_MISSING" for f in report.blocking)


@pytest.mark.cartography
def test_unknown_component_type_and_layer_type_block():
    ir = _ir(
        layer_intents=[_present_layer(layer_type="teleport_ray")],
        component_intents=[ComponentIntent(intent_id="ci-9", component_type="holodeck",
                                           component_id="comp-h", required=True)],
    )
    report = check_obligations(ir, {"sources": {"ds:pop": {}}, "layers": []})
    codes = {f.code for f in report.blocking}
    assert "LAYER_TYPE_UNSUPPORTED" in codes
    assert "COMPONENT_UNKNOWN_TYPE" in codes


@pytest.mark.cartography
def test_unsupported_export_blocks_and_supported_passes():
    base = dict(layer_intents=[_present_layer()])
    ir_bad = _ir(exports=[ExportObligation(fmt="hologram")], **base)
    assert any(f.code == "EXPORT_UNSUPPORTED" for f in
               check_obligations(ir_bad, {"sources": {"ds:pop": {}}}).blocking)
    for fmt in ("png", "pdf", "svg", "csv", "geojson"):
        ir_ok = _ir(exports=[ExportObligation(fmt=fmt)], **base)
        assert check_obligations(ir_ok, {"sources": {"ds:pop": {}}}).status == "ok"


@pytest.mark.cartography
def test_scale_crs_advisory_warns_without_blocking():
    ir = _ir(
        layer_intents=[_present_layer()],
        datasets=[DatasetRef(dataset_id="ds:pop", crs="EPSG:4326")],
    )
    report = check_obligations(ir, {"sources": {"ds:pop": {}}})
    # V1 无打印义务 → 不提示；置打印 output_purpose 后 advisory 出现但非阻塞
    assert report.status == "ok"
    from app.lib.cartography.plan_ir import LayoutObligation
    ir_print = ir.model_copy(update={"layout": LayoutObligation(output_purpose="print_a4")})
    report2 = check_obligations(ir_print, {"sources": {"ds:pop": {}}})
    assert report2.status == "ok"
    assert any(f.code == "SCALE_CRS_ADVISORY" and f.severity == "warning"
               for f in report2.findings)


@pytest.mark.cartography
def test_findings_sorted_deterministically():
    ir = _ir(
        layer_intents=[_present_layer(layer_id="pl-b", source_ref="ds:ghost1"),
                       _present_layer(layer_id="pl-a", source_ref="ds:ghost2")],
        exports=[ExportObligation(fmt="hologram")],
    )
    r1 = check_obligations(ir, {"sources": {}, "layers": []})
    r2 = check_obligations(ir, {"sources": {}, "layers": []})
    assert r1 == r2
    keys = [f.sort_key() for f in r1.findings]
    assert keys == sorted(keys)


@pytest.mark.cartography
def test_present_new_layer_without_ref_and_absent_target_blocks():
    """review P1-2：编译器铸造的 layer_id 不能当数据绑定证据 ——
    新建层意图无 source_ref 且目标不在场 ⇒ blocked（拒绝无数据层）。"""
    ir = _ir(layer_intents=[_present_layer(layer_id="pl-fresh", source_ref="")])
    report = check_obligations(ir, {"sources": {"ds:pop": {}}, "layers": []})
    assert report.status == "blocked"
    assert any(f.code == "DATA_REF_UNRESOLVED" for f in report.blocking)


@pytest.mark.cartography
def test_present_existing_target_without_ref_is_allowed():
    """目标层已在意（就地修正语义）时无 ref 合法 —— 不回退为整层重建。"""
    ir = _ir(layer_intents=[_present_layer(layer_id="pl-1", source_ref="")])
    current = {"sources": {"ds:pop": {}},
               "layers": [{"id": "pl-1", "type": "fill"}]}
    assert check_obligations(ir, current).status == "ok"
