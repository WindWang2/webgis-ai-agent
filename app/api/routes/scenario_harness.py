"""情境化多方案候选工作台 API（G05 WP5 服务面）。

与 Pi 工具面（``app/tools/scenario_harness_tools.py``）**完全同链**：同一
scenario_harness 服务函数、同一锁/CAS/user-wins 守卫 —— UI 采纳不因走
HTTP 而绕过任何检查。鉴权同 situation_views 族（require_owned_session）。

路由面（全部有界响应）：
- GET  /chat/sessions/{sid}/scenario/overview          只读总览
- GET  /chat/sessions/{sid}/scenario/compare           只读对比
- POST /chat/sessions/{sid}/scenario/candidates        提案（用户试验）
- POST /chat/sessions/{sid}/scenario/candidates/{cid}/execute    分支干预
- POST /chat/sessions/{sid}/scenario/candidates/{cid}/adopt      选择性采纳
- DELETE /chat/sessions/{sid}/scenario/candidates/{cid}          放弃/归档
- GET  /chat/sessions/{sid}/scenario/locks             活跃锁定
- POST /chat/sessions/{sid}/scenario/locks             用户锁定
- DELETE /chat/sessions/{sid}/scenario/locks/{lock_id} 用户释放（仅 user）
"""
from __future__ import annotations

import logging
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services.auth_history_bridge import require_owned_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["AI对话"])


def _typed_error_status(code: str) -> int:
    """机器码 → HTTP 状态（子串匹配；400 兜底）。"""
    if "limit" in code or "superseded" in code or "conflict" in code or "exists" in code:
        return 409
    if "not_found" in code or "missing" in code:
        return 404
    if "forbidden" in code:
        return 403
    return 400


def _http_error(exc: Exception) -> HTTPException:
    # RegistryError 与 whatif BranchError 共享 (code, message) 形状 ——
    # 统一映射，分支预算/撞车不再 500（review P2-7）。
    if hasattr(exc, "code") and hasattr(exc, "message"):
        code = str(exc.code)
        return HTTPException(
            status_code=_typed_error_status(code),
            detail={"error": code, "message": str(exc.message)},
        )
    raise exc


class ScenarioCandidateProposal(BaseModel):
    """用户从工作台发起的候选提案（与 Agent 工具同 schema）。"""

    title: str = Field(default="", max_length=80)
    hypothesis: str = Field(default="", max_length=200)
    intents: List[dict] = Field(default_factory=list, max_length=24)


class ScenarioExecuteRequest(BaseModel):
    intents: List[dict] = Field(default_factory=list, max_length=24)


class ScenarioAdoptRequest(BaseModel):
    mode: Literal["all", "groups", "seqs"] = "all"
    groups: List[Literal["analysis", "style", "layout", "component", "data"]] = Field(
        default_factory=list, max_length=5
    )
    seqs: List[int] = Field(default_factory=list, max_length=24)
    expected_revision: Optional[int] = None


class ScenarioLockRequest(BaseModel):
    kind: Literal["layer_field", "component_field", "analysis_param"]
    target_id: str = Field(default="", max_length=120)
    field_paths: List[str] = Field(default_factory=list, max_length=16)
    observed_value: str = Field(default="", max_length=200)
    reason: str = Field(default="", max_length=160)


@router.get("/sessions/{session_id}/scenario/overview", response_model=dict[str, object])
async def get_scenario_overview(
    session_id: str,
    _conv: Any = Depends(require_owned_session),
) -> dict[str, object]:
    """候选总览：候选清单 + 共享基准 + 活跃锁定（只读派生）。"""
    from app.services.scenario_harness.candidates import list_candidates
    from app.services.scenario_harness.locks import list_active_locks
    from app.services.scenario_harness.projection import scenario_situation_value

    from app.services.scenario_harness.candidates import candidate_summary

    value, corrupt = await scenario_situation_value(session_id)
    candidates = [
        candidate_summary(c) for c in await list_candidates(session_id)
    ]
    locks = [lk.model_dump(mode="json") for lk in await list_active_locks(session_id)]
    out: dict[str, object] = {
        "schema_version": "scenario_overview.v1",
        "overview": value,
        "candidates": candidates,
        "active_locks": locks,
    }
    if corrupt:
        out["corrupt_entries"] = corrupt[:8]
    return out


@router.get("/sessions/{session_id}/scenario/compare", response_model=dict[str, object])
async def get_scenario_compare(
    session_id: str,
    candidate_ids: str = "",
    _conv: Any = Depends(require_owned_session),
) -> dict[str, object]:
    """多候选结构化对比 + 基准漂移影响分级（candidate_ids 逗号分隔；空 = 全部活跃）。"""
    from app.services.scenario_harness.candidates import list_candidates
    from app.services.scenario_harness.store import RegistryError

    ids = [s.strip() for s in (candidate_ids or "").split(",") if s.strip()]
    if not ids:
        ids = [
            c.candidate_id for c in await list_candidates(session_id)
            if c.state in ("comparable", "stale", "draft", "running")
        ]
    if not ids:
        return {"schema_version": "scenario_compare.v1", "result": None, "reason": "no_candidates"}
    # compare 模块按需导入（无活跃候选路径不依赖它）
    from app.services.scenario_harness.compare import compare_candidates

    try:
        result = await compare_candidates(session_id, ids)
    except RegistryError as exc:
        raise _http_error(exc) from exc
    return {"schema_version": "scenario_compare.v1", "result": result.model_dump(mode="json")}


@router.post("/sessions/{session_id}/scenario/candidates", response_model=dict[str, object])
async def post_scenario_candidate(
    session_id: str,
    body: ScenarioCandidateProposal,
    _conv: Any = Depends(require_owned_session),
) -> dict[str, object]:
    """用户从工作台发起候选提案（与 Agent 工具同链同守卫）。"""
    from app.services.scenario_harness.candidates import propose_candidate
    from app.services.scenario_harness.intents_codec import DeltaCodecError
    from app.services.scenario_harness.store import RegistryError
    from app.tools.scenario_harness_tools import _intents_from_dicts

    try:
        typed = _intents_from_dicts(body.intents)
        candidate = await propose_candidate(
            session_id,
            title=body.title,
            hypothesis=body.hypothesis,
            intents=typed,
            created_by="user",
        )
    except (RegistryError, DeltaCodecError) as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_intents", "message": str(exc)}) from exc
    return {"schema_version": "scenario_candidate.v1", "candidate": candidate.model_dump(mode="json")}


@router.post(
    "/sessions/{session_id}/scenario/candidates/{candidate_id}/execute",
    response_model=dict[str, object],
)
async def post_scenario_execute(
    session_id: str,
    candidate_id: str,
    body: ScenarioExecuteRequest,
    _conv: Any = Depends(require_owned_session),
) -> dict[str, object]:
    """对候选分支施加干预（零父会话突变；锁拦截逐条披露）。"""
    from app.services.scenario_harness.candidates import apply_candidate_interventions
    from app.services.scenario_harness.intents_codec import DeltaCodecError, serialize_intents
    from app.services.scenario_harness.store import RegistryError
    from app.tools.scenario_harness_tools import _intents_from_dicts

    try:
        typed = _intents_from_dicts(body.intents)
        deltas = serialize_intents(typed) if typed else None
        results = await apply_candidate_interventions(session_id, candidate_id, deltas=deltas)
    except (RegistryError, DeltaCodecError) as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_intents", "message": str(exc)}) from exc
    return {"schema_version": "scenario_execute.v1", "results": results}


@router.post(
    "/sessions/{session_id}/scenario/candidates/{candidate_id}/adopt",
    response_model=dict[str, object],
)
async def post_scenario_adopt(
    session_id: str,
    candidate_id: str,
    body: ScenarioAdoptRequest,
    _conv: Any = Depends(require_owned_session),
) -> dict[str, object]:
    """选择性采纳（用户决议；与 Pi 工具同链，CAS + user-wins + 锁检查）。"""
    from app.services.scenario_harness.adopt import adopt_candidate
    from app.services.scenario_harness.contracts import AdoptSelection
    from app.services.scenario_harness.store import RegistryError

    selection = AdoptSelection(
        mode=body.mode,
        groups=body.groups,
        seqs=body.seqs,
        expected_revision=body.expected_revision,
    )
    try:
        receipt = await adopt_candidate(
            session_id, candidate_id, selection,
            origin="user", actor="user_workbench",
        )
    except RegistryError as exc:
        raise _http_error(exc) from exc
    return {"schema_version": "scenario_adoption.v1", "receipt": receipt.model_dump(mode="json")}


@router.post(
    "/sessions/{session_id}/scenario/adoptions/{adoption_id}/revert",
    response_model=dict[str, object],
)
async def post_scenario_revert(
    session_id: str,
    adoption_id: str,
    allow_drift: bool = False,
    _conv: Any = Depends(require_owned_session),
) -> dict[str, object]:
    """回退采纳（用户决议；漂移默认拒绝并披露丢弃区间）。"""
    from app.services.scenario_harness.adopt import revert_adoption
    from app.services.scenario_harness.store import RegistryError

    try:
        receipt = await revert_adoption(
            session_id, adoption_id,
            origin="user", actor="user_workbench",
            allow_drift=allow_drift,
        )
    except RegistryError as exc:
        raise _http_error(exc) from exc
    return {"schema_version": "scenario_adoption.v1", "receipt": receipt.model_dump(mode="json")}


@router.delete("/sessions/{session_id}/scenario/candidates/{candidate_id}", response_model=dict[str, object])
async def delete_scenario_candidate(
    session_id: str,
    candidate_id: str,
    hard_delete: bool = True,
    reason: str = "",
    _conv: Any = Depends(require_owned_session),
) -> dict[str, object]:
    """放弃候选（hard_delete 联动分支回收；已采纳候选需走回退）。"""
    from app.services.scenario_harness.candidates import (
        abandon_candidate,
        get_candidate,
    )
    from app.services.scenario_harness.store import RegistryError

    try:
        existing = await get_candidate(session_id, candidate_id)
        if existing.state == "adopted":
            raise RegistryError("abandon_adopted", "已采纳候选不可放弃（走回退）")
        removed = await abandon_candidate(
            session_id, candidate_id, reason=reason, hard_delete=hard_delete,
        )
    except RegistryError as exc:
        raise _http_error(exc) from exc
    return {"schema_version": "scenario_abandon.v1", "removed": removed}


@router.get("/sessions/{session_id}/scenario/locks", response_model=dict[str, object])
async def get_scenario_locks(
    session_id: str,
    _conv: Any = Depends(require_owned_session),
) -> dict[str, object]:
    """活跃锁定清单（released 不返回；保留在环内审计）。"""
    from app.services.scenario_harness.locks import list_active_locks

    locks = [lk.model_dump(mode="json") for lk in await list_active_locks(session_id)]
    return {"schema_version": "scenario_locks.v1", "locks": locks}


@router.post("/sessions/{session_id}/scenario/locks", response_model=dict[str, object])
async def post_scenario_lock(
    session_id: str,
    body: ScenarioLockRequest,
    _conv: Any = Depends(require_owned_session),
) -> dict[str, object]:
    """用户锁定（幂等；同 (kind,target,fields) 返回既有锁）。"""
    from app.services.scenario_harness.locks import set_lock
    from app.services.scenario_harness.store import RegistryError

    try:
        lock = await set_lock(
            session_id,
            kind=body.kind,
            target_id=body.target_id,
            field_paths=body.field_paths,
            observed_value=body.observed_value,
            reason=body.reason,
        )
    except RegistryError as exc:
        raise _http_error(exc) from exc
    return {"schema_version": "scenario_lock.v1", "lock": lock.model_dump(mode="json")}


@router.delete("/sessions/{session_id}/scenario/locks/{lock_id}", response_model=dict[str, object])
async def delete_scenario_lock(
    session_id: str,
    lock_id: str,
    reason: str = "",
    _conv: Any = Depends(require_owned_session),
) -> dict[str, object]:
    """用户释放锁定（保留审计；Agent 无权释放）。"""
    from app.services.scenario_harness.locks import release_lock
    from app.services.scenario_harness.store import RegistryError

    try:
        lock = await release_lock(session_id, lock_id, reason=reason, origin="user")
    except RegistryError as exc:
        raise _http_error(exc) from exc
    return {"schema_version": "scenario_lock.v1", "lock": lock.model_dump(mode="json")}
