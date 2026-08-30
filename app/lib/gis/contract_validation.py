"""Artifact 输出契约验证（V2 P2，纯函数、有界、零 IO）。

§10：工具执行成功 + ``success=true`` ≠ 产物一定符合算法声明的输出契约。
本模块比对「声明词」（capability 的 output_artifact_types / algorithm 的
output_artifact_type）与「实况画像」（DatasetProfile，来自 ref descriptor
—— store() 时一次遍历的 O(1) 元数据），产出有界 findings：

- 校验是**派生披露**，不是第二执行门：findings 只进 ArtifactRegistry
  metadata（contract_check）与日志，绝不阻断工具/计划路径 —— 与
  ADR-0082「注册是增值记录，失败降级」同一哲学；
- 未知不判死：profile 缺 geometry（descriptor-only 画像）时跳过几何比对
  （unknown ≠ mismatch）；CRS 缺失只 warning 不 error；
- 有界：findings ≤ MAX_CONTRACT_FINDINGS，detail ≤ 160 字符。

架构位置：调用于 session_plan plan-apply seam（capability 声明唯一在场
点）。不进 dispatch seam —— 那里无 capability 上下文，声明不可得。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from app.lib.gis.artifacts import artifact_type
from app.lib.gis.dataset_profile import DatasetProfile

MAX_CONTRACT_FINDINGS = 8
_MAX_DETAIL = 160

# finding codes（V2 词表；与 map_completion 的 F_* 命名族同风格）
C_GEOMETRY_KIND_MISMATCH = "contract_geometry_kind_mismatch"
C_UNREGISTERED_TYPE = "contract_unregistered_type"
C_EMPTY_ARTIFACT = "contract_empty_artifact"
C_CRS_UNDECLARED = "contract_crs_undeclared"
# Runtime V3（ADR-0089 §P13）：栅格输出契约 —— 声明栅格族的产物必须携带
# 网格证据（宽高/波段数）；证据缺席/不完整只披露不判死（unknown ≠ mismatch）。
C_RASTER_GRID_EVIDENCE_MISSING = "contract_raster_grid_evidence_missing"
C_RASTER_GRID_EVIDENCE_INCOMPLETE = "contract_raster_grid_evidence_incomplete"

# raster 族的 artifact type（geometry_kind == "raster"）
_RASTER_KIND = "raster"


@dataclass(frozen=True)
class ContractFinding:
    code: str
    severity: str            # error / warning
    target: str
    detail: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "target": self.target[:64],
            "detail": self.detail[:_MAX_DETAIL],
        }


def _declared_geometry_kinds(declared_types: Sequence[str]) -> List[str]:
    """声明类型的 geometry_kind 并集（未注册类型由专用 finding 披露）。"""
    kinds: List[str] = []
    for tid in declared_types:
        desc = artifact_type(tid)
        if desc is not None and desc.geometry_kind not in kinds:
            kinds.append(desc.geometry_kind)
    return kinds


def validate_output_contract(
    declared_types: Sequence[str],
    profile: Optional[DatasetProfile],
) -> List[ContractFinding]:
    """声明输出词表 vs 实况画像 → 有界 findings（纯函数，零 IO）。

    参数为空（无声明或无画像）时返回 []——没有契约可验是一种合法状态，
    不虚构 findings。"""
    declared = [str(t) for t in (declared_types or []) if t]
    if not declared or profile is None:
        return []

    findings: List[ContractFinding] = []

    # 1) 声明词必须是注册类型（词表漂移在这里现形）。
    unregistered = [t for t in declared if artifact_type(t) is None]
    if unregistered:
        findings.append(ContractFinding(
            code=C_UNREGISTERED_TYPE,
            severity="error",
            target=declared[0],
            detail=f"declared output type(s) not registered: {','.join(unregistered[:4])}",
        ))

    # 2) 几何族比对：两边都已知时才裁决（unknown 不判死）。
    actual_kind = profile.geometry_kind
    if actual_kind != "unknown":
        declared_kinds = [k for k in _declared_geometry_kinds(declared) if k != "unknown"]
        if declared_kinds and actual_kind not in declared_kinds:
            # 栅格/矢量跨族是最危险的错配（例如把点集标成 stats_table）。
            findings.append(ContractFinding(
                code=C_GEOMETRY_KIND_MISMATCH,
                severity="error",
                target=declared[0],
                detail=(
                    f"declared {declared[0]} (geometry_kind={'/'.join(declared_kinds)}) "
                    f"but artifact is {actual_kind}"
                ),
            ))

    # 3) 空产物披露（合法但下游消费需要知道）。
    if profile.is_empty:
        findings.append(ContractFinding(
            code=C_EMPTY_ARTIFACT,
            severity="warning",
            target=declared[0],
            detail="artifact has feature_count == 0 (empty result)",
        ))

    # 4) CRS 缺失披露：metric 参数算法下游依赖 CRS 事实，未知即如实说。
    if not profile.crs:
        findings.append(ContractFinding(
            code=C_CRS_UNDECLARED,
            severity="warning",
            target=declared[0],
            detail="artifact profile carries no CRS evidence",
        ))

    # 5) 栅格网格证据（Runtime V3 §P13）：声明 raster 族的产物，宽高/波段
    #    数是下游对齐/瓦片消费的硬前提。画像缺 raster 子结构 → 证据缺席
    #    （warning）；有 raster 子结构但关键字段 unknown → 不完整（warning）。
    declared_kinds_all = _declared_geometry_kinds(declared)
    if _RASTER_KIND in declared_kinds_all:
        raster_profile = getattr(profile, "raster", None)
        if raster_profile is None:
            findings.append(ContractFinding(
                code=C_RASTER_GRID_EVIDENCE_MISSING,
                severity="warning",
                target=declared[0],
                detail="raster artifact declared but profile carries no grid evidence",
            ))
        else:
            missing = [
                name for name, val in (
                    ("width", raster_profile.width),
                    ("height", raster_profile.height),
                    ("band_count", raster_profile.band_count),
                ) if not val
            ]
            if missing:
                findings.append(ContractFinding(
                    code=C_RASTER_GRID_EVIDENCE_INCOMPLETE,
                    severity="warning",
                    target=declared[0],
                    detail=f"raster grid evidence incomplete: unknown {','.join(missing)}",
                ))

    return findings[:MAX_CONTRACT_FINDINGS]


def contract_check_metadata(
    declared_types: Sequence[str],
    profile: Optional[DatasetProfile],
) -> Optional[dict]:
    """findings → ArtifactRegistry metadata["contract_check"]（有界 dict）。

    无 findings 时返回 None（不写空键）。"""
    findings = validate_output_contract(declared_types, profile)
    if not findings:
        return None
    return {
        "declared": [str(t)[:64] for t in (declared_types or [])][:8],
        "findings": [f.to_dict() for f in findings],
    }


def log_contract_findings(findings: Sequence[ContractFinding], *, session_id: str, ref: str) -> None:
    """findings → 结构化 warning 日志（注册路径是增值记录，不抛错）。"""
    if not findings:
        return
    import logging

    logger = logging.getLogger(__name__)
    for f in findings:
        logger.warning(
            "[ArtifactContract] session=%s ref=%s %s %s: %s",
            session_id, ref, f.severity, f.code, f.detail,
        )


def findings_from_metadata(metadata: Optional[dict]) -> List[dict]:
    """从 artifact metadata 读回 contract_check（dependency report 投影用）。"""
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("contract_check")
    if not isinstance(raw, dict):
        return []
    findings = raw.get("findings")
    if not isinstance(findings, list):
        return []
    return [f for f in findings if isinstance(f, dict)][:MAX_CONTRACT_FINDINGS]
