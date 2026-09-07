"""Repair Proposal Seam —— QualityIssue → REMEDIATION_OPS 词表映射（plan-only）。

审计 03 §1.3/§7 R5（W3-9）：``repairable`` 质量标志、修复操作词表
（``gis_harness.data_qualification.REMEDIATION_OPS`` / ``REMEDIATION_OP_BACKING``）
与修复执行器（``spatial_repair_pipeline``）都已存在，但没有任何环节把
质量报告翻译成显式修复提案 —— 修复提案是孤儿阶段。本模块补上这一**缝**：

    QualityReport / issue codes → list[RepairProposal]（有界、机器可读）

红线（Wave-4 契约）：
- **plan-only：本模块绝不执行任何修复** —— 执行仍走既有
  ``SpatialRepairPipeline`` / geocompute 路径（由后续 wave 接线）；
- **单一词表源**：operation 只允许引用 ``REMEDIATION_OPS``（模块导入期
  校验，漂移即 fail-fast），实现背书引用 ``REMEDIATION_OP_BACKING``；
  app/lib/data/quality.py 的诊断码在此**别名**到该词表 —— 不新建平行词表；
- 每条提案携带 ``auto_applicable`` / ``confidence``：缺输入（如 CRS 缺失
  的重投影需要用户先声明源 CRS）→ 不可自动应用，宁可问询不猜；
- 输出有界（≤8 条，字段截断），确定性：同报告同提案。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.services.gis_harness.data_qualification import (
    REMEDIATION_OP_BACKING,
    REMEDIATION_OPS,
)

logger = logging.getLogger(__name__)

_MAX_PROPOSALS = 8


@dataclass(frozen=True)
class RepairProposal:
    """一条显式修复提案（声明意图，不执行；与 RemediationStep 同形约束）。"""

    operation: str                 # ⊆ REMEDIATION_OPS（词表校验见模块尾）
    target: str = ""               # 字段/波段定位；空 = 整个数据集
    params: Dict[str, Any] = field(default_factory=dict)
    reason_code: str = ""          # 触发的质量诊断码（稳定机器可读）
    auto_applicable: bool = False  # True = 有确定性实现且无缺输入
    confidence: float = 0.5
    backing: Tuple[str, ...] = ()  # REMEDIATION_OP_BACKING 实现背书引用
    disclosure: str = ""           # 用户可见披露（为什么会提这条）

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation[:32],
            "target": self.target[:64],
            "params": dict(list(self.params.items())[:6]),
            "reason_code": self.reason_code[:64],
            "auto_applicable": self.auto_applicable,
            "confidence": round(self.confidence, 2),
            "backing": [str(b)[:80] for b in self.backing[:4]],
            "disclosure": self.disclosure[:200],
        }


def _p(
    code: str,
    operation: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    auto_applicable: bool = True,
    confidence: float = 0.6,
    disclosure: str = "",
) -> RepairProposal:
    """映射表构造器：backing 自动取自 REMEDIATION_OP_BACKING（单一事实源）。"""
    return RepairProposal(
        operation=operation,
        params=params or {},
        reason_code=code,
        auto_applicable=auto_applicable,
        confidence=confidence,
        backing=tuple(REMEDIATION_OP_BACKING.get(operation, ())),
        disclosure=disclosure,
    )


# ── 别名表：app/lib/data/quality.py 诊断码 → REMEDIATION_OPS 修复操作 ────
# 每行 = (operation, params, auto_applicable, confidence, disclosure)。
# 无映射的诊断码（EMPTY_PAYLOAD / EXTENT_MISMATCH / TEMPORAL_GAPS /
# SCHEMA_TRUNCATED / PROFILE_INCOMPLETE / MISSING_REQUIRED_FIELDS 等）属于
# 「阻断或证据不足」，不是数据修复操作 —— 如实不提案，绝不硬凑。
_REPAIR_MAP: Dict[str, RepairProposal] = {
    # CRS / 坐标
    "crs_missing": _p(
        "crs_missing", "reproject",
        params={"requires_declared_crs": True, "target_crs": None},
        auto_applicable=False, confidence=0.7,
        disclosure="CRS 未知：需先声明源 CRS 才能重投影；绝不静默假设 EPSG:4326。",
    ),
    "crs_suspicious": _p(
        "crs_suspicious", "reproject",
        params={"requires_confirmed_crs": True},
        auto_applicable=False, confidence=0.4,
        disclosure="坐标范围与声明 CRS 不符：确认真实 CRS 并更正声明后重投影。",
    ),
    "impossible_coordinates": _p(
        "impossible_coordinates", "reproject",
        params={"requires_declared_crs": True},
        auto_applicable=False, confidence=0.5,
        disclosure="经纬度出界：常见原因是投影坐标误标为经纬度，需声明正确 CRS 后重投影。",
    ),
    "zero_coordinates": _p(
        "zero_coordinates", "filter_null",
        params={"predicate": "drop_zero_zero"},
        confidence=0.6,
        disclosure="大量 (0,0) 坐标疑似缺失值填充：过滤后重查。",
    ),
    # 几何
    "invalid_geometry": _p(
        "invalid_geometry", "repair_geometry",
        params={"mode": "make_valid"}, confidence=0.9,
        disclosure="无效几何：make_valid 确定性修复。",
    ),
    "self_intersection": _p(
        "self_intersection", "repair_geometry",
        params={"mode": "make_valid"}, confidence=0.85,
        disclosure="自相交面：make_valid 修复后重查。",
    ),
    "empty_geometry": _p(
        "empty_geometry", "filter_null",
        params={"predicate": "drop_empty_geometry"}, confidence=0.8,
        disclosure="空几何不能参与空间分析：剔除空几何要素。",
    ),
    "duplicate_geometries": _p(
        "duplicate_geometries", "repair_geometry",
        params={"mode": "deduplicate"}, confidence=0.8,
        disclosure="重复几何：按几何去重（保留属性最全）。",
    ),
    "duplicate_rows": _p(
        "duplicate_rows", "repair_geometry",
        params={"mode": "deduplicate"}, confidence=0.6,
        disclosure="重复行：按主键/全字段去重。",
    ),
    # 行 / 字段
    "null_heavy_field": _p(
        "null_heavy_field", "filter_null",
        params={"target_field": None},  # target 字段由 issue.field 填充
        confidence=0.7,
        disclosure="字段缺失率过高：过滤空值行或显式声明不可用。",
    ),
    "inconsistent_unit": _p(
        "inconsistent_unit", "normalize",
        params={"requires_declared_unit": True},
        auto_applicable=False, confidence=0.4,
        disclosure="单位不一致：需先声明目标单位再做属性归一。",
    ),
    "invalid_dates": _p(
        "invalid_dates", "normalize",
        params={"mode": "iso8601"}, confidence=0.6,
        disclosure="日期值无法解析：统一为 ISO 8601。",
    ),
    "encoding_issues": _p(
        "encoding_issues", "normalize",
        params={"mode": "reimport_encoding", "suggested_encoding": "gb18030"},
        auto_applicable=False, confidence=0.6,
        disclosure="编码问题需以正确编码重新导入（中文场景常见 GBK/GB18030）。",
    ),
    # 栅格
    "nodata_saturation": _p(
        "nodata_saturation", "filter_nodata",
        confidence=0.6,
        disclosure="有效像元占比过低：nodata 掩膜后取有效区。",
    ),
    "resolution_mismatch": _p(
        "resolution_mismatch", "resample",
        params={"requires_target_resolution": True},
        auto_applicable=False, confidence=0.5,
        disclosure="多源分辨率不一致：需声明目标分辨率后重采样。",
    ),
}

#: 有映射（= 可提案修复）的诊断码集合（供上游统计 repairable 数量）。
REPAIRABLE_ISSUE_CODES: Set[str] = frozenset(_REPAIR_MAP)  # type: ignore[assignment]


def _proposal_for_issue(code: str, target_field: str = "") -> Optional[RepairProposal]:
    base = _REPAIR_MAP.get(str(code))
    if base is None:
        return None
    target = target_field or ""
    if "target_field" in base.params and target_field:
        # 字段级提案：把定位写进 params（如 filter_null 的 target_field）
        params = dict(base.params)
        params["target_field"] = target_field
        return RepairProposal(
            operation=base.operation, target=target, params=params,
            reason_code=base.reason_code, auto_applicable=base.auto_applicable,
            confidence=base.confidence, backing=base.backing,
            disclosure=base.disclosure,
        )
    return RepairProposal(
        operation=base.operation, target=target, params=base.params,
        reason_code=base.reason_code, auto_applicable=base.auto_applicable,
        confidence=base.confidence, backing=base.backing,
        disclosure=base.disclosure,
    )


def _issue_code_str(value: Any) -> str:
    """诊断码 → 规范字符串（QualityIssueCode 是 (str, Enum)：
    ``str()`` 会得到 'QualityIssueCode.crs_missing'，必须取 ``value``）。"""
    return str(getattr(value, "value", value))


def propose_repairs(quality_report: Any) -> List[RepairProposal]:
    """QualityReport → 有界修复提案列表（plan-only，绝不执行）。

    接受 ``app.lib.data.quality.QualityReport``（鸭子类型：任何带
    ``issues``（含 ``code``/``field``）的对象均可）—— 避免导入环，
    码→词表映射统一走 ``propose_repairs_for_issue_codes``。
    """
    issues = list(getattr(quality_report, "issues", None) or [])
    codes = [_issue_code_str(getattr(i, "code", "")) for i in issues]
    return propose_repairs_for_issue_codes(
        codes,
        fields={
            _issue_code_str(getattr(i, "code", "")): str(getattr(i, "field", "") or "")
            for i in issues
        },
    )


def propose_repairs_for_issue_codes(
    codes: Sequence[str],
    fields: Optional[Dict[str, str]] = None,
) -> List[RepairProposal]:
    """诊断码列表 → 有界修复提案（有界视图路径：quality.summary() 的 issues）。"""
    fields = fields or {}
    proposals: List[RepairProposal] = []
    seen: Set[str] = set()
    for code in codes:
        code = str(code)
        if not code or code in seen:
            continue
        seen.add(code)
        proposal = _proposal_for_issue(code, fields.get(code, ""))
        if proposal is not None:
            proposals.append(proposal)
        if len(proposals) >= _MAX_PROPOSALS:
            break
    return proposals


# 词表守卫：映射表任何 operation 漂移出 REMEDIATION_OPS → 导入期即失败
# （与 test_data_qualification 的 registry 对账同纪律，fail-fast）。
_invalid = [op for op in {p.operation for p in _REPAIR_MAP.values()} if op not in REMEDIATION_OPS]
if _invalid:  # pragma: no cover — 防御性：词表漂移在 CI 立即暴露
    raise RuntimeError(
        f"repair_planning 映射表引用了未知修复操作 {_invalid}；"
        f"合法词表 = REMEDIATION_OPS（单一事实源）"
    )

__all__ = [
    "RepairProposal",
    "REPAIRABLE_ISSUE_CODES",
    "propose_repairs",
    "propose_repairs_for_issue_codes",
]
