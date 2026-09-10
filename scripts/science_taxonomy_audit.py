#!/usr/bin/env python3
"""Science V6 taxonomy 覆盖矩阵审计（Goal 07 Phase A）。

回答「科学算法 taxonomy 的 14 个族在注册表里各有多少算法、成熟度如何、
缺什么」——矩阵**从注册表机器推导**（不是手写文档），taxonomy → 域
category / algorithm_family 的对齐词表在本文件唯一维护；新增算法落进
对应族后重跑本脚本即更新矩阵。

用法：``python scripts/science_taxonomy_audit.py``（工作树根目录执行）。
输出是确定性的：同注册表状态必产生字节相同的报告；
``--check`` 模式与 docs/science/SCIENCE_V6_TAXONOMY_AUDIT.md diff
（漂移 = 注册表与审计文档失同步，非零退出）。

族对齐约定（与 Goal 07 taxonomy 一致）：

- 对齐键 = descriptor.category **或** descriptor.algorithm_family 的前缀
  词表（TAXONOMY_FAMILIES.alignment）；
- Uncertainty 是横切族：按 uncertainty_outputs 非空 + family 白名单计数；
- platform/data_access 是平台面（非科学族），显式排除。
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.lib.gis.algorithm_registry import get_algorithm_registry  # noqa: E402

AUDIT_DOC = ROOT / "docs" / "science" / "SCIENCE_V6_TAXONOMY_AUDIT.md"

#: 14 族稳定词表（Goal 07 taxonomy；纯加法演进）。alignment 是 category
#: 精确匹配 ∪ algorithm_family 前缀匹配的并集；families 互斥（按序首中）。
@dataclass(frozen=True)
class TaxonomyFamily:
    family_id: str
    label_zh: str
    categories: tuple = ()
    family_prefixes: tuple = ()
    exclude_family_prefixes: tuple = ()
    cross_cutting: bool = False  # Uncertainty：按 uncertainty_outputs 判定


# 族序注意：change_detection 的 family 前缀必须先于 remote_sensing /
# time_series 的 category 匹配（remote.change.* / temporal.anomaly 的
# category 分别落在遥感/时序，family 才是其变化检测语义的真实归属）。
TAXONOMY_FAMILIES: tuple = (
    TaxonomyFamily("raster", "栅格", categories=("raster_analysis",)),
    TaxonomyFamily("vector", "矢量",
                   categories=("geometry_processing", "spatial_relationship")),
    TaxonomyFamily("terrain", "地形", family_prefixes=("terrain_gradient",
                   "terrain_curvature", "terrain_geomorphometry",
                   "terrain_morphometry", "terrain_neighborhood", "viewshed",
                   "contour_extraction", "erosion_index",
                   "terrain_cost_mapping")),
    TaxonomyFamily("hydrology", "水文", family_prefixes=("terrain_hydrology",
                   "terrain_wetness")),
    TaxonomyFamily("spatial_statistics", "空间统计",
                   categories=("spatial_statistics", "point_pattern",
                               "spatial_regression"),
                   family_prefixes=("spatial_weights",)),
    TaxonomyFamily("interpolation", "插值", categories=("interpolation",)),
    TaxonomyFamily("network", "网络", categories=("network_analysis",)),
    TaxonomyFamily("change_detection", "变化检测",
                   family_prefixes=("change_detection", "temporal_change",
                                    "change_point")),
    TaxonomyFamily("remote_sensing", "遥感",
                   categories=("remote_sensing",),
                   family_prefixes=("sar_", "spectral_", "radiometric_")),
    TaxonomyFamily("time_series", "时间序列", categories=("temporal_analysis",)),
    TaxonomyFamily("ecology", "生态",
                   family_prefixes=("ecology_", "landscape_", "habitat_")),
    TaxonomyFamily("mcda", "多准则决策", categories=("decision_analysis",)),
    TaxonomyFamily("sampling", "空间抽样",
                   family_prefixes=("sampling_", "spatial_sampling")),
    TaxonomyFamily("uncertainty", "不确定性（横切）", cross_cutting=True),
)

#: 非科学族（平台面/数据接入）——排除在矩阵外但在报告尾部披露。
NON_SCIENCE_CATEGORIES = ("platform", "data_access", "flow_analysis", "")


def _match_family(algo) -> str:
    """确定性族归属：按 TAXONOMY_FAMILIES 顺序首中；未中返回 ""。"""
    for fam in TAXONOMY_FAMILIES:
        if fam.cross_cutting:
            continue
        if algo.category in fam.categories:
            return fam.family_id
        if any(algo.algorithm_family.startswith(p)
               for p in fam.family_prefixes):
            return fam.family_id
    return ""


@dataclass
class FamilyStats:
    family_id: str
    label_zh: str
    algorithms: list = field(default_factory=list)

    @property
    def validated(self) -> list:
        return [a for a in self.algorithms
                if a.scientific_status in ("VALIDATED", "PRODUCTION")]

    @property
    def native(self) -> list:
        return [a for a in self.algorithms if a.runtime_status == "native"]


def build_matrix() -> dict:
    registry = get_algorithm_registry()
    stats = {f.family_id: FamilyStats(f.family_id, f.label_zh)
             for f in TAXONOMY_FAMILIES}
    unaligned: list = []
    for algo in registry._by_id.values():
        fid = _match_family(algo)
        if fid:
            stats[fid].algorithms.append(algo)
        elif algo.category not in NON_SCIENCE_CATEGORIES:
            unaligned.append(algo)
    # 横切 Uncertainty 族：uncertainty_outputs 非空即计入（不去重于原族）。
    for algo in registry._by_id.values():
        if algo.uncertainty_outputs:
            stats["uncertainty"].algorithms.append(algo)
    return {
        "total": registry.count,
        "stats": stats,
        "unaligned": sorted(unaligned, key=lambda a: a.id),
    }


def render_report(matrix: dict) -> str:
    lines = [
        "# Science V6 Taxonomy 审计（自动生成 · Goal 07 Phase A）",
        "",
        "> **本文件由注册表生成，请勿手工编辑。** 事实源：",
        "> `app/lib/gis/algorithms/`（算法 descriptor 的 category /",
        "> algorithm_family / uncertainty_outputs）。再生成：",
        "> `python scripts/science_taxonomy_audit.py`；`--check` 模式 diff。",
        "> 族对齐词表唯一维护于脚本内 `TAXONOMY_FAMILIES`。",
        "",
        f"注册表算法总数：**{matrix['total']}**。",
        "",
        "| 族 | 算法数 | native | VALIDATED/PRODUCTION | 代表算法 |",
        "|---|---|---|---|---|",
    ]
    for fam in TAXONOMY_FAMILIES:
        s = matrix["stats"][fam.family_id]
        total = len(s.algorithms)
        if fam.cross_cutting:
            # 横切族只报计数（成员与原族重复，不列代表算法）。
            lines.append(
                f"| {fam.label_zh} | {total}（横切） | — | — |"
                " 声明 uncertainty_outputs 的算法全体 |")
            continue
        reps = ", ".join(f"`{a.id}`" for a in sorted(
            s.validated or s.algorithms, key=lambda a: a.id)[:3]) or "—"
        lines.append(
            f"| {fam.label_zh} | {total} | {len(s.native)} | "
            f"{len(s.validated)} | {reps} |")
    lines += [
        "",
        "## 族对齐约定",
        "",
        "- 对齐键 = descriptor.category（精确）∪ algorithm_family（前缀），"
        "按 `"
        + " → ".join(f.family_id for f in TAXONOMY_FAMILIES
                     if not f.cross_cutting)
        + " ` 顺序首中（互斥；change_detection 的 family 前缀先于",
        "  remote_sensing / time_series 的 category 匹配）；",
        "- Uncertainty 是横切族：`uncertainty_outputs` 非空即计入，成员与",
        "  原族重复计数；",
        f"- platform / data_access / flow_analysis 为平台面，不入矩阵",
        f"（未对齐且非平台的算法 {len(matrix['unaligned'])} 个，见下）。",
        "",
    ]
    if matrix["unaligned"]:
        lines.append("## 未对齐算法（非平台、无族归属）")
        lines.append("")
        for a in matrix["unaligned"]:
            lines.append(f"- `{a.id}`（category={a.category!r}, "
                         f"family={a.algorithm_family!r}）")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="与审计文档 diff，漂移非零退出")
    args = parser.parse_args()

    report = render_report(build_matrix())
    if args.check:
        current = AUDIT_DOC.read_text(encoding="utf-8") if AUDIT_DOC.exists() else ""
        if current != report:
            sys.stderr.write(
                f"taxonomy audit drift: {AUDIT_DOC} is stale — "
                "re-run scripts/science_taxonomy_audit.py\n")
            return 1
        print("taxonomy audit in sync")
        return 0
    AUDIT_DOC.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_DOC.write_text(report, encoding="utf-8")
    print(f"wrote {AUDIT_DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
