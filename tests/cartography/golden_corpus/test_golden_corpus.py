"""Golden Corpus V3 — 结构化 golden 契约测试（ADR-0101 D8）.

- 全用例矩阵回归：digest 与 committed golden 逐字段一致；
- 属性不变量：双跑一致、无 planned 泄漏、required 槽满足、binding 零冲突；
- 刷新受 ``GOLDEN_CORPUS_UPDATE=1`` 显式控制（写盘后失败 —— 强制人工
  review 黄金差异，禁止无审查快照更新）。
"""
from __future__ import annotations

import os

import pytest

from tests.cartography.golden_corpus.corpus import (
    build_cases,
    digest_case,
    digest_sha,
    golden_path,
    load_golden,
    write_golden,
)

pytestmark = pytest.mark.cartography


def _run_case(case):
    digest = digest_case(case)
    sha = digest_sha(digest)
    return digest, sha


def test_corpus_size_is_meaningful() -> None:
    cases = build_cases()
    # V4（Design System）：500+ 结构化用例 —— 模型×模板×变体钉选×版式×
    # 布局约束×标注场景×多层绑定×边界全覆盖
    assert len(cases) >= 500, f"corpus 过小: {len(cases)}"
    from collections import Counter
    kinds = {c["kind"] for c in cases}
    assert {"base", "variants", "stress", "profiles", "layout", "labels",
            "multi", "planned-gate", "edge"} <= kinds


def test_corpus_case_ids_unique() -> None:
    cases = build_cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))


def test_corpus_digest_deterministic_double_run() -> None:
    cases = build_cases()[:20]
    for case in cases:
        first = digest_sha(digest_case(case))
        second = digest_sha(digest_case(case))
        assert first == second, f"{case['id']}: 双跑 digest 不一致"


def test_planned_models_never_receive_thematic_binding() -> None:
    """planned 模型层不得获得图例族/面板等专题组件绑定（诚实门禁）。

    通用 chrome（title/scale_bar/attribution）与模型无关，合法在场；
    门禁语义是：没有任何组件实例**绑定到 planned 模型的层**。
    """
    for case in build_cases():
        if case["kind"] != "planned-gate":
            continue
        digest = digest_case(case)
        bound = [c for c in digest["components"] if c["layerId"]]
        assert bound == [], (
            f"{case['id']}: planned 模型层被绑定 {[(c['id'], c['type']) for c in bound]}"
        )
        # 且组合模板未按该模型特化（generic 兜底才合法）
        assert digest["selection"]["composition_template_id"] in (
            "composition.minimal_interactive", "composition.standard_analysis",
            "composition.presentation_map", "composition.report_map",
        ), f"{case['id']}: planned 模型选中特化模板"


def test_no_planned_component_instances_in_any_case() -> None:
    from app.services.gis_harness.components import ComponentType
    from typing import get_args

    for case in build_cases():
        digest = digest_case(case)
        for comp in digest["components"]:
            assert comp["type"] in get_args(ComponentType), (
                f"{case['id']}: 未知组件类型 {comp['type']}"
            )


def test_pdf_cases_satisfy_required_slots() -> None:
    for case in build_cases():
        if case["kind"] in ("planned-gate", "edge"):
            continue
        digest = digest_case(case)
        if "pdf" not in case["output"]:
            continue
        assert "required_slot_missing" not in digest["validation"]["error_codes"], (
            f"{case['id']}: required 槽缺失 {digest['validation']}"
        )


def test_planned_models_never_get_keyed_templates_at_any_output() -> None:
    """R1 架构审查：planned 模型在**任意 output target** 下都不得选中
    为其特化登记的 composition 模板（resolver 记因 model_planned）。
    """
    from app.services.gis_harness.component_resolver import ComponentResolver

    planned_ids = [
        case["model"] for case in build_cases() if case["kind"] == "planned-gate"
    ]
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )

    compo_reg = get_composition_template_registry()
    for model in planned_ids:
        for output in ("interactive", "png", "pdf"):
            sel = ComponentResolver().resolve(
                map_model_id=model, output_target=output,
            )
            assert "model_planned" in sel.reason_codes, (
                f"{model}@{output}: 缺 model_planned 记因"
            )
            # generic 白名单断言（不是前缀黑名单 —— 未来新增 planned-keyed
            # pack 模板也会被此拦截）：选中模板必须不声明任何模型
            compo = compo_reg.get(sel.composition_template_id)
            assert compo is not None and not compo.compatible_map_models, (
                f"{model}@{output}: 选中非 generic 模板 {sel.composition_template_id}"
            )


def test_binding_conflicts_absent() -> None:
    for case in build_cases():
        digest = digest_case(case)
        assert "binding_conflict" not in digest["validation"]["error_codes"], (
            f"{case['id']}: binding 冲突"
        )
        assert "orphan_layer_binding" not in digest["validation"]["error_codes"], (
            f"{case['id']}: 孤儿绑定"
        )


def test_layout_solver_never_crashes_and_places_required() -> None:
    for case in build_cases():
        digest = digest_case(case)
        placed = set(digest["layout"]["placements"])
        suppressed = set(digest["layout"]["suppressed"])
        # 每个组件要么被放置要么被显式抑制 —— 不允许凭空消失
        for comp in digest["components"]:
            if comp["type"] in ("map_border", "graticule", "export_layout"):
                continue  # canvas 型不进 placements 字典（zone=none 亦计入）
            assert comp["id"] in placed | suppressed, (
                f"{case['id']}: 组件 {comp['id']} 从布局中凭空消失"
            )


@pytest.mark.parametrize("case_index", range(len(build_cases())))
def test_golden_matches(case_index: int) -> None:
    cases = build_cases()
    case = cases[case_index]
    digest, sha = _run_case(case)

    if os.environ.get("GOLDEN_CORPUS_UPDATE") == "1":
        write_golden(case["id"], {"sha": sha, "digest": digest})
        pytest.fail(
            f"golden 已刷新: {golden_path(case['id']).name} — "
            f"请 review 差异后去掉 GOLDEN_CORPUS_UPDATE 重跑"
        )

    golden = load_golden(case["id"])
    assert golden is not None, (
        f"golden 缺失: {case['id']}（以 GOLDEN_CORPUS_UPDATE=1 生成后 review 入库）"
    )
    assert golden["sha"] == sha, (
        f"{case['id']}: digest 漂移\nexpected {golden['sha']}\nactual   {sha}"
    )
    assert golden["digest"] == digest
