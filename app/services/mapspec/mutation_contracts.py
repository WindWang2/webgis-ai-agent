"""MapSpec mutation 边界契约（H02 解巨石 — 单一事实源）。

结果 Domain 值对象（MapSpecResult / MapSpecBatchResult / BatchIntentOutcome）
与 origin 词表的原生定义点；lifecycle_engine 原样 re-export 保持既有
import 面零破坏。叶子模块：不依赖任何 service（数据类纯定义）。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

MutationOrigin = Literal["agent", "user", "system"]


@dataclass
class MapSpecResult:
    """MapSpec 意图变迁统一结果 Domain 值对象"""

    mapspec: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    is_compiled: bool = False
    checkpoint_id: Optional[str] = None
    ref_count: int = 0
    is_error: bool = False
    error_msg: str = ""
    correction_hint: str = ""
    # ADR-0078: deterministic cartography-semantic findings (paint ↔ legend
    # equivalence, cardinality, domain coverage, no-data, …). Structural
    # validity (is_compiled) ≠ thematic correctness — these findings are the
    # evidence the Harness surfaces so "structurally valid but legend/paint
    # drift" is detectable. Checks needing a source profile are NOT_EVALUATED.
    cartography_findings: List[Dict[str, Any]] = field(default_factory=list)
    # Desired-state cartographic review is intentionally separate from
    # ``is_compiled``. A structurally valid mutation may still have a failed or
    # not-evaluated quality review, and neither implies frontend convergence.
    cartographic_review: Optional[Dict[str, Any]] = None
    mapspec_fingerprint: Optional[str] = None
    # Latest frontend observation already present when this mutation began.
    # A runtime snapshot must carry a strictly newer sequence to certify it.
    runtime_observation_seq: int = 0
    # 锁拒绝载荷（单码契约的区分面：码唯一，id 列表指明被锁目标）。
    locked_layer_ids: List[str] = field(default_factory=list)
    locked_component_ids: List[str] = field(default_factory=list)
    # Monotonic session revision assigned while holding the distributed
    # lifecycle lock. Durable harness context uses it to reject late writes.
    mutation_revision: int = 0
    origin: Optional[MutationOrigin] = None
    # Stale expected_revision: not a validation error and not a commit.
    superseded: bool = False
    # 机器可读精确错误码（单码契约：组件锁复用 layer_locked，载荷
    # locked_*_ids 区分；非错误时为 None）。
    # #1220（audit3 C-6）：此前重复声明（str "" 与 Optional[str] None
    # 并存，第二声明胜出）—— 单一权威声明。
    error_code: Optional[str] = None
    # 方向 8（ADR-0183）：突变信封回声 —— 幂等键与优先级分类。
    # duplicate=True：同 mutation_id 已在此前世代提交过；本次按幂等重放处理
    # （返回存证 revision + 当前权威 spec，不重复执行操作、不递增 revision）。
    mutation_id: Optional[str] = None
    producer_class: Optional[str] = None
    duplicate: bool = False

    def to_dict(self) -> Dict[str, Any]:
        if self.duplicate:
            return {
                "success": True,
                "duplicate": True,
                "message": "Duplicate mutation_id; already committed at the recorded revision.",
                "mutation_revision": self.mutation_revision,
                "mapspec": self.mapspec,
                **({"origin": self.origin} if self.origin is not None else {}),
                **({"mutation_id": self.mutation_id} if self.mutation_id else {}),
                **(
                    {"producer_class": self.producer_class}
                    if self.producer_class
                    else {}
                ),
            }
        if self.superseded:
            res = {
                "success": False,
                "status": "superseded",
                "message": self.error_msg,
                "mutation_revision": self.mutation_revision,
                "mapspec": self.mapspec,
            }
            if self.origin is not None:
                res["origin"] = self.origin
            if self.correction_hint:
                res["correction_hint"] = self.correction_hint
            if self.error_code:
                res["error_code"] = self.error_code
            return res
        if self.is_error:
            res = {"success": False, "message": self.error_msg}
            if self.locked_layer_ids:
                res["locked_layer_ids"] = list(self.locked_layer_ids)
            if self.locked_component_ids:
                res["locked_component_ids"] = list(self.locked_component_ids)
            if self.origin is not None:
                res["origin"] = self.origin
            if self.correction_hint:
                res["correction_hint"] = self.correction_hint
            if self.error_code:
                res["error_code"] = self.error_code
            return res
        res = {
            "success": True,
            "mapspec": self.mapspec,
            "warnings": self.warnings,
            "is_compiled": self.is_compiled,
            "checkpoint_id": self.checkpoint_id,
            "cartography_findings": self.cartography_findings,
            "cartographic_review": self.cartographic_review,
            "mapspec_fingerprint": self.mapspec_fingerprint,
            "runtime_observation_seq": self.runtime_observation_seq,
            "mutation_revision": self.mutation_revision,
        }
        if self.origin is not None:
            res["origin"] = self.origin
        if self.mutation_id:
            res["mutation_id"] = self.mutation_id
        if self.producer_class:
            res["producer_class"] = self.producer_class
        return res


@dataclass
class BatchIntentOutcome:
    """GISMutationBatch 中单个 intent 的裁决（applied / refused / not_found）。"""

    layer_id: str
    status: str
    visible: Optional[bool] = None
    error_msg: Optional[str] = None
    # review R2 MAJOR-2：锁拒绝的机器可读码（单码契约 LOCK_CONFLICT_CODE；
    # 非锁 refused / applied / not_found 留空 —— 码只断言锁冲突一种语义）。
    error_code: Optional[str] = None


@dataclass
class MapSpecBatchResult:
    """GISMutationBatch 统一结果：一次锁/一次读/一次校验/一次 revision+1。

    v2(Phase 7)：finalize_display 等收口此前逐层 apply_gis_mutation ——
    N 层 = N 个完整事务（N 次锁循环 + N 次 checkpoint（每次物化全部 ref）
    + N 次 revision 递增 + N×4 次全量 parse），既是性能根因也是 409 风暴
    根因。batch 把 N 个 presentation patch 合并为一个事务；refused/
    not_found 的 intent 被跳过并逐项上报，不影响其余 intent 提交。
    """

    mapspec: Optional[Dict[str, Any]] = None
    outcomes: List[BatchIntentOutcome] = field(default_factory=list)
    applied_count: int = 0
    refused_count: int = 0
    not_found_count: int = 0
    mutation_revision: int = 0
    is_error: bool = False
    error_msg: str = ""
    correction_hint: str = ""
    superseded: bool = False
    origin: Optional[MutationOrigin] = None
    checkpoint_id: Optional[str] = None
    mapspec_fingerprint: Optional[str] = None
    cartographic_review: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    # 方向 8（ADR-0183）：信封回声（同 MapSpecResult）。
    mutation_id: Optional[str] = None
    producer_class: Optional[str] = None
    duplicate: bool = False

    @property
    def committed(self) -> bool:
        """至少一个 intent 落盘（revision 已递增）。"""
        return self.applied_count > 0 and not self.is_error and not self.superseded

    def to_dict(self) -> Dict[str, Any]:
        res = {
            "mapspec": self.mapspec,
            "outcomes": [
                {
                    "layer_id": o.layer_id,
                    "status": o.status,
                    "visible": o.visible,
                    "error_msg": o.error_msg,
                    # review R2 MAJOR-2：锁拒绝码随项透出（调用方机器判定）。
                    "error_code": o.error_code,
                }
                for o in self.outcomes
            ],
            "applied_count": self.applied_count,
            "refused_count": self.refused_count,
            "not_found_count": self.not_found_count,
            "mutation_revision": self.mutation_revision,
            "is_error": self.is_error,
            "error_msg": self.error_msg,
            "correction_hint": self.correction_hint,
            "superseded": self.superseded,
            "committed": self.committed,
        }
        if self.mutation_id:
            res["mutation_id"] = self.mutation_id
        if self.producer_class:
            res["producer_class"] = self.producer_class
        if self.duplicate:
            res["duplicate"] = True
        return res
