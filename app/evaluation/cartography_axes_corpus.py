"""Cartography Axes Benchmark Corpus（V2；GOAL 里程碑 8/10）。

制图质量轴语料（全部确定性纯函数，零 LLM / 零渲染器）：

- **layout 轴**：``render_observation.derive_component_layout_findings``
  对组件观测投影做重叠/越界检查（fixtures 提供确定性组件 rect，
  把 runner 内联探针的形态正式化为语料案例）；
- **CVD 轴**：``palettes.simulate_cvd + min_adjacent_delta_e`` —— 色带
  在色觉缺陷模拟下的相邻可辨性（ΔE00，阈值 10 = 公认可辨惯例）；
- **template/codegen 轴**：``template_codegen_evaluator`` 对 MapSpec
  fixture 的组件完备性（图例/标题/指北针/比例尺/Attribution）；
- **诚实轴**：无 VLM 证据时 unified feedback 的 visual 轴必须是
  ``not_evaluated``（离线诚实性红线）。

CVD 期望值为 2026-09-16 对照 faa453a8 的手工审定真值（探针运行 +
ΔE00 惯例推导）。**已知发现**：默认 YlOrRd 在 k=5 时 deuteranopia
min ΔE=8.99 < 10 —— 语料以 known-unsafe 锚定（检测器必须给出 finding），
换 palette / k 的硬化工单落地后应有意识翻新。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.evaluation.runner import CaseResult


#: CVD 相邻可辨阈值（CIEDE2000；ΔE ≥ 10 = 清晰可辨的通行惯例）。
CVD_MIN_DELTA_E = 10.0


@dataclass(frozen=True)
class CartographyAxesCase:
    """一个制图轴案例（axis 词表闭合：layout / cvd / template / honesty）。"""

    case_id: str
    name: str
    axis: str  # layout | cvd | template | honesty
    description: str = ""
    # layout 轴：观测 fixture 名（fixtures.py render_observation_*）
    observation_fixture: str = ""
    expected_finding_targets: Tuple[str, ...] = ()  # 排序后期望被点名的组件
    # cvd 轴
    palette: str = ""
    k: int = 5
    cvd_kinds: Tuple[str, ...] = ()
    expected_cvd_safe: bool = True
    # template 轴：mapspec fixture 开关 + required 槽位缺席锚
    # （composition.standard_analysis：title/north_arrow/scale_bar/attribution
    # 为 required；legend 为 conditional，缺席不报错 —— 审定语义）。
    mapspec_legend: Optional[bool] = None
    mapspec_scale_bar: bool = True
    expected_required_missing: Tuple[str, ...] = ()
    # honesty 轴：空证据 → visual not_evaluated
    tags: Tuple[str, ...] = ()


def build_cartography_axes_corpus() -> List[CartographyAxesCase]:
    """制图轴语料（按 case_id 排序 + 构建期守卫）。"""
    cases = [
        CartographyAxesCase(
            case_id="CARTX-cvd-default-ylorrd-deuteranopia",
            name="默认 YlOrRd k=5 在 deuteranopia 下不可辨（known-unsafe）",
            axis="cvd", palette="YlOrRd", k=5,
            cvd_kinds=("cvd_deuteranopia",),
            expected_cvd_safe=False,
            tags=("cartography", "cvd", "known-unsafe"),
            description="审定：min ΔE=8.99 < 10 —— 默认色带的可访问性缺口"
                        "（检测器必须给出 finding；硬化后翻新本行）",
        ),
        CartographyAxesCase(
            case_id="CARTX-cvd-redgreen-detector",
            name="红绿相邻对 deuteranopia 检出（protanopia 被明度拯救）",
            axis="cvd", palette="redgreen-contrast-pair", k=2,
            cvd_kinds=("cvd_deuteranopia", "cvd_protanopia"),
            expected_cvd_safe=False,
            tags=("cartography", "cvd", "detector"),
            description="审定：红绿对 deuteranopia ΔE=9.76 < 10 → 必须检出；"
                        "protanopia ΔE=28.6（明度轴）≥ 10 —— 明度差是合法"
                        "救赎路径，非对称性为审定语义",
        ),
        CartographyAxesCase(
            case_id="CARTX-cvd-viridis-safe",
            name="Viridis k=5 全色觉类型可辨（CVD-safe 锚）",
            axis="cvd", palette="Viridis", k=5,
            cvd_kinds=("cvd_protanopia", "cvd_deuteranopia", "cvd_tritanopia"),
            expected_cvd_safe=True,
            tags=("cartography", "cvd", "safe-anchor"),
            description="审定：min ΔE ∈ [13.4, 15.0] ≥ 10 —— 三类色觉模拟下"
                        "全部相邻可辨",
        ),
        CartographyAxesCase(
            case_id="CARTX-honesty-visual-not-evaluated",
            name="无 VLM 证据 → visual 轴诚实 not_evaluated",
            axis="honesty",
            tags=("cartography", "honesty"),
            description="离线红线：没有视觉判断证据时 visual 轴不得虚构 pass",
        ),
        CartographyAxesCase(
            case_id="CARTX-layout-clean",
            name="无冲突布局 → 零 finding",
            axis="layout", observation_fixture="clean",
            expected_finding_targets=(),
            tags=("cartography", "layout", "clean-anchor"),
            description="title/legend/scale_bar 各据一角 → 无布局冲突",
        ),
        CartographyAxesCase(
            case_id="CARTX-layout-overlap-detected",
            name="legend/title 重叠 → 对称 finding 对",
            axis="layout", observation_fixture="overlap",
            expected_finding_targets=("legend", "title"),
            tags=("cartography", "layout"),
            description="交集 > 1px² → 每对两条对称 warning（各执一端）",
        ),
        CartographyAxesCase(
            case_id="CARTX-layout-offscreen-detected",
            name="组件完全越界 → offscreen finding",
            axis="layout", observation_fixture="offscreen",
            expected_finding_targets=("legend",),
            tags=("cartography", "layout"),
            description="mounted 但完全在画布外 → 事实披露（warning）",
        ),
        CartographyAxesCase(
            case_id="CARTX-template-required-missing",
            name="required 槽位缺席（scale_bar）→ 组合 violation",
            axis="template", mapspec_legend=True, mapspec_scale_bar=False,
            expected_required_missing=("scale_bar",),
            tags=("cartography", "template", "required-slot"),
            description="composition.standard_analysis 下 scale_bar 缺席 → "
                        "required_slot_missing（标题/指北针/比例尺/归属为"
                        " required 审定锚；legend 为 conditional）",
        ),
        CartographyAxesCase(
            case_id="CARTX-template-standard-complete",
            name="required 槽位齐备 → 组合通过",
            axis="template", mapspec_legend=True, mapspec_scale_bar=True,
            expected_required_missing=(),
            tags=("cartography", "template", "clean-anchor"),
            description="title/north_arrow/scale_bar/attribution(+legend) 齐备 "
                        "→ 组合校验零 required violation",
        ),
    ]
    cases.sort(key=lambda c: c.case_id)
    # 构建期守卫：id 唯一 / axis 词表闭合 / 轴覆盖完整
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids)), f"duplicate cartography case ids: {ids}"
    axes = {c.axis for c in cases}
    assert axes == {"layout", "cvd", "template", "honesty"}, (
        f"cartography axis coverage hole: {axes}"
    )
    return cases


# ── 驱动器（纯函数；产出 CaseResult 与 report/manifest 同币种）──────────


def _run_layout(case: CartographyAxesCase, fixtures: Dict[str, Callable]) -> List[str]:
    from app.services.gis_harness.render_observation import (
        derive_component_layout_findings,
    )

    builder = fixtures.get(f"render_observation_{case.observation_fixture}")
    assert builder is not None, f"unknown observation fixture {case.observation_fixture}"
    findings = derive_component_layout_findings(builder())
    targets = sorted({str(f.target) for f in findings})
    want = sorted(case.expected_finding_targets)
    failures: List[str] = []
    if targets != want:
        failures.append(
            f"layout findings: expected targets {want}, got {targets} "
            f"(details={[f.detail for f in findings][:4]})"
        )
    return failures


def _run_cvd(case: CartographyAxesCase) -> List[str]:
    from app.lib.cartography.palettes import (
        min_adjacent_delta_e,
        sample_ramp_colors,
        simulate_cvd,
    )

    if case.palette == "redgreen-contrast-pair":
        colors = ["#e41a1c", "#4daf4a"]  # 审定的红绿对抗对
    else:
        colors = sample_ramp_colors(case.palette, case.k)
        if not colors:
            return [f"cvd: palette {case.palette!r} produced no colors"]
    failures: List[str] = []
    per_kind: Dict[str, Any] = {}
    safe_everywhere = True
    for kind in case.cvd_kinds:
        sim = [simulate_cvd(c, kind) for c in colors]
        if any(c is None for c in sim):
            failures.append(f"cvd {kind}: simulate_cvd returned None (fail-closed)")
            continue
        delta = min_adjacent_delta_e([c for c in sim if c])
        per_kind[kind] = delta
        if delta is None:
            failures.append(f"cvd {kind}: min delta-E None (unresolvable color)")
            continue
        kind_safe = delta >= CVD_MIN_DELTA_E
        # 每个色觉类型独立判定：任一类型不可辨 → 该案例整体不安全
        if not kind_safe:
            safe_everywhere = False
    if case.expected_cvd_safe and not safe_everywhere:
        failures.append(
            f"cvd: palette {case.palette!r} expected safe for all kinds "
            f"{list(case.cvd_kinds)}, but a kind fell below ΔE "
            f"{CVD_MIN_DELTA_E} (per_kind={per_kind})"
        )
    if not case.expected_cvd_safe and safe_everywhere:
        failures.append(
            f"cvd: known-unsafe expectation not reproduced for "
            f"{case.palette!r} (per_kind={per_kind}) —— 检测器失效或真值翻新"
        )
    return failures


def _run_template(case: CartographyAxesCase) -> List[str]:
    from app.evaluation.fixtures import mapspec_with_legend
    from app.lib.harness.template_codegen_evaluator import (
        CHECK_COMPOSITION,
        evaluate_template_codegen,
    )

    assert case.mapspec_legend is not None
    report = evaluate_template_codegen(
        mapspec_with_legend(
            legend=case.mapspec_legend, scale_bar=case.mapspec_scale_bar,
        ),
        composition_template_id="composition.standard_analysis",
    )
    failures: List[str] = []
    # required_slot_missing finding：detail=违规码，message 含 slot id
    missing_messages = [
        str(getattr(f, "message", ""))
        for f in (report.findings or [])
        if getattr(f, "detail", "") == "required_slot_missing"
    ]
    want = sorted(case.expected_required_missing)
    got = sorted(s for s in want if any(s in m for m in missing_messages))
    extra_missing = [
        m for m in missing_messages
        if not any(s in m for s in want)
    ]
    if got != want or extra_missing:
        failures.append(
            f"template: expected required-slot-missing {want}, resolved {got}, "
            f"unexpected={extra_missing} "
            f"(composition check={report.checks.get(CHECK_COMPOSITION)})"
        )
    return failures


def _run_honesty() -> List[str]:
    from app.lib.harness.cartography_feedback import build_unified_feedback

    feedback = build_unified_feedback({})
    axis = feedback.to_dict()["axes"]["visual"]
    failures: List[str] = []
    if axis["evaluated"] or axis["status"] != "not_evaluated":
        failures.append(
            f"honesty: visual axis must be not_evaluated without evidence, "
            f"got {axis}"
        )
    return failures


def run_cartography_axes_case(case: CartographyAxesCase) -> CaseResult:
    """确定性执行一个制图轴案例。"""
    from app.evaluation import fixtures as fixture_mod

    result = CaseResult(
        case_id=case.case_id, group="cartography-axes", name=case.name,
    )
    failures: List[str] = []
    metrics: Dict[str, Any] = {}
    try:
        if case.axis == "layout":
            fixtures = {
                name: getattr(fixture_mod, name)
                for name in dir(fixture_mod)
                if name.startswith("render_observation_")
            }
            failures = _run_layout(case, fixtures)
        elif case.axis == "cvd":
            failures = _run_cvd(case)
        elif case.axis == "template":
            failures = _run_template(case)
        elif case.axis == "honesty":
            failures = _run_honesty()
        else:  # pragma: no cover — 构建期守卫已闭合
            failures = [f"unknown axis {case.axis}"]
    except Exception as exc:  # noqa: BLE001 — fail-closed 诚实化
        failures.append(f"driver error: {type(exc).__name__}: {exc}")
    metrics["cartography_axes_ok"] = not failures
    result.metrics = metrics
    result.failures = failures
    result.passed = not failures
    result.status = "pass" if result.passed else "fail"
    return result
