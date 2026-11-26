"""Series Production 契约 —— 系列冻结计划与成品 manifest（WP5）。

**系列 ≠ 单会话图册**（AtlasJobSpec 是单会话多页）；系列 = 独立 MapSpec
产品集合的出版计划。冻结纪律（ADR-0226 Freeze 同款）：

- 冻结计划 pin 每成员 `{session_id, mapspec_fingerprint, print_layout_digest?,
  data_fingerprints, title, design_spec pin, output}` —— 执行前逐项对账，
  指纹不符 = stale 成员（typed blocked，绝不静默用新内容）；
- 成员缓存键 = 全部冻结输入的 canonical sha256 —— 同数据/同规范/同版式
  才复用；任一输入变化只失效该成员（增量语义，绝不错用旧字节）；
- manifest 无 wall-clock 字段（确定性；同输入同字节）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.design_specs.contract import canonical_json

#: 契约版本。
SERIES_FREEZE_PLAN_VERSION = "series_freeze_plan.v1"
SERIES_MANIFEST_VERSION = "series_publication_manifest.v1"

#: 支持的成品格式（真实渲染链；png 需要 rsvg-convert 在场，缺席 typed 降级）。
SERIES_FORMATS: tuple = ("pdf", "svg", "png")

_MAX_MEMBERS = 50
_MAX_FORMATS = 3


class FrozenMember(BaseModel):
    """冻结成员：生产输入的全部事实 pin（防漂移）。"""

    model_config = ConfigDict(extra="forbid")

    member_id: str = Field(min_length=1, max_length=40)
    title: str = Field(default="", max_length=160)
    session_id: str = Field(min_length=1, max_length=255)
    mapspec_fingerprint: str = Field(min_length=8, max_length=80)
    print_layout_digest: Optional[str] = Field(default=None, max_length=80)
    #: 逐层数据指纹（freeze 期从源 profile/data_fingerprint 提取；有界）
    data_fingerprints: Dict[str, str] = Field(default_factory=dict)
    design_spec_id: Optional[str] = Field(default=None, max_length=36)
    design_spec_version: Optional[int] = Field(default=None, ge=1)
    role: str = Field(default="primary", max_length=20)


class SeriesFreezePlan(BaseModel):
    """系列冻结计划（production 输入；frozen + extra=forbid）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec_version: Literal["series_freeze_plan.v1"] = SERIES_FREEZE_PLAN_VERSION
    series_id: str = Field(min_length=1, max_length=36)
    name: str = Field(default="", max_length=255)
    design_spec_id: Optional[str] = Field(default=None, max_length=36)
    design_spec_version: Optional[int] = Field(default=None, ge=1)
    design_spec_digest: Optional[str] = Field(default=None, max_length=80)
    output: Dict[str, Any] = Field(default_factory=dict)
    members: List[FrozenMember] = Field(default_factory=list)
    simulated_data: bool = False
    """数据来源披露：合成/测试数据必须置 true（manifest 恒如实传播）。"""

    def plan_digest(self) -> str:
        """冻结输入 canonical sha256 —— 缓存键与 manifest inputs 指纹。"""
        payload = {
            "spec_version": self.spec_version,
            "series_id": self.series_id,
            "design_spec_id": self.design_spec_id,
            "design_spec_version": self.design_spec_version,
            "design_spec_digest": self.design_spec_digest,
            "output": self.output,
            "members": [m.model_dump() for m in self.members],
        }
        return "splan-sha256:" + _sha256(canonical_json(payload))


class MemberArtifactRecord(BaseModel):
    """成员产物记录（manifest 面；无环境 URL —— 文件名+checksum 可复现）。"""

    model_config = ConfigDict(extra="forbid")

    fmt: str
    filename: str = Field(max_length=255)
    checksum: str = Field(max_length=80)
    bytes_size: int = Field(ge=0)
    media_type: str = Field(default="", max_length=60)


class MemberQaRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    checks: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class MemberResult(BaseModel):
    """成员生产结果（executor 逐项回执；status 词表封闭）。"""

    model_config = ConfigDict(extra="forbid")

    member_id: str = Field(max_length=40)
    status: Literal["complete", "cached_reused", "failed", "blocked", "skipped"]
    code: Optional[str] = None
    message: str = Field(default="", max_length=300)
    artifacts: List[MemberArtifactRecord] = Field(default_factory=list)
    qa: Optional[MemberQaRecord] = None
    member_fingerprint: Optional[str] = None
    cache_key: Optional[str] = None


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_freeze_plan_payload(payload: Dict[str, Any]) -> SeriesFreezePlan:
    """raw dict → typed 计划（fail-closed；typed pydantic errors 上抛）。"""
    plan = SeriesFreezePlan.model_validate(payload)
    if not plan.members:
        raise ValueError("freeze plan requires at least one member")
    if len(plan.members) > _MAX_MEMBERS:
        raise ValueError(f"freeze plan members over budget (>{_MAX_MEMBERS})")
    member_ids = [m.member_id for m in plan.members]
    if len(set(member_ids)) != len(member_ids):
        raise ValueError("duplicate member_id in freeze plan")
    return plan


def output_formats(output: Dict[str, Any]) -> List[str]:
    """output.targets/series_formats → 有效格式列表（pdf+svg 缺省；有界）。"""
    requested = output.get("series_formats") or output.get("formats") or ["pdf", "svg"]
    if isinstance(requested, str):
        requested = [requested]
    formats = [str(f).lower() for f in requested][:_MAX_FORMATS]
    valid = [f for f in formats if f in SERIES_FORMATS]
    return valid or ["pdf", "svg"]


__all__ = [
    "SERIES_FREEZE_PLAN_VERSION",
    "SERIES_MANIFEST_VERSION",
    "SERIES_FORMATS",
    "FrozenMember",
    "SeriesFreezePlan",
    "MemberArtifactRecord",
    "MemberQaRecord",
    "MemberResult",
    "validate_freeze_plan_payload",
    "output_formats",
]
