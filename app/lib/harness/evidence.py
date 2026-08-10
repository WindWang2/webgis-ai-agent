"""Unified evaluation evidence model for the Harness ↔ GIS ↔ MapSpec ↔ Cartography loop.

This module defines the structured evidence that flows across the whole chain.
It replaces the legacy "didn't-error = success" booleans with a tiered,
evidence-backed model where MISSING EVIDENCE IS NEVER SUCCESS.

核心不变量：
- 缺失证据 → ``NOT_EVALUATED`` / ``UNKNOWN``，绝不被当作 success。
- MapSpecValidity 是分层证据（mutation accepted → semantic valid → compile valid
  → runtime valid），不压成一个假 boolean。
- CursorResolution 来自真实 SessionStore 解析（存在 + 归属 session + 类型匹配），
  不是 ref 字符串前缀检查。
- 每条证据携带完整 correlation（run/session/turn/tool_call/mapspec revision/
  checkpoint/compile/runtime），并发 session 不互相污染。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class RefResolutionStatus(str, Enum):
    """Ref cursor 解析的真实状态（非布尔）。"""
    MALFORMED = "malformed"                 # 不符合 ref:<type>:<id> 语法
    SYNTACTICALLY_VALID = "syntactically_valid"  # 语法合法但未解析
    NOT_FOUND = "not_found"                 # 解析过，session 内不存在
    WRONG_SESSION = "wrong_session"         # 存在但归属其它 session
    TYPE_MISMATCH = "type_mismatch"         # 存在但 payload 类型与 ref 前缀不符
    RESOLVED = "resolved"                   # 存在 + 归属正确 session + 类型匹配

    @property
    def is_resolved(self) -> bool:
        return self is RefResolutionStatus.RESOLVED


class MapSpecValidityTier(int, Enum):
    """MapSpec 有效性的分层证据（值越大证据越强）。

    缺失证据为 NOT_EVALUATED（0），绝不为 success。
    """
    NOT_EVALUATED = 0       # 未采集到任何证据
    MUTATION_REJECTED = 1   # mutation 被校验拒绝（transaction 回滚）
    MUTATION_ACCEPTED = 2   # 工具未报错（最弱证据——"没崩"≠"有效"）
    SEMANTIC_VALID = 3      # 通过纯 Python 结构校验 validate()
    COMPILE_VALID = 4       # 通过 TS 编译器（compile-report success）
    RUNTIME_VALID = 5       # 通过 headless 运行时（mapLoaded + 无错误 + 非空 canvas）


@dataclass
class RefResolution:
    """单次 ref cursor 解析的真实结果。"""
    ref: str
    session_id: Optional[str]
    status: RefResolutionStatus = RefResolutionStatus.SYNTACTICALLY_VALID
    expected_type: Optional[str] = None   # ref 前缀声明的类型 (geojson/raster/table)
    actual_type: Optional[str] = None     # payload 实际类型
    detail: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.status.is_resolved


@dataclass
class MapSpecValidityEvidence:
    """一次 MapSpec mutation 的分层有效性证据。"""
    tier: MapSpecValidityTier = MapSpecValidityTier.NOT_EVALUATED
    # 各层原始证据（可观察，不静默）
    mutation_accepted: Optional[bool] = None
    semantic_errors: List[Dict[str, Any]] = field(default_factory=list)
    compile_success: Optional[bool] = None
    compile_errors: List[Dict[str, Any]] = field(default_factory=list)
    runtime_valid: Optional[bool] = None
    runtime_fatal_error: Optional[str] = None
    mapspec_revision: Optional[str] = None
    checkpoint_id: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        """有效 = 至少达到 SEMANTIC_VALID（真实证据），MUTATION_ACCEPTED 不算有效。"""
        return self.tier >= MapSpecValidityTier.SEMANTIC_VALID

    @property
    def evaluated(self) -> bool:
        return self.tier is not MapSpecValidityTier.NOT_EVALUATED


@dataclass
class ToolCallEvidence:
    """单次工具调用的完整 correlation + 证据记录。

    每条记录独立携带 run/session/turn/tool_call 标识，避免并发 session 互相污染。
    """
    run_id: str
    session_id: str
    turn_id: str
    tool_call_id: str
    tool_name: str
    duration_ms: int = 0
    is_error: bool = False
    error_msg: str = ""
    # ref 解析证据（真实 SessionStore 解析）
    ref_resolutions: List[RefResolution] = field(default_factory=list)
    # MapSpec 有效性分层证据（仅 mutation 工具）
    mapspec_validity: Optional[MapSpecValidityEvidence] = None
    # 运行时制图证据路径（screenshot/trace/report，可选）
    runtime_evidence_path: Optional[str] = None


@dataclass
class EvaluationRun:
    """一次评估 run 的 session-scoped 证据集合。"""
    run_id: str
    session_id: str
    expected_tools: List[str] = field(default_factory=list)
    ideal_step_count: int = 0
    evidence: List[ToolCallEvidence] = field(default_factory=list)

    def add(self, ev: ToolCallEvidence) -> None:
        self.evidence.append(ev)
