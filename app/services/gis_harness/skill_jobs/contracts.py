"""G30 技能作业域模型 —— 纯数据 + 闭词表（零 IO、零 LLM、确定性）。

三个身份分别记录（不能互相顶替）：

- **技能资产身份**：skill_id + skill_version + skill_digest（内容指纹）；
  同版本内容被替换 → digest 漂移 → 旧 prepare 失效；
- **参数身份**：值 + 来源（user / skill_default / preset / derived）；
  零值、显式 null 与未提供三态可区分；
- **数据身份**：role → ref + 内容 digest + 事实（几何/数量/字段/CRS）；
  数据变了 → digest 变 → 不能复用旧结果。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

#: prepared call 结构版本（结构变化必须提升）。
PREPARED_SKILL_CALL_SCHEMA = "skilljob.prepared/v1"

#: 参数值来源词表（closed）。
PARAM_SOURCES = ("user", "skill_default", "preset", "derived")

#: prepare 期 typed 拒绝码（closed；启动/运行期错误码见 runner）。
SKILL_JOB_ERROR_CODES = (
    # ── 资产面 ──
    "SKILL_NOT_FOUND",             # id 不存在
    "SKILL_VERSION_MISMATCH",      # 显式版本与库中不符
    "SKILL_DEPRECATED",            # 已弃用（附 deprecated_by 替代）
    "SKILL_GUIDANCE_ONLY",         # 无执行映射 / pack 非 core —— 仅指导
    "SKILL_QUARANTINED",           # 政策面 blocked（如诱导包不可信）
    # ── 参数面 ──
    "PARAM_MISSING",               # 必填参数未提供
    "PARAM_UNKNOWN",               # 未声明的参数（不透传任意键）
    "PARAM_TYPE_INVALID",          # 类型不符
    "PARAM_ENUM_INVALID",          # 枚举外取值
    "PARAM_RANGE_INVALID",         # 数值越界
    "PARAM_UNIT_INVALID",          # 单位词表外
    "PARAM_CONFLICT",              # 参数组合非法（如 rate 缺分母）
    # ── 数据绑定面 ──
    "BINDING_MISSING",             # 必需角色未绑定
    "BINDING_UNKNOWN_ROLE",        # 绑定了契约外的角色
    "BINDING_BAD_SHAPE",           # 同一角色 ref 与 inline 都给/都没给
    "REF_NOT_FOUND",               # ref 在会话存储不可解析
    "DATA_NOT_GEOJSON",            # 绑定数据不是 FeatureCollection
    "GEOMETRY_MISMATCH",           # 几何类别不符（执行面约束）
    "MIN_FEATURES_NOT_MET",        # 要素数不足
    "FIELD_NOT_FOUND",             # field_ref 指向的字段不存在
    "FIELD_TYPE_MISMATCH",         # 字段值类型与 expects 不符
    # ── 能力/工具面 ──
    "CAPABILITY_UNAVAILABLE",      # required+hard 能力不可用（阳性事实）
    "TOOL_NOT_REGISTERED",         # 执行映射的工具未注册
    "TOOL_CAPABILITY_MISMATCH",    # 工具不归属声明能力
    # ── 会话面 ──
    "SESSION_REQUIRED",            # 无会话上下文无法解析 ref
)


class SkillJobErrorCode(Exception):
    """typed prepare/start 拒绝（code ∈ SKILL_JOB_ERROR_CODES + 运行期码）。"""

    def __init__(self, code: str, message: str, *, detail: Optional[Dict[str, Any]] = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


# 兼容别名：调用侧更自然的异常名。
SkillJobError = SkillJobErrorCode


class PreparedParam(BaseModel):
    """已解析参数（值 + 来源；零值/null/未提供三态在 prepare 输入侧区分）。"""
    model_config = ConfigDict(extra="forbid")

    name: str
    value: Any = None
    source: str                   # ⊆ PARAM_SOURCES
    unit: str = ""                # number 参数的 canonical 单位（词表内）


class BindingFacts(BaseModel):
    """绑定数据的真实事实（prepare 期有界抽取；只陈述不推断）。"""
    model_config = ConfigDict(extra="forbid")

    geometry_kind: str = "unknown"    # ⊆ GEOMETRY_KINDS（主导几何类别）
    feature_count: int = 0
    fields: List[str] = Field(default_factory=list, max_length=64)
    crs: str = ""
    content_digest: str = ""          # canonical JSON SHA256（版本竞争核对）
    field_types: Dict[str, str] = Field(default_factory=dict)  # name → numeric|categorical|unknown（有界采样）


class PreparedBinding(BaseModel):
    """已解析数据角色绑定（ref 身份 + 事实；inline 数据在 start 时落 store）。"""
    model_config = ConfigDict(extra="forbid")

    role: str
    ref: str = ""                     # 已在会话存储的 ref（inline 时为空，start 时铸造）
    inline: bool = False              # True = 数据随 prepare 快照传递（有界校验后由调用方在 start 重供）
    facts: BindingFacts = Field(default_factory=BindingFacts)


class PreparedStep(BaseModel):
    """编译后的步骤：工具 + 参数已按闭词表解析（role 叶子在 run 时解析数据）。"""
    model_config = ConfigDict(extra="forbid")

    step_id: str
    tool: str
    capability_id: str = ""           # 该工具归属的能力（prepare 期核对）
    args: Dict[str, Any] = Field(default_factory=dict)  # 叶子可为 {"$role": name} 标记
    produces_evidence: List[str] = Field(default_factory=list)
    required: bool = True


class PreparedSkillCall(BaseModel):
    """prepared call：确定性快照（同输入同 idempotency_key）。"""
    model_config = ConfigDict(extra="forbid")

    schema_version: str = PREPARED_SKILL_CALL_SCHEMA
    prepared_id: str
    session_id: str
    skill_id: str
    skill_version: str
    skill_digest: str
    params: List[PreparedParam] = Field(default_factory=list)
    bindings: List[PreparedBinding] = Field(default_factory=list)
    steps: List[PreparedStep] = Field(default_factory=list)
    result_kind: str = "report"
    idempotency_key: str
    preview: Dict[str, Any] = Field(default_factory=dict)   # 有界计划预览
    warnings: List[str] = Field(default_factory=list, max_length=8)
    prepared_at: str = ""            # ISO 时间（审计面；不入幂等键）

    def param_map(self) -> Dict[str, PreparedParam]:
        return {p.name: p for p in self.params}

    def binding_map(self) -> Dict[str, PreparedBinding]:
        return {b.role: b for b in self.bindings}

    def canonical_payload(self) -> str:
        """幂等键原料：内容 canonical JSON（排除 prepared_id/prepared_at/
        ref（inline 数据以 digest 代表）—— 同配置同数据指纹 = 同键。"""
        payload = {
            "schema_version": self.schema_version,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "skill_digest": self.skill_digest,
            "params": [
                {"name": p.name, "value": p.value, "source": p.source}
                for p in sorted(self.params, key=lambda x: x.name)
            ],
            "bindings": [
                {
                    "role": b.role,
                    "ref": b.ref,
                    "inline": b.inline,
                    "digest": b.facts.content_digest,
                }
                for b in sorted(self.bindings, key=lambda x: x.role)
            ],
            "steps": [
                {"step_id": s.step_id, "tool": s.tool, "args": s.args}
                for s in self.steps
            ],
            "result_kind": self.result_kind,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)

    def compute_idempotency_key(self, owner_fingerprint: str) -> str:
        raw = f"skill_job:{owner_fingerprint}:{self.canonical_payload()}"
        return "skj-" + hashlib.sha256(
            raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:40]
