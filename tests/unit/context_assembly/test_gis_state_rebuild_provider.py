"""ADR-0288（W09）：GIS 状态重建块 —— compaction/重启后的权威事实投影。

测试使用**真实** SessionPlan / MapSpec 形状（review-P1 修复锚点）：
- visibility 在 ``layer.layout.visibility = "visible" | "none"``；
- 待完成能力在 ``SessionPlan.progress``（pending/voided/failed）；
- 证据 refs 在 ``PlanStep.evidence[].ref``。
"""
from __future__ import annotations

import asyncio

from app.services.context_assembly.contract import ContextDomain
from app.services.context_assembly.providers import (
    GisStateRebuildProvider,
    build_default_providers,
    render_gis_state_rebuild_block,
)
from app.services.session_plan import CapabilityProgress, SessionPlan


class _Req:
    session_id = "sess-rebuild"


class _Facts:
    def __init__(self, mapspec, plan=None):
        self.mapspec = mapspec
        self.plan = plan
        self.source_session_id = "sess-rebuild"


def _real_plan(capabilities=(), steps=()):
    return SessionPlan(
        envelope_id="env-rebuild",
        session_id="sess-rebuild",
        user_goal="分布图",
        progress=[
            CapabilityProgress(capability=c, status="pending") for c in capabilities
        ],
        steps=list(steps),
    )


def test_rebuild_block_projects_authoritative_state():
    maps = {
        "revision": "rev-7",
        "layers": [
            {"id": "poi-layer", "layout": {"visibility": "visible"}},
            # canonical hidden: layout.visibility = "none"（review-P1 关键形状）
            {"id": "hidden-heatmap", "layout": {"visibility": "none"}},
        ],
    }
    plan = _real_plan(("capability_x",))
    text = render_gis_state_rebuild_block(maps, plan)
    assert "rev-7" in text
    assert "可见图层: poi-layer" in text
    # 隐藏状态是用户约束 —— 重建后必须保留（A11）；canonical layout.visibility=none
    assert "隐藏图层（保持隐藏）: hidden-heatmap" in text
    assert "capability_x" in text


def test_rebuild_block_hidden_layer_not_misreported_visible():
    """review-P1 核心回归：真实隐藏层绝不可被投成可见（防止模型重新显示）。"""
    maps = {"layers": [{"id": "hidden-real", "layout": {"visibility": "none"}}]}
    text = render_gis_state_rebuild_block(maps, None)
    assert "可见图层: hidden-real" not in text
    assert "隐藏图层（保持隐藏）: hidden-real" in text


def test_rebuild_block_projects_step_evidence_refs():
    from app.services.harness_kernel.models import PlanStep, StepEvidence

    step = PlanStep(
        id="step-1",
        capability="capability_x",
        evidence=[StepEvidence(tool="nearest_facility", ref="ref://r1")],
    )
    maps = {"layers": [{"id": "a-layer", "layout": {"visibility": "visible"}}]}
    text = render_gis_state_rebuild_block(maps, _real_plan(steps=[step]))
    assert "ref://r1" in text


def test_rebuild_block_bounded_and_honest():
    # 有界：>24 层时通用目录截断披露（G22-P2 语义：仅通用可见列表限额，
    # 隐藏/锁定/标注用户约束改为全量提取——A13 的第 25 层手动隐藏不再消失）；
    # >12 每类遗漏披露；空 mapspec 形状不虚构
    maps = {
        "revision": "r",
        "layers": [
            {"id": f"l{i}", "layout": {"visibility": "visible"}} for i in range(40)
        ],
    }
    text = render_gis_state_rebuild_block(maps, None)
    assert "图层目录截断" in text
    assert "另 28 层未逐一列出" in text  # 40 visible, only 12 listed
    assert render_gis_state_rebuild_block({}, None) == ""
    assert render_gis_state_rebuild_block(None, None) == ""


def test_provider_skips_without_mapspec_and_registered():
    provider = GisStateRebuildProvider()
    report = asyncio.run(provider.collect(_Req(), _Facts(None, None)))
    assert report.skipped_reason == "disabled"
    assert provider.domain is ContextDomain.GIS_STATE_REBUILD
    assert any(
        isinstance(p, GisStateRebuildProvider) for p in build_default_providers()
    )


# ── G22-P2：权威 GIS 状态恢复目录（A13–A16/A18）────────────────────────────


def test_g22_a13_hidden_constraint_beyond_scan_cap_survives():
    """A13：>24 层时第 25 层（及更后）的手动隐藏约束仍可恢复——用户约束
    全量提取，不因通用目录的扫描/列数限额而消失。"""
    layers = [
        {"id": f"l{i}", "layout": {"visibility": "visible"}} for i in range(24)
    ]
    layers += [
        {"id": "l24-manual-hide", "layout": {"visibility": "none"}},
        {"id": "l25-manual-hide", "layout": {"visibility": "none"}},
    ]
    text = render_gis_state_rebuild_block({"revision": "r", "layers": layers}, None)
    assert "l24-manual-hide" in text
    assert "l25-manual-hide" in text
    assert "保持隐藏" in text


def test_g22_a14_label_authoring_overrides_projected():
    """A14：G16 标注人工状态（pin/offset/leader/text 覆写）不丢。

    权威形状 = **list**（mutation_handlers 持久化
    ``[{"featureId": …, …}]``，sort_overrides_stable 按 featureId 序）；
    review-P1 修复锚点：旧断言用自发明的 dict 形状（真实数据恒空投影）。
    """
    layer = {
        "id": "schools",
        "label": {"authoring": {"overrides": [
            {"featureId": "f1", "pinned": True, "offset": [4, -2]},
            {"featureId": "f2", "text": "城东小学", "callout": True},
        ]}},
    }
    text = render_gis_state_rebuild_block(
        {"revision": "r", "layers": [layer]}, None)
    assert "标注人工覆写 2 条" in text
    assert "schools:f1(" in text and "pinned" in text
    assert "城东小学" not in text  # 代表项只投影键，不外泄改文内容？——
    # ↑ 撤销：文本覆写值本就是用户事实，投影键即可；不额外断言。


def test_g22_a14_dict_shape_tolerated_not_authoritative():
    """dict 形状仅读容错（历史/夹具）；不作为权威形状断言依据。"""
    layer = {
        "id": "legacy",
        "label": {"authoring": {"overrides": {
            "f9": {"pinned": True},
        }}},
    }
    text = render_gis_state_rebuild_block(
        {"revision": "r", "layers": [layer]}, None)
    assert "标注人工覆写 1 条" in text


def test_g22_a15_active_frame_projected():
    """A15：G15 frame_product 的 active frame 正确投影。"""
    maps = {
        "revision": "r",
        "layers": [{"id": "l1"}],
        "frame_product": {
            "product_kind": "small_multiples",
            "enabled": True,
            "active_frame": "frame-b",
            "frames": [
                {"frame_id": "frame-a", "title": "第一幅"},
                {"frame_id": "frame-b", "title": "第二幅"},
            ],
        },
    }
    text = render_gis_state_rebuild_block(maps, None)
    assert "active_frame=frame-b" in text
    assert "frame-b:第二幅" in text


def test_g22_a16_invalid_ref_disclosed_no_ghost_semantics():
    """A16：数据 ref 删除时投影披露失效（不把幽灵图层当有效数据）。"""
    maps = {
        "revision": "r",
        "layers": [{"id": "ghosty"}],
        "sources": {"src-1": {"refId": "data-gone"}},
    }
    text = render_gis_state_rebuild_block(
        maps, None, invalid_source_refs=[{"source": "src-1", "ref": "data-gone"}])
    assert "失效数据引用" in text
    assert "src-1→data-gone" in text
    assert "不重建幽灵图层" in text


def test_g22_a18_restored_job_status_honest():
    """A18：恢复的 queued/running job 状态如实，绝不显示为完成。"""
    text = render_gis_state_rebuild_block(
        {"revision": "r", "layers": [{"id": "l1"}]}, None,
        durable_jobs=[{"id": "job-77", "status": "queued"},
                      {"id": "job-78", "status": "running"}])
    assert "job-77=queued" in text
    assert "job-78=running" in text
    job_line = text.split("非终态任务")[1].split("\n")[0]
    # 状态值本身绝不得是终态（completed/succeeded/…）
    assert "=completed" not in job_line and "=succeeded" not in job_line


def test_g22_constraints_survive_budget_pressure_realistic_ids():
    """预算挤压（review-P1 修复锚点：真实 uuid 级 id + 超界约束量）：
    段内预算覆盖**全部**行（约束/恢复/通用），任何类别不因域级 1600 硬截断
    静默消失；压缩必须伴随披露行。"""
    def _uid(prefix, i):
        return f"{prefix}-{i:04d}-4f2a9b8c1d3e{ i % 97:02x}"

    layers = [
        {"id": _uid("layer", i), "layout": {"visibility": "visible"},
         "label": {"authoring": {"overrides": [
             {"featureId": f"feat-{i}", "pinned": True},
             {"featureId": f"feat-b-{i}", "offset": [2, -1]},
         ]}}}
        for i in range(26)
    ]
    # 22 层隐藏（含第 25+ 层——超通用扫描界）；16 个层锁；8 组件锁。
    for layer in layers[4:26]:
        layer["layout"] = {"visibility": "none"}
    locked = [_uid("lock", i) for i in range(16)]
    locked_components = [_uid("comp", i) for i in range(8)]
    maps = {
        "revision": "rev-2f9c41ab",
        "layers": layers,
        "workbench": {"lockedLayerIds": locked, "lockedComponentIds": locked_components},
        "frame_product": {
            "product_kind": "small_multiples", "enabled": True,
            "active_frame": _uid("frame", 1),
            "frames": [{"frame_id": _uid("frame", i), "title": f"第{i}幅"}
                       for i in range(12)],
        },
    }
    text = render_gis_state_rebuild_block(
        maps, None,
        durable_jobs=[{"id": _uid("job", i), "status": "queued"} for i in range(4)],
        invalid_source_refs=[{"source": _uid("src", i), "ref": _uid("ref", i)}
                             for i in range(3)])

    assert len(text) <= 1600, f"块长 {len(text)} 超域 cap——硬截断风险"
    # 每个约束/恢复**类别**必须在场（可压缩，不可消失）；断言用跨压缩级别
    # 的类别不变量，不绑定具体压缩措辞。
    assert "用户锁定" in text and "layer_locked" in text
    assert "lock-0000" in text  # 锁 id 全量档（预算足够）
    assert "隐藏图层" in text and "另 2 层隐藏" in text
    assert "layer-0023" in text  # 第 24 个隐藏层 id 仍可寻址
    assert "标注人工覆写 52 条" in text
    assert "多画幅产品" in text and "active_frame=" in text and "共 12 幅" in text
    assert "非终态任务" in text and "4" in text
    assert "失效数据引用" in text and "3 处" in text
    # 压缩/截断必须披露（模型必须知道有省略）；披露行是最后一行
    # （域级硬截断没有切掉块尾）
    assert "目录压缩/截断披露" in text
    assert text.rstrip().endswith("计数已如实给出）")


def test_g22_no_fabrication_of_selection_or_absent_facts():
    """诚实边界：无持久权威源的字段（selection 等）不发明；facts 缺席
    （durable_jobs/invalid refs = None）不产出对应行。"""
    text = render_gis_state_rebuild_block(
        {"revision": "r", "layers": [{"id": "l1"}]}, None,
        durable_jobs=None, invalid_source_refs=None)
    assert "非终态任务" not in text
    assert "失效数据引用" not in text
    assert "selection" not in text.lower() or "不发明" in text
