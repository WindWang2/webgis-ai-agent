"""H02 — 他属 issue 回归契约（#1726 item 2 / #1713 item 1）。

xfail（非 strict）契约把已知缺陷的**期望行为**固化为可执行文档：归属
修复 PR 落地后 XPASS 提醒移除标记。H02 不动其实现文件（避免争抢）。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.cartography


@pytest.mark.xfail(
    reason="#1726 item 2（Python 孪生归属）：mapspec_to_svg._resolve_paint_value "
    "对 MapLibre 表达式数组原样返回不求值（:391-397 if not isinstance(val, "
    "dict): return val）—— 同一份 live 合成 spec 两个导出路径产出不同几何",
    strict=False,
)
def test_python_twin_evaluates_case_expression():
    from app.services.mapspec_to_svg import _resolve_paint_value

    expr = ["case", ["==", ["get", "x"], None], "#aaaaaa", "#bbbbbb"]
    assert _resolve_paint_value(expr, {"x": None}) == "#aaaaaa"
    assert _resolve_paint_value(expr, {"x": 1}) == "#bbbbbb"


@pytest.mark.xfail(
    reason="#1713 item 1（视觉观测归属）：_eval_vlm/_eval_hybrid 整体跳过 "
    "C13 revision/指纹硬门与会话绑定（仅 _eval_rules 实现）—— 旧像素可被 "
    "归因到新 spec 产生跨代误修复",
    strict=False,
)
def test_vlm_mode_enforces_c13_revision_gate(monkeypatch):
    from app.services.gis_harness.visual_observation import provider as vp
    from app.services.gis_harness.visual_observation.contracts import (
        VisualObservationInput,
        VisualScreenshotRef,
    )

    called = {"vlm": 0}

    def _spy(obs):
        called["vlm"] += 1
        return vp.VisualObservationResult.not_evaluated(
            "spy", provider=vp.MODE_VLM)

    monkeypatch.setattr(vp, "_eval_vlm", _spy)
    monkeypatch.setattr(vp, "provider_mode", lambda: vp.MODE_VLM)

    obs = VisualObservationInput(
        trigger="post_repair",
        session_id="sess-x",
        mapspec_revision=9,
        mapspec_fingerprint="carto-sha256:current",
        screenshot=VisualScreenshotRef(
            ref="ref:screenshot:old", sha256="deadbeef",
            mapspec_revision=7,  # 旧代截图
            mapspec_fingerprint="carto-sha256:stale",
        ),
    )
    result = vp.evaluate_observation(obs)
    assert called["vlm"] == 0, "C13 门必须先于模式分发拒绝旧代截图"
    assert result.status == "not_evaluated"
    assert "mismatch" in str(result.reason)


@pytest.mark.xfail(
    reason="#1713 item 1（视觉观测归属）：vlm/hybrid 无会话绑定（跨会话猜 "
    "ref 在解析面不拒绝）",
    strict=False,
)
def test_vlm_mode_enforces_session_binding(monkeypatch):
    from app.services.gis_harness.visual_observation import provider as vp
    from app.services.gis_harness.visual_observation.contracts import (
        VisualObservationInput,
        VisualScreenshotRef,
    )

    called = {"vlm": 0}

    def _spy(obs):
        called["vlm"] += 1
        return vp.VisualObservationResult.not_evaluated(
            "spy", provider=vp.MODE_VLM)

    monkeypatch.setattr(vp, "_eval_vlm", _spy)
    monkeypatch.setattr(vp, "provider_mode", lambda: vp.MODE_VLM)

    obs = VisualObservationInput(
        trigger="post_repair",
        session_id="sess-a",
        mapspec_revision=5,
        screenshot=VisualScreenshotRef(  # 同代但属其它会话
            ref="ref:screenshot:other-session", sha256="cafebabe",
            mapspec_revision=5,
        ),
    )
    result = vp.evaluate_observation(obs)
    assert called["vlm"] == 0, "会话绑定必须先于模式分发拒绝跨会话截图"
    assert result.status == "not_evaluated"
    assert "unresolvable" in str(result.reason) or "mismatch" in str(result.reason)
