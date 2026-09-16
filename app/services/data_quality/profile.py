"""统一 DataQualityProfile —— 前置分析质量画像（聚合投影，非第二引擎）。

定位（DQH v1；勘察见 .agent-work/data-quality-harmonization-v1/）：仓库已有
五套质量子系统（lib 剖析证据检查 / 规则引擎 / 空间审计 / pre-cartography 门 /
五态数据资格）。本模块**不重写其中任何一个**，只把分析前需要的质量事实聚合为
一个有界、确定性、可附着的画像对象：

    build_data_quality_profile(...) → DataQualityProfile

红线：

- **聚合层**：各来源保持原生形状的有界投影（``sections``），不发明第二套
  issue 词表 —— 跨源 issue 只收 lib 词表（QualityIssue）与语义检测器产出；
- **确定性**：``profile_digest`` 是输入事实的 sha256（同输入同 digest），
  墙钟（``generated_at``）只是元数据，绝不参与指纹；
- **gate 四态**：unknown（无检查事实 ≠ 干净）< ready（仅 info 披露）<
  degraded（warning / 可修复 error / 来源警告）< blocked（不可修复 error /
  lib blocked / 空间审计 blocking）；
- **提案单一来源**：修复提案只经 ``propose_repairs``（W3 单点映射）；
- **有界**：issues ≤32（QualityReport 校验器）、proposals ≤8、sections
  字段截断 —— 绝不携带要素/栅格载荷。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.lib.data.quality import QualityIssue, QualityReport

#: gate 四态（确定性收敛；顺序即严重度序）。
GATE_UNKNOWN = "unknown"
GATE_READY = "ready"
GATE_DEGRADED = "degraded"
GATE_BLOCKED = "blocked"

_MAX_PROPOSALS = 8
_MAX_ROLE_ENTRIES = 64


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    )


class DataQualityProfile(BaseModel):
    """统一前置分析质量画像（有界、确定性 digest、可随证据披露输出）。"""

    target_ref: str = ""
    dataset_fingerprint: str = ""
    gate: str = GATE_UNKNOWN
    issues: List[QualityIssue] = Field(default_factory=list)
    checks_run: List[str] = Field(default_factory=list)
    checks_not_run: List[str] = Field(default_factory=list)
    proposals: List[Dict[str, Any]] = Field(default_factory=list)
    sections: Dict[str, Any] = Field(default_factory=dict)
    disclosure: Dict[str, Any] = Field(default_factory=dict)
    profile_digest: str = ""
    generated_at: str = ""      # 墙钟元数据；绝不参与 profile_digest

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "target_ref": self.target_ref[:80],
            "dataset_fingerprint": self.dataset_fingerprint[:64],
            "gate": self.gate,
            "issues": [
                {
                    "code": i.code.value,
                    "severity": i.severity,
                    "field": i.field or None,
                    "message": str(i.message)[:160] or None,
                }
                for i in self.issues[:32]
            ],
            "issues_total": len(self.issues),
            "checks_run": [str(c)[:32] for c in self.checks_run[:16]],
            "checks_not_run": [str(c)[:32] for c in self.checks_not_run[:16]],
            "proposals": list(self.proposals[:_MAX_PROPOSALS]),
            "sections": {
                str(k)[:24]: (dict(list(v.items())[:8]) if isinstance(v, dict) else v)
                for k, v in list(self.sections.items())[:6]
            },
            "disclosure": dict(list(self.disclosure.items())[:8]),
            "profile_digest": self.profile_digest[:64],
            "generated_at": self.generated_at,
        }


def _compute_digest(
    *,
    target_ref: str,
    dataset_fingerprint: str,
    gate: str,
    issues: List[QualityIssue],
    checks_run: List[str],
    checks_not_run: List[str],
    sections: Dict[str, Any],
) -> str:
    """输入事实 → 确定性 digest（排除墙钟与消息文本，同 RepairPlan 纪律）。"""
    projection = {
        "target_ref": str(target_ref)[:128],
        "dataset_fingerprint": str(dataset_fingerprint)[:128],
        "gate": gate,
        "issues": sorted(
            _canonical({
                "code": str(getattr(i.code, "value", i.code))[:64],
                "severity": str(i.severity)[:16],
                "field": str(i.field or "")[:64],
            })
            for i in issues[:32]
        ),
        "checks_run": sorted(str(c)[:32] for c in checks_run[:24]),
        "checks_not_run": sorted(str(c)[:32] for c in checks_not_run[:24]),
        "spatial_overall": str(
            (sections.get("spatial") or {}).get("overall_status", ""))[:16],
        "rules_overall": str(
            (sections.get("rules") or {}).get("overall_status", ""))[:16],
        "ruleset_digest": str(
            (sections.get("rules") or {}).get("ruleset_digest", ""))[:64],
    }
    return "dqprof_" + hashlib.sha256(
        _canonical(projection).encode("utf-8")
    ).hexdigest()[:24]


def _converge_gate(
    *,
    issues: List[QualityIssue],
    lib_status: str,
    spatial_overall: str,
    rules_overall: str,
    has_any_facts: bool,
) -> str:
    """确定性 gate 收敛（优先级 blocked > degraded > ready > unknown）。"""
    if not has_any_facts:
        return GATE_UNKNOWN
    if any(i.severity == "error" and not i.repairable for i in issues):
        return GATE_BLOCKED
    if lib_status == "blocked":
        return GATE_BLOCKED
    if spatial_overall == "blocking":
        return GATE_BLOCKED
    if any(i.severity in ("warning", "error") for i in issues):
        return GATE_DEGRADED
    if lib_status in ("warning", "repairable"):
        return GATE_DEGRADED
    if spatial_overall == "warning":
        return GATE_DEGRADED
    if rules_overall in ("warn", "fail"):
        return GATE_DEGRADED
    return GATE_READY


def build_data_quality_profile(
    *,
    target_ref: str = "",
    dataset_fingerprint: str = "",
    lib_report: Optional[QualityReport] = None,
    semantic_issues: Optional[List[QualityIssue]] = None,
    semantic_run: Optional[List[str]] = None,
    semantic_not_run: Optional[List[str]] = None,
    semantic_profile: Optional[Any] = None,
    spatial_report: Optional[Any] = None,
    rule_report: Optional[Dict[str, Any]] = None,
) -> DataQualityProfile:
    """各质量来源 → 统一画像（纯聚合；同输入同输出）。"""
    issues: List[QualityIssue] = []
    checks_run: List[str] = []
    checks_not_run: List[str] = []
    lib_status = ""
    sections: Dict[str, Any] = {}

    if lib_report is not None:
        issues.extend(list(getattr(lib_report, "issues", None) or [])[:32])
        checks_run.extend(str(c) for c in (getattr(lib_report, "checks_run", None) or []))
        checks_not_run.extend(
            str(c) for c in (getattr(lib_report, "checks_not_run", None) or []))
        lib_status = str(getattr(getattr(lib_report, "status", ""), "value",
                                 getattr(lib_report, "status", "")) or "")
        sections["lib"] = {
            "quality_status": lib_status,
            "checks_run": [str(c)[:32] for c in checks_run[:16]],
            "checks_not_run": [str(c)[:32] for c in checks_not_run[:16]],
        }

    semantic_issues = list(semantic_issues or [])
    if semantic_issues or semantic_run or semantic_not_run:
        issues.extend(semantic_issues[:32])
        checks_run.extend(str(c) for c in (semantic_run or []))
        checks_not_run.extend(str(c) for c in (semantic_not_run or []))
        sections["semantic"] = {
            "checks_run": [str(c)[:32] for c in (semantic_run or [])[:16]],
            "checks_not_run": [str(c)[:32] for c in (semantic_not_run or [])[:16]],
        }

    if semantic_profile is not None:
        field_roles = [
            fr.to_dict()
            for fr in (getattr(semantic_profile, "field_roles", None) or [])[:_MAX_ROLE_ENTRIES]
        ]
        sections["semantic"] = {
            **sections.get("semantic", {}),
            "field_roles": field_roles,
            "role_index": dict(list(
                (getattr(semantic_profile, "role_index", None) or {}).items()
            )[:_MAX_ROLE_ENTRIES]),
        }

    spatial_overall = ""
    if spatial_report is not None:
        spatial_overall = str(getattr(spatial_report, "overall_status", "") or "")
        sections["spatial"] = {
            "dataset_id": str(getattr(spatial_report, "dataset_id", "") or "")[:64],
            "overall_status": spatial_overall,
            "issue_summary": dict(list(
                (getattr(spatial_report, "issue_summary", None) or {}).items())[:8]),
            "truncated": bool(getattr(spatial_report, "truncated", False)),
        }

    rules_overall = ""
    if rule_report is not None:
        rules_overall = str(rule_report.get("overall_status", "") or "")
        sections["rules"] = {
            "overall_status": rules_overall,
            "failed_count": int(rule_report.get("failed_count", 0) or 0),
            "warn_count": int(rule_report.get("warn_count", 0) or 0),
            "ruleset_digest": str(rule_report.get("ruleset_digest", "") or "")[:64],
        }

    has_any_facts = bool(
        checks_run or checks_not_run or sections.get("spatial") or sections.get("rules")
    )
    gate = _converge_gate(
        issues=issues,
        lib_status=lib_status,
        spatial_overall=spatial_overall,
        rules_overall=rules_overall,
        has_any_facts=has_any_facts,
    )

    # 提案：只走 propose_repairs 单点映射（lib 词表码 → REMEDIATION_OPS）。
    proposal_dicts: List[Dict[str, Any]] = []
    seen_codes: set = set()
    for i in issues:
        if i.code in seen_codes:
            continue
        seen_codes.add(i.code)
    proposals = propose_repairs_for_codes(sorted(c.value for c in seen_codes))
    for p in proposals[:_MAX_PROPOSALS]:
        proposal_dicts.append(p.to_bounded_dict())

    digest = _compute_digest(
        target_ref=target_ref,
        dataset_fingerprint=dataset_fingerprint,
        gate=gate,
        issues=issues,
        checks_run=checks_run,
        checks_not_run=checks_not_run,
        sections=sections,
    )

    severity_counts: Dict[str, int] = {}
    for i in issues:
        severity_counts[i.severity] = severity_counts.get(i.severity, 0) + 1

    disclosure = {
        "quality_gate": gate,
        "issues_total": len(issues),
        "issue_counts": severity_counts,
        "not_checked": [str(c)[:32] for c in checks_not_run[:16]] or None,
        "methodology_note": (
            "quality profile is metadata/sample-derived evidence only "
            "(bounded sampling); proposals require explicit user confirmation "
            "before any repair; unknown gate means no checks had facts"
        ),
    }

    return DataQualityProfile(
        target_ref=str(target_ref or "")[:128],
        dataset_fingerprint=str(dataset_fingerprint or "")[:128],
        gate=gate,
        issues=issues,
        checks_run=checks_run,
        checks_not_run=checks_not_run,
        proposals=proposal_dicts,
        sections=sections,
        disclosure=disclosure,
        profile_digest=digest,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def propose_repairs_for_codes(codes: List[str]):
    """码列表 → 提案（薄转发；独立命名以便 profile 消费方追踪单一来源）。"""
    from app.services.data_ingest.repair_planning import (
        propose_repairs_for_issue_codes,
    )

    return propose_repairs_for_issue_codes(codes)


__all__ = [
    "DataQualityProfile",
    "GATE_UNKNOWN",
    "GATE_READY",
    "GATE_DEGRADED",
    "GATE_BLOCKED",
    "build_data_quality_profile",
]
