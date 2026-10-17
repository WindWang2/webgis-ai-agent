"""RepresentationBinding typed contract（ADR-0219 C2）。

**binding 是动词不是表**：一个 binding = 一次「requirement 语义 → MapSpec
落地」的提案 + 回执。无持久 ledger——幂等由引擎 ``mutation_id`` dedup 与
确定性 fingerprint 承载，跨轮感知由 ``agent_context`` 摘要实时计算。

契约纪律：versioned（``representation_binding.v1``）、bounded（全部字段
显式上限）、deterministic-first（fingerprint = 规范化输入的 sha256 截断，
同语义输入两次构造完全相等）、serializable（model_dump/validate 往返）、
``extra="forbid"``（fail-closed）。
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.gis_harness.requirement_ir.contracts import Provenance

BINDING_SCHEMA = "representation_binding.v1"

#: grammar face 目标（palette / geometry_kind / pinned_channels 的既有
#: 权威路径——``grammar_request_face`` → ``GrammarRequest`` pin，经
#: resolve_symbology 唯一裁决，不构造 MapSpec mutation、不强写 paint）。
BINDING_TARGET_GRAMMAR_FACE = "grammar_request_face"

BindingActor = Literal["user", "agent"]
LockSemantics = Literal["none", "respect_existing", "project_lock"]
BindingTargetKind = Literal[
    # 现有 registry intent kinds（构造正式 mutation）：
    "PatchLayerPresentationIntent",
    "PatchWorkbenchDeltaIntent",
    "PatchComponentIntent",
    "SetLayoutIntent",
    # 既有 grammar face（不构造 mutation）：
    "grammar_request_face",
]

MAX_BINDING_REASON = 200
MAX_BINDING_EVIDENCE = 8


class BindingStatus(str, Enum):
    """binding 生命周期（一次构造 → 一次终态；无中间持久态）。"""

    PROPOSED = "proposed"       # 构造产物（纯函数输出）
    APPLIED = "applied"         # 事务成功提交（含 duplicate 幂等重放）
    REFUSED = "refused"         # 事务拒绝（user-wins 锁/校验），携 error_code
    SUPERSEDED = "superseded"   # CAS 过期（非错误非提交），可携新 revision 重试
    INELIGIBLE = "ineligible"   # 表达与数据/renderer 不兼容，披露回退（不落地）
    SKIPPED = "skipped"         # 目标缺席（如层已不存在），非错误


def binding_fingerprint(payload: Dict[str, Any]) -> str:
    """确定性指纹：规范化输入的 sha256 前 16 hex。

    输入必须是纯 JSON 可序列化的语义元组（调用方保证）；同语义输入
    （dict 键序无关）两次计算完全相等。
    """
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class RepresentationBinding(BaseModel):
    """一条 requirement 语义 → MapSpec 落地的绑定提案与回执。

    字段面（C2 契约）：source requirement path / target mutation intent
    type / actor / provenance / lock semantics / expected MapSpec revision /
    requirement revision / reason / reversible / no-reanalysis flag /
    binding fingerprint。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = BINDING_SCHEMA
    binding_id: str = Field(max_length=64)
    source_requirement_path: str = Field(max_length=128)   # 如 representation.hidden_layer_ids
    target_kind: BindingTargetKind
    actor: BindingActor = "agent"
    #: 落地载荷（intent 构造输入或 grammar pin 面；有界，纯 JSON）。
    payload: Dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)
    lock_semantics: LockSemantics = "respect_existing"
    expected_mapspec_revision: Optional[int] = Field(None, ge=0)
    requirement_revision: Optional[int] = Field(None, ge=1)
    reason: str = Field("", max_length=MAX_BINDING_REASON)
    reversible: bool = True
    #: style-only 绑定绝不触发数据获取/科学计算（构造面即保证：全部
    #: 目标 intent 都是 presentation 类）。
    no_reanalysis: bool = True
    binding_fingerprint: str = Field("", max_length=16)
    status: BindingStatus = BindingStatus.PROPOSED
    #: 回执回声（apply 后填充；fail-closed：未 applied 绝不携带成功回执）。
    mutation_id: Optional[str] = Field(None, max_length=128)
    mutation_revision: Optional[int] = Field(None, ge=0)
    error_code: Optional[str] = Field(None, max_length=64)
    disclosures: List[str] = Field(default_factory=list, max_length=8)

    def with_status(
        self,
        status: BindingStatus,
        *,
        mutation_id: Optional[str] = None,
        mutation_revision: Optional[int] = None,
        error_code: Optional[str] = None,
        disclosures: Optional[List[str]] = None,
    ) -> "RepresentationBinding":
        """终态拷贝（纯函数；原 binding 不变）。"""
        return self.model_copy(update={
            "status": status,
            "mutation_id": mutation_id,
            "mutation_revision": mutation_revision,
            "error_code": error_code,
            "disclosures": (self.disclosures if disclosures is None else disclosures)[:8],
        })

    def engine_mutation_id(self) -> str:
        """引擎幂等键（≤128，与 envelope `_MUTATION_ID_MAX` 同界）：
        同一 requirement 语义重放命中引擎 dedup，不二次落盘。"""
        return f"rb:{self.binding_fingerprint}"

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")
