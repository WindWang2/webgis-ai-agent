"""执行计划漂移检测（ADR-0096 D7）：重开持久计划时的语义时效判定。

目标 §10 的红线：持久化计划/产物在新运行时下重开时，必须**检测漂移并
诚实降级**，绝不静默声称精确可复现。

判定状态（对齐既有 ``is_stale_plan`` 的 fail-open 历史兼容语义）：
- ``current``       —— runtime 指纹与计划指纹都一致；
- ``stale_runtime`` —— registry 世代变了（算法/工具语义漂移）→ 只能
  重算或显式接受降级；
- ``degraded_plan`` —— runtime 未变但计划指纹不匹配（spec/参数/数据集
  指纹变化）→ 结果是另一个计划的；
- ``unknown``       —— 空缺/损坏的历史记录（不判 stale，避免升级即全量
  作废；诚实标 not_assessed）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel

from app.services.geocompute.plan import EXECUTION_PLAN_VERSION, ExecutionPlan


class DriftVerdict(BaseModel):
    state: str                     # current | stale_runtime | degraded_plan | unknown
    reason: Optional[str] = None
    stored_plan_fingerprint: Optional[str] = None
    current_plan_fingerprint: Optional[str] = None
    stored_runtime_fingerprint: Optional[str] = None
    current_runtime_fingerprint: Optional[str] = None

    @property
    def reusable(self) -> bool:
        return self.state == "current"

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


def _current_runtime_fingerprint() -> Optional[str]:
    try:
        from app.lib.gis.runtime_manifest import get_runtime_manifest

        return get_runtime_manifest().fingerprint
    except Exception:  # noqa: BLE001 - 指纹不可得时诚实 unknown，绝不伪造
        return None


def build_plan_record(
    plan: ExecutionPlan,
    *,
    runtime_manifest_fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """构造可持久化的计划记录（有界；供产物/审计行携带）。"""
    return {
        "execution_plan_version": EXECUTION_PLAN_VERSION,
        "plan_id": plan.plan_id,
        "plan_fingerprint": plan.graph_fingerprint(),
        "node_fingerprints": {n.node_id: n.semantic_fingerprint() for n in plan.nodes},
        "runtime_manifest_fingerprint": (
            runtime_manifest_fingerprint
            if runtime_manifest_fingerprint is not None
            else _current_runtime_fingerprint()
        ),
    }


def check_plan_drift(
    stored: Optional[Dict[str, Any]],
    *,
    plan: Optional[ExecutionPlan] = None,
    current_runtime_fingerprint: Optional[str] = None,
) -> DriftVerdict:
    """对持久记录做漂移判定。

    ``plan`` 给出时按图指纹重建比较；``current_runtime_fingerprint``
    可注入（测试/调用方已知世代时避免触发编译）。
    """
    current_rt = current_runtime_fingerprint
    if current_rt is None:
        current_rt = _current_runtime_fingerprint()

    if not isinstance(stored, dict):
        return DriftVerdict(state="unknown", reason="no persisted plan record")
    stored_plan_fp = stored.get("plan_fingerprint")
    stored_rt_fp = stored.get("runtime_manifest_fingerprint")
    if not stored_plan_fp or not isinstance(stored_plan_fp, str):
        return DriftVerdict(
            state="unknown",
            reason="stored record lacks plan fingerprint (legacy record)",
            stored_runtime_fingerprint=_safe_str(stored_rt_fp),
            current_runtime_fingerprint=current_rt,
        )

    if stored_rt_fp and current_rt and stored_rt_fp != current_rt:
        return DriftVerdict(
            state="stale_runtime",
            reason="registry semantics changed since this plan was persisted; "
                   "recomputation required for reproducible outputs",
            stored_plan_fingerprint=stored_plan_fp,
            stored_runtime_fingerprint=str(stored_rt_fp),
            current_runtime_fingerprint=current_rt,
        )

    current_plan_fp = plan.graph_fingerprint() if plan is not None else None
    if plan is not None and current_plan_fp != stored_plan_fp:
        return DriftVerdict(
            state="degraded_plan",
            reason="persisted fingerprint does not match the rebuilt plan "
                   "(spec/parameters/dataset fingerprints changed)",
            stored_plan_fingerprint=stored_plan_fp,
            current_plan_fingerprint=current_plan_fp,
            stored_runtime_fingerprint=_safe_str(stored_rt_fp),
            current_runtime_fingerprint=current_rt,
        )

    return DriftVerdict(
        state="current",
        stored_plan_fingerprint=stored_plan_fp,
        current_plan_fingerprint=current_plan_fp,
        stored_runtime_fingerprint=_safe_str(stored_rt_fp),
        current_runtime_fingerprint=current_rt,
    )


def assert_reusable(
    stored: Optional[Dict[str, Any]],
    *,
    plan: Optional[ExecutionPlan] = None,
    current_runtime_fingerprint: Optional[str] = None,
) -> DriftVerdict:
    """可复用性守卫：stale/degraded 抛类型化错误（绝不静默复现）。"""
    from app.services.geocompute.errors import GeoComputeError

    verdict = check_plan_drift(
        stored, plan=plan, current_runtime_fingerprint=current_runtime_fingerprint
    )
    if verdict.state in ("stale_runtime", "degraded_plan"):
        raise GeoComputeError(
            f"persisted plan is {verdict.state}: {verdict.reason}",
            details=verdict.to_dict(),
        )
    return verdict


def _safe_str(v: Any) -> Optional[str]:
    return str(v) if v is not None else None
