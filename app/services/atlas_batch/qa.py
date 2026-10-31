"""Atlas QA —— 图册级质量门（证据化；全部失败可机器复核）。

门（任何一扇不过 → 图册不交付，job failed 带逐项证据）：

1. **完整性**：计划页集合 ⊆ 渲染页集合（无缺页）、无重复页 id、渲染序 =
   计划序（目录/页码与实际页面一一对应）；
2. **页数相等**：expected（计划）== rendered（实际渲染）== PDF 实际页数
   （pypdf 计数，组装后复核——杜绝 page_count 与实际输出不符）；
3. **分类一致性**：``shared`` scope 下全部内容页 legend_digest 必须一致
   （同一绝对阈值/图例跨册复用是科学语义，不是渲染偏好）；``local`` scope
   不做一致性门，但 manifest 必须披露跨页不可比；
4. **内容页范围在场**：每内容页有 bounds（范围驱动地图，无范围 = 空白页）。

输出 :class:`AtlasQaReport`（violations 有界，code + 单行 detail）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.lib.cartography.atlas_job_spec import ClassificationScope

_MAX_VIOLATIONS = 32


class AtlasQaReport(BaseModel):
    """图册 QA 报告（进 manifest 与 job result_summary；门内累加非 frozen）。"""

    model_config = ConfigDict(validate_assignment=True)

    passed: bool = True
    page_count_expected: int = 0
    page_count_rendered: int = 0
    duplicate_page_ids: List[str] = Field(default_factory=list)
    missing_page_ids: List[str] = Field(default_factory=list)
    ordering_ok: bool = True
    classification_consistent: Optional[bool] = None
    comparability_disclosed: bool = False
    violations: List[Dict[str, str]] = Field(default_factory=list)

    def _fail(self, code: str, detail: str) -> None:
        self.passed = False
        if len(self.violations) < _MAX_VIOLATIONS:
            self.violations.append({
                "code": code[:64], "detail": detail[:200],
            })


def verify_atlas_pages(
    bindings: List[Any],
    rendered_page_ids: List[str],
    *,
    classification_scope: ClassificationScope,
    classification_scope_disclosed: bool,
    rendered_legend_digests: Optional[Dict[str, Optional[str]]] = None,
) -> AtlasQaReport:
    """页级门（1/3/4 + 页数的前两元）。``rendered_page_ids`` = 渲染序。"""
    report = AtlasQaReport(page_count_expected=len(bindings),
                           page_count_rendered=len(rendered_page_ids))
    expected_ids = [b.page_id for b in bindings]

    # 完整性：重复
    seen: Dict[str, int] = {}
    for pid in rendered_page_ids:
        seen[pid] = seen.get(pid, 0) + 1
    dups = sorted(pid for pid, n in seen.items() if n > 1)
    if dups:
        report.duplicate_page_ids = dups[:20]
        report._fail("atlas_page_duplicate", f"重复页: {', '.join(dups[:8])}")
    # 完整性：缺页（重复计数下缺页判断按集合语义）
    missing = [pid for pid in expected_ids if pid not in seen]
    if missing:
        report.missing_page_ids = missing[:20]
        report._fail("atlas_page_missing", f"缺页: {', '.join(missing[:8])}")
    # 渲染序 = 计划序（页码/目录与实际页面一一对应的前提）
    plan_rendered_order = [pid for pid in expected_ids if pid in seen]
    if [pid for pid in rendered_page_ids if seen.get(pid)] != plan_rendered_order:
        report.ordering_ok = False
        report._fail("atlas_page_order", "渲染序与计划页序不一致")
    # 页数（expected == rendered）
    if report.page_count_expected != report.page_count_rendered:
        report._fail(
            "atlas_page_count_mismatch",
            f"计划 {report.page_count_expected} 页 vs 渲染 {report.page_count_rendered} 页")
    # 分类一致性（shared 硬门；local 只要求披露）
    report.comparability_disclosed = classification_scope_disclosed
    if classification_scope == ClassificationScope.shared:
        digests = set()
        for b in bindings:
            if b.cover:
                continue
            digests.add(b.legend_digest)
        digests.discard(None)
        consistent = len(digests) <= 1
        report.classification_consistent = consistent
        if not consistent:
            report._fail(
                "atlas_classification_inconsistent",
                f"shared scope 下内容页 legend_digest 不一致（{len(digests)} 种）")
        if rendered_legend_digests:
            rendered_digests = {v for v in rendered_legend_digests.values()
                                if v is not None}
            if len(rendered_digests) > 1:
                report.classification_consistent = False
                report._fail(
                    "atlas_classification_inconsistent",
                    f"渲染面复核：内容页 legend_digest {len(rendered_digests)} 种")
    else:
        report.classification_consistent = None
        if not classification_scope_disclosed:
            report._fail(
                "atlas_comparability_undisclosed",
                "local scope 未披露跨页不可比（manifest 缺 comparability 披露）")
    # 内容页范围在场
    for b in bindings:
        if b.cover:
            continue
        if not b.bounds:
            report._fail("atlas_page_bounds_missing", f"页 {b.page_id} 无地图范围")
            break
    return report


def verify_pdf_page_count(report: AtlasQaReport, pdf_page_count: int) -> AtlasQaReport:
    """门 2 的第三元：pypdf 实际页数 == expected（组装后复核）。"""
    if pdf_page_count != report.page_count_expected:
        report._fail(
            "atlas_page_count_mismatch",
            f"PDF 实际 {pdf_page_count} 页 vs 计划 {report.page_count_expected} 页")
    return report


__all__ = ["AtlasQaReport", "verify_atlas_pages", "verify_pdf_page_count"]
