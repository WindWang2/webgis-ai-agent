"""WP4/R1 · 供给计划执行服务（生产数据面单管线桥接）。

R1 评审修复（B-P1-1/B-P1-2/B-P1-4、A-P1-2、C-P1-1/C-P3-1）：
- **投影不再丢弃**：``ads_request``（bbox/time_range/columns/sample_rate）
  投影为既有 ``QuerySpec`` 后传给 ``materialize_catalog_item``——
  绝不静默回退到下游默认 ``limit=500`` 全量采样；
- **fabric id 空间桥接**：联邦目录候选（``fabric:<dataset_id>``）按
  ``sync_catalog`` 的既有 id 约定解析回 ``CatalogItemModel``（只读查询，
  不复制第二套 id 体系；解析不到 → 类型化失败 + 诚实披露）；
- **租户授权**：物化前对 CatalogItemModel 的 DataSource 做
  ``_require_tenant_owned`` 同语义租户校验（REST ``_authorize_catalog_item``
  的工具侧等价物——404 语义不泄漏跨租户存在性）；
- **幂等**：已 completed/partial 的步骤跳过不重跑（重复请求不重复付费）；
  计划级 executing 状态做单进程并发抢占；
- 错误文本统一截断，不泄漏内部细节。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.task_data_supply.supply_plan import (
    SupplyPlan,
    SupplyPlanner,
    SupplyStep,
)

logger = logging.getLogger(__name__)

#: 执行面获取行数上限（有界；超出部分在回执中如实披露截断）。
_EXECUTE_MAX_ROWS = 50_000


def _caller_identity() -> Tuple[Optional[str], Optional[str]]:
    """(user_id, org_id) from the tool execution context（data_discovery 同款）。"""
    from app.utils.best_effort import best_effort

    with best_effort(
        "task-supply-caller-identity",
        "tools-task-data-supply-caller-identity-best-effort",
    ):
        from app.services.provenance.context import get_tool_execution_context

        ctx = get_tool_execution_context()
        if ctx is not None:
            # 归一 str（DataSourceModel.owner_id 为 str；ctx 侧可能为 int）
            uid = ctx.user_id
            oid = ctx.org_id
            return (str(uid) if uid is not None else None,
                    str(oid) if oid is not None else None)
    return None, None


def _resolve_fabric_catalog_item(db, candidate_id: str, source_ref: str):
    """联邦目录候选 → CatalogItemModel（sync_catalog 既有 id 约定，只读）。

    sync 落库 id 形如 ``cat_{source_id}_{name}``（name 含 ``.``/``/`` 时
    追加 ``_<sha16>``）；联邦目录候选 id 是 ``fabric:<dataset_id>``，
    ``source_ref`` 携带 source_id。解析不到 → None（调用方类型化失败，
    不猜第四种 id 形态）。
    """
    from app.models.data_fabric import CatalogItemModel

    dataset_id = candidate_id[len("fabric:"):] if candidate_id.startswith(
        "fabric:") else candidate_id
    tried: List[str] = []
    if source_ref:
        base = f"cat_{source_ref}_{dataset_id}".replace(".", "_").replace(
            "/", "_")
        if "." in dataset_id or "/" in dataset_id:
            base += "_" + hashlib.sha256(
                dataset_id.encode("utf-8")).hexdigest()[:16]
        tried.append(base)
    tried.append(dataset_id)  # 退化：候选 id 本身已是 CatalogItemModel id
    for cid in tried:
        row = db.query(CatalogItemModel).filter(
            CatalogItemModel.id == cid).first()
        if row is not None:
            return row
    return None


def _tenant_owns_source(source, caller_user_id: Optional[str],
                        caller_org_id: Optional[str]) -> bool:
    """与 data_fabric._require_tenant_owned 同语义（布尔化）：

    匿名 → 只允许 org/owner 双空的全局源；有 org → 同 org / 本人 /
    全局源；无 org → 本人 / 全局源。不满足 → False（调用方 404 语义）。
    """
    if source is None:
        return False
    if caller_user_id is None:
        return source.org_id is None and source.owner_id is None
    if caller_org_id is not None:
        return (source.org_id == caller_org_id
                or source.owner_id == caller_user_id
                or (source.org_id is None and source.owner_id is None))
    return (source.owner_id == caller_user_id
            or (source.org_id is None and source.owner_id is None))


def _query_spec_from_ads(ads_request: Dict[str, Any]):
    """计划投影 → 既有 QuerySpec（bbox/time/columns 逐字段下推）。

    ``limit`` 取有界执行帽（不静默 500 采样）；跨线 bbox 不在此拆分——
    QuerySpec 校验拒绝时按类型化失败如实上报（计划层已披露）。
    """
    from app.schemas.data_fabric_schema import QuerySpec

    bbox = ads_request.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4 and bbox[0] > bbox[2]:
        bbox = None  # 跨线形状对单 bbox 下推不可解析——保持 None 并披露
    return QuerySpec(
        bbox=bbox if isinstance(bbox, list) and len(bbox) == 4 else None,
        columns=[str(c) for c in (ads_request.get("columns") or [])][:16]
        or None,
        datetime_range=(
            [str(t) for t in tr] if (tr := ads_request.get("time_range"))
            else None),
        limit=_EXECUTE_MAX_ROWS,
    )


async def execute_plan_steps(
    session_id: str,
    plan: SupplyPlan,
    *,
    roles: Optional[set] = None,
    planner: Optional[SupplyPlanner] = None,
    caller_identity: Optional[Tuple[Optional[str], Optional[str]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """执行计划步骤（幂等）：返回 (results, skipped)。

    幂等语义（R1 评审 B P1-4 + R2 评审 B 修正）：已 completed/partial
    的步骤跳过不重跑（回执 skipped 可见）；并发互斥用 ``plan.in_flight``
    旗标（不再复用 state 字段——roles 过滤执行曾把计划楔死在 executing，
    R2-B 运行时复现）。``caller_identity``（R2 评审 C P1）：REST 入口
    显式传入 ``(user_id, org_id)``——工具执行上下文只在 agent 链路绑定，
    REST 不传会对非全局 fabric 源 fail-closed。
    """
    planner = planner or SupplyPlanner()
    if plan.in_flight:
        return [{"role": "*", "outcome": "failed",
                 "code": "TASK_SUPPLY_PLAN_BUSY",
                 "error": "计划正在执行中（并发抢占）"}], []
    if plan.state == "requires_confirmation":
        return [{"role": "*", "outcome": "failed",
                 "code": "TASK_SUPPLY_PLAN_NEEDS_CONFIRMATION",
                 "error": "存在未决策事项"}], []

    plan.in_flight = True
    results: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    try:
        for step in plan.steps:
            if roles and step.role not in roles:
                continue
            if step.outcome in ("completed", "partial"):
                # 幂等：合法产物保留，不重复付费物化
                skipped.append({"role": step.role, "outcome": step.outcome,
                                "receipt_ref": step.receipt_ref[:96]})
                continue
            if step.action not in ("acquire", "use_bound"):
                planner.record_outcome(
                    plan, step.role, outcome="failed",
                    disclosure=(f"步骤动作 {step.action} 不可自动执行"
                                "（需用户上传/澄清/继续搜索）"))
                results.append({"role": step.role, "outcome": "failed",
                                "code": "TASK_SUPPLY_STEP_NOT_EXECUTABLE"})
                continue
            try:
                outcome, receipt, rows, disc = await _execute_step(
                    session_id, step, caller_identity=caller_identity)
                planner.record_outcome(
                    plan, step.role, outcome=outcome, receipt_ref=receipt,
                    rows_written=rows, disclosure=disc)
                results.append({"role": step.role, "outcome": outcome,
                                "receipt_ref": receipt[:96], "rows": rows})
            except Exception as e:  # noqa: BLE001
                planner.record_outcome(
                    plan, step.role, outcome="failed",
                    disclosure=f"物化失败（如实披露）: {str(e)[:200]}")
                results.append({"role": step.role, "outcome": "failed",
                                "code": "TASK_SUPPLY_MATERIALIZATION_FAILED",
                                "error": str(e)[:200]})
    finally:
        plan.in_flight = False
        planner._refresh_state(plan)
    return results, skipped


async def _execute_step(session_id: str, step: SupplyStep,
                        *, caller_identity=None):
    """单步骤真实执行：走既有 MaterializationService 生产数据面。

    - acquire + fabric 通道 → id 解析 + 租户校验 + QuerySpec 投影后
      ``materialize_catalog_item``（REST 与 agent 共用的单管线）；
    - use_bound / local 通道 → 数据已在会话内，确认绑定零获取。
    返回 (outcome, receipt_ref, rows, disclosure)。
    """
    if step.action == "use_bound":
        return ("completed", step.candidate_id, None,
                "已绑定数据直接使用（零获取）")
    cand_channel = (step.channel or "").lower()
    if cand_channel in ("local", ""):
        return ("completed", step.candidate_id, None,
                "会话内已有资产直接使用（零获取）")
    if cand_channel not in ("data_fabric_catalog", "data_fabric_stac"):
        return ("failed", "", None,
                f"通道 {step.channel} 暂无生产物化路径（如实拒绝，"
                "不静默绕道）")

    from app.core.database import SessionLocal
    from app.services.data_fabric.manager import DataFabricManager

    if caller_identity is not None:
        caller_user_id, caller_org_id = caller_identity
    else:
        caller_user_id, caller_org_id = _caller_identity()
    spec = _query_spec_from_ads(step.ads_request)
    disclosure_parts: List[str] = []
    if (step.ads_request.get("bbox") is not None
            and isinstance(step.ads_request["bbox"], list)
            and len(step.ads_request["bbox"]) == 4
            and step.ads_request["bbox"][0] > step.ads_request["bbox"][2]):
        disclosure_parts.append(
            "任务范围跨日期线：单 bbox 下推不可用，本次按无 bbox 限制获取"
            "（结果范围以产物核验为准）")

    with SessionLocal() as db:
        item = _resolve_fabric_catalog_item(
            db, step.candidate_id,
            getattr(step, "source_ref", "") or "")
        if item is None:
            return ("failed", "", None,
                    f"候选 {step.candidate_id} 无法解析到目录条目"
                    "（目录未同步或 id 形态不识别——如实失败）")
        from app.models.data_fabric import DataSourceModel

        src = db.query(DataSourceModel).filter(
            DataSourceModel.id == item.source_id).first()
        if not _tenant_owns_source(src, caller_user_id, caller_org_id):
            # 404 语义：不泄漏跨租户条目存在性（R1 评审 B P1-2）
            return ("failed", "", None,
                    "候选目录条目不可见（无租户权限——如实拒绝）")
        result = await DataFabricManager.materialize_catalog_item(
            db, session_id, item_id=item.id, query_spec=spec)
    ref = str(result.get("ref_id") or result.get("ref") or "")
    # materialize_catalog_item 回执的行数字段是 feature_count（R2-B P3）
    rows = None
    for k in ("feature_count", "rows"):
        v = result.get(k)
        if isinstance(v, int) and v >= 0:
            rows = v
            break
    if not ref:
        return ("failed", "", None,
                f"物化未返回产物 ref（如实失败）: {str(result)[:160]}")
    if rows is not None and rows >= _EXECUTE_MAX_ROWS:
        disclosure_parts.append(
            f"结果达到执行行数上限 {_EXECUTE_MAX_ROWS}——实际为有界截断"
            "（不冒充全量）")
    disclosure = "；".join(disclosure_parts)
    return ("completed", ref, rows, disclosure)


__all__ = ["execute_plan_steps", "_execute_step", "_planner_service"]


def _planner_service() -> SupplyPlanner:
    return SupplyPlanner()
