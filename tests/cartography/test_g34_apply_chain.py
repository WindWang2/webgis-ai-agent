"""G34 写链路回归（apply / CAS / 锁不变量）。

独立评审 P0 的直接回归。此前 ``test_g34_print_layout_authoring.py`` 的
27 条 oracle 无一条触达 ``service.apply``，于是三个致命缺陷全部漏过：

1. ``apply(initialize=True)`` 复用会重读 store 的 ``propose``，首建必然
   ``PRINT_LAYOUT_NOT_CONFIGURED``；
2. ``source.mapspec_revision`` 双向绑定 → 任何别的 mutation 之后永久
   ``SUPERSEDED``（作者计划再也改不动）；
3. ``replace_plan`` / ``apply_template`` 可静默删除锁组件（数据丢失）。

本模块只覆盖**写**：一次 CAS、锁不变量、漂移重绑、物理尺寸注入。
产物几何 oracle 仍在 ``test_g34_print_layout_authoring.py``。
"""
from __future__ import annotations

import copy
import uuid

import pytest

from app.lib.cartography.print_layout_schema import empty_print_layout
from app.services.mapspec_store import mapspec_store
from app.services.print_layout_authoring import operations, service


async def _seed(sid: str) -> int:
    await mapspec_store.init_project(
        sid, view={"center": [120.0, 30.0], "zoom": 6.0})
    return int((await service.inspect(sid)).get("mapspec_revision") or 0)


async def test_t27_apply_initialize_commits_first_plan():
    """首建路径：initialize=True + 一次 CAS 必须真的落盘。"""
    sid = "g34-t27-" + uuid.uuid4().hex[:8]
    rev = await _seed(sid)
    out = await service.apply(sid, {
        "initialize": True,
        "operations": [
            {"op": "set_paper", "profile": "a4_landscape"},
            {"op": "set_frame", "component_ids": ["title"],
             "frame": {"x": 15.0, "y": 15.0, "width": 180.0, "height": 18.0}},
        ],
    }, expected_revision=rev)
    assert out["committed"] is True, out
    assert out["mutation_revision"] == rev + 1

    plan = (await mapspec_store.get_mapspec(sid))["layout"]["print_layout"]
    assert plan["version"] == "print_layout.v1"
    title = next(c for c in plan["components"] if c["id"] == "title")
    assert title["frame"]["y"] == 15.0
    assert plan["source"]["mapspec_revision"] == rev


async def test_t28_foreign_mutation_rebinds_instead_of_deadlock():
    """别的 mutation 推进 revision 后，作者仍能继续编辑（只报漂移）。"""
    sid = "g34-t28-" + uuid.uuid4().hex[:8]
    rev = await _seed(sid)
    first = await service.apply(sid, {
        "initialize": True,
        "operations": [{"op": "set_frame", "component_ids": ["title"],
                        "frame": {"x": 12.0, "y": 12.0,
                                  "width": 180.0, "height": 18.0}}],
    }, expected_revision=rev)
    assert first["committed"] is True, first

    await mapspec_store.set_view(sid, center=[121.0, 31.0], zoom=7.0)
    rev2 = int((await service.inspect(sid))["mapspec_revision"])
    assert rev2 > first["mutation_revision"]

    second = await service.apply(sid, {
        "operations": [{"op": "set_frame", "component_ids": ["title"],
                        "frame": {"x": 18.0, "y": 18.0,
                                  "width": 180.0, "height": 18.0}}],
    }, expected_revision=rev2)
    assert second["committed"] is True, second
    plan = (await mapspec_store.get_mapspec(sid))["layout"]["print_layout"]
    assert plan["source"]["mapspec_revision"] == rev2, "drift must rebind"

    # 真正的 CAS 冲突仍然拒绝（旧 expected_revision）
    stale = await service.apply(sid, {
        "operations": [{"op": "set_frame", "component_ids": ["title"],
                        "frame": {"x": 19.0, "y": 19.0,
                                  "width": 180.0, "height": 18.0}}],
    }, expected_revision=rev2)
    assert stale["success"] is False and stale["code"] == "SUPERSEDED", stale


async def test_t29_locked_component_cannot_be_deleted_or_retargeted():
    """锁组件不可被 replace_plan 删除，也不可被 ``__`` 族后缀绕过。"""
    sid = "g34-t29-" + uuid.uuid4().hex[:8]
    rev = await _seed(sid)
    await service.apply(sid, {"initialize": True, "operations": []},
                        expected_revision=rev)
    spec = copy.deepcopy(await mapspec_store.get_mapspec(sid))
    spec.setdefault("workbench", {})["lockedComponentIds"] = ["scale"]
    await mapspec_store.save_mapspec(sid, spec)
    rev2 = int((await service.inspect(sid))["mapspec_revision"])
    plan = (await mapspec_store.get_mapspec(sid))["layout"]["print_layout"]

    kept = [c for c in plan["components"] if c["id"] != "scale"]
    dropped = await service.apply(sid, {
        "operations": [{"op": "replace_plan",
                        "plan": {**empty_print_layout(), "components": kept}}],
    }, expected_revision=rev2)
    assert dropped["success"] is False, dropped
    assert dropped["code"] in ("PRINT_LAYOUT_LOCKED_COMPONENT",
                               "PRINT_LAYOUT_BLOCKED")

    # 族后缀：locks=["scale"] 必须命中 "scale__v2"
    fam = operations.apply_operations(
        {"version": "print_layout.v1",
         "components": [{"id": "scale__v2", "kind": "scale_bar",
                         "frame": {"x": 20.0, "y": 150.0,
                                   "width": 60.0, "height": 12.0}}]},
        [{"op": "set_frame", "component_ids": ["scale__v2"],
          "frame": {"x": 0.0, "y": 0.0, "width": 60.0, "height": 12.0}}],
        ["scale"])
    assert fam["ok"] is False
    assert fam["issues"][0]["code"] == "PRINT_LAYOUT_LOCKED_COMPONENT"

    # 锁组件的图例同样被冻结
    mutated = await service.apply(sid, {
        "operations": [{"op": "set_legend", "component_ids": ["scale"],
                        "legend": {"columns": 3}}],
    }, expected_revision=rev2)
    assert mutated["success"] is False, mutated
    still = (await mapspec_store.get_mapspec(sid))["layout"]["print_layout"]
    scale = next(c for c in still["components"] if c["id"] == "scale")
    assert (scale.get("legend") or {}).get("columns") != 3, "zero write on reject"


async def test_t30_apply_template_cannot_wipe_locked_components():
    """模板整份替换也不得吃掉锁组件（数据丢失护栏）。"""
    sid = "g34-t30-" + uuid.uuid4().hex[:8]
    rev = await _seed(sid)
    await service.apply(sid, {"initialize": True, "operations": []},
                        expected_revision=rev)
    spec = copy.deepcopy(await mapspec_store.get_mapspec(sid))
    spec.setdefault("workbench", {})["lockedComponentIds"] = ["scale"]
    await mapspec_store.save_mapspec(sid, spec)
    rev2 = int((await service.inspect(sid))["mapspec_revision"])
    plan = (await mapspec_store.get_mapspec(sid))["layout"]["print_layout"]
    only_title = [c for c in plan["components"] if c["id"] == "title"]

    out = await service.apply(sid, {"operations": [
        {"op": "save_template", "template_id": "tpl:mini", "name": "Mini",
         "components": only_title},
        {"op": "apply_template", "template_id": "tpl:mini"},
    ]}, expected_revision=rev2)
    assert out["success"] is False, out
    still = (await mapspec_store.get_mapspec(sid))["layout"]["print_layout"]
    assert any(c["id"] == "scale" for c in still["components"]), \
        "locked component must survive a rejected template apply"


def test_t31_exported_svg_carries_physical_mm_size():
    """独立交付的 SVG 必须声明物理毫米尺寸（DPI 无关的打印尺寸）。"""
    svg = ('<svg role="img" width="1188" height="840" viewBox="0 0 1188 840" '
           'xmlns="http://www.w3.org/2000/svg"><g/></svg>')
    out = service._with_physical_size(svg, 297.0, 210.0)
    assert 'width="297.0000mm"' in out and 'height="210.0000mm"' in out
    assert 'viewBox="0 0 1188 840"' in out, "viewBox must stay (geometry unchanged)"
    assert 'data-g34-paper-mm="297.0000x210.0000"' in out
    assert out.count("<svg") == 1


def test_t32_page_override_cannot_move_locked_component():
    """页级覆写是改锁组件的第二条旁路，必须同样被锁不变量拦住。"""
    plan = empty_print_layout()
    plan["atlas"] = {"pages": [{"page_id": "p1", "enabled": True, "order": 0}]}
    base = copy.deepcopy(plan)
    # 给锁定组件加一条页级覆写 → 生效值改变
    moved = copy.deepcopy(plan)
    moved["atlas"]["pages"][0]["overrides"] = [{
        "component_id": "scale",
        "frame": {"x": 0.0, "y": 0.0, "width": 72.0, "height": 12.0},
    }]
    res = operations.apply_operations(
        base, [{"op": "replace_plan", "plan": moved}], ["scale"])
    assert res["ok"] is False, res
    assert res["issues"][0]["code"] == "PRINT_LAYOUT_LOCKED_COMPONENT"

    # 未被锁的组件仍可写页级覆写
    ok_res = operations.apply_operations(
        base, [{"op": "replace_plan", "plan": moved}], ["title"])
    assert ok_res["ok"] is True, ok_res


def test_t33_missing_glyph_measures_with_controlled_fallback():
    """缺字必须用兜底族的真实 advance 度量（渲染时正是它接手）。

    旧实现按 0.6em 估，CJK 兜底实际 1.0em → 换行/溢出判定低估约 40%。
    """
    from app.services.print_layout_authoring.fonts import (
        check_glyphs, measure_text_pt,
    )

    sample = "龘" * 8
    if not check_glyphs(sample, "noto-sans-sc-embedded"):
        pytest.skip("embedded subset covers these glyphs in this environment")
    via_embedded = measure_text_pt(sample, "noto-sans-sc-embedded", 10.0)
    via_fallback = measure_text_pt(sample, "noto-sans-cjk-sc", 10.0)
    assert via_fallback > 0
    assert via_embedded == pytest.approx(via_fallback, rel=1e-6), \
        "missing glyphs must borrow the fallback advance, not a 0.6em guess"
    # 明显高于旧的 0.6em 估计（= 48pt）
    assert via_embedded > 48.0
