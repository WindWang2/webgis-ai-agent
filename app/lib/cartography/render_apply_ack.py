"""RenderApplyAck —— desired→apply→ACK 事务语义的 versioned 契约
（F13，ADR-0214 D3）。

前端 reconcile 落定后，此前只有自由文本 ``reconcile_error`` + 有界 error
环 —— per-layer/组件的应用结果、失败归因、部分应用事实没有结构化回流。
本模块定义 ``render_apply_ack.v1``：

    desired（MapSpec，唯一真相） → apply（MapSpecRuntime reconcile）
      → ACK（本契约：事务级 status + per-layer/per-component status +
        封闭 reason code + 部分应用披露）
      → 服务端校验（fail-closed）→ 落库 → findings 派生

信任边界与 stale 语义：

- **词表单一权威在后端**：未知 reason_code 在此被降级为 ``apply_error``
  并记录（前端词表漂移在服务端收敛，不虚构语义、不 422 主链路）；
- **fail-closed 结构校验**：非 dict / 版本不符 / 缺 revision / status
  非法 → 整块拒绝（返回 ``(None, errors)``），绝不部分收编自由文本；
- **stale ACK 不覆盖新状态**：``stamped_revision``（服务端在接受门通过
  后盖章的当前 revision）≠ ACK 自带 revision → ``stale: true``——
  载荷保留披露但 findings 派生必须跳过（见
  :func:`derive_apply_ack_findings`）；
- **有界**：层 ≤64、组件 ≤32、源 ≤64（超限截断 + ``partial_apply.discarded``
  披露）；id ≤64 字符；errors ≤8 条。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.contracts.completion import F_RENDER_APPLY_FAILED, MapCompletionFinding

ACK_SCHEMA_VERSION = "render_apply_ack.v1"

#: 事务级 status（封闭词表）。
ACK_STATUS_APPLIED = "applied"
ACK_STATUS_PARTIAL = "partial"
ACK_STATUS_FAILED = "failed"
ACK_STATUSES = (ACK_STATUS_APPLIED, ACK_STATUS_PARTIAL, ACK_STATUS_FAILED)

#: 条目级 status（封闭词表）。``pending`` = 数据/渲染仍在途（非失败，
#: 非终态 —— 由后续 observation 覆盖）；``skipped`` = 显式弃权
#: （unsupported / user_pending）；``stale`` = 该条目属于旧数据代次
#: （ADR-0225：data revision 对账不匹配 —— 只进诊断，不算当前代成功，
#: 语义对齐 #1618 settle disposition ``stale``）。
ENTRY_APPLIED = "applied"
ENTRY_FAILED = "failed"
ENTRY_SKIPPED = "skipped"
ENTRY_PENDING = "pending"
ENTRY_STALE = "stale"
ENTRY_STATUSES = (
    ENTRY_APPLIED, ENTRY_FAILED, ENTRY_SKIPPED, ENTRY_PENDING, ENTRY_STALE,
)

#: 封闭 reason code 词表（**后端单一权威**；前端镜像漂移在此收敛）。
RC_MISSING_AFTER_APPLY = "missing_after_apply"
RC_UNSUPPORTED_LAYER_TYPE = "unsupported_layer_type"
RC_SOURCE_UNRESOLVED = "source_unresolved"
RC_STYLE_DIVERGED = "style_diverged"
RC_APPLY_ERROR = "apply_error"
RC_USER_PENDING = "user_pending"
RC_BUDGET_DEGRADED = "budget_degraded"
RC_STALE_DATA_REVISION = "stale_data_revision"
REASON_CODES = frozenset({
    RC_MISSING_AFTER_APPLY,
    RC_UNSUPPORTED_LAYER_TYPE,
    RC_SOURCE_UNRESOLVED,
    RC_STYLE_DIVERGED,
    RC_APPLY_ERROR,
    RC_USER_PENDING,
    RC_BUDGET_DEGRADED,
    RC_STALE_DATA_REVISION,
})

#: 有界预算（服务端第二道防线；前端 builder 同值契约）。
MAX_ACK_LAYERS = 64
MAX_ACK_COMPONENTS = 32
#: ADR-0225：per-source 数据面状态条目上限（与信封 data_dependencies 同值）。
MAX_ACK_SOURCES = 64
_MAX_ID = 64
_MAX_REASON = 32
_MAX_ERRORS = 8
_MAX_FINGERPRINT = 64
_MAX_STYLE_EPOCH = 2**31


def _clean_id(value: Any) -> str:
    return str(value or "")[:_MAX_ID]


def validate_render_apply_ack(
    payload: Any,
    *,
    stamped_revision: Optional[int] = None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """结构 + 词表校验 → (normalized | None, errors)。

    fail-closed：形状/版本/revision/status 顶层非法 → ``(None, errors)``
    （调用方按证据缺席降级，绝不落库半信半疑的块）。
    条目级漂移（未知 reason_code）降级 ``apply_error`` 收编 + errors 披露
    —— 词表演进不炸主链路，但语义永远可审计。
    """
    errors: List[str] = []
    if not isinstance(payload, dict):
        return None, ["apply_ack must be an object"]
    if payload.get("schema_version") != ACK_SCHEMA_VERSION:
        return None, [f"unsupported schema_version: {payload.get('schema_version')!r}"]
    rev_raw = payload.get("mapspec_revision")
    if isinstance(rev_raw, bool) or not isinstance(rev_raw, (int, float)) \
            or int(rev_raw) < 0:
        return None, ["mapspec_revision must be a non-negative number"]
    ack_revision = int(rev_raw)
    status = payload.get("status")
    if status not in ACK_STATUSES:
        return None, [f"invalid transaction status: {status!r}"]

    layers: List[Dict[str, Any]] = []
    raw_layers = payload.get("layers")
    if raw_layers is not None and not isinstance(raw_layers, list):
        return None, ["layers must be an array"]
    discarded = 0
    for entry in (raw_layers or []):
        if len(layers) >= MAX_ACK_LAYERS:
            discarded += 1
            continue
        normalized = _validate_entry(entry, errors, "layer")
        if normalized is not None:
            layers.append(normalized)

    components: List[Dict[str, Any]] = []
    raw_components = payload.get("components")
    if raw_components is not None and not isinstance(raw_components, list):
        return None, ["components must be an array"]
    discarded_components = 0
    for entry in (raw_components or []):
        if len(components) >= MAX_ACK_COMPONENTS:
            discarded_components += 1
            continue
        normalized = _validate_entry(entry, errors, "component")
        if normalized is not None:
            components.append(normalized)

    partial_raw = payload.get("partial_apply")
    discarded_total = discarded + discarded_components
    if isinstance(partial_raw, dict) and isinstance(
        partial_raw.get("discarded"), int
    ) and not isinstance(partial_raw.get("discarded"), bool):
        discarded_total += max(0, int(partial_raw["discarded"]))

    # ADR-0225：work echo 块（additive，可选）—— ACK 绑定的执行信封身份。
    # 形状非法 → 条目级跳过（不整块拒绝：旧客户端无此块照常）。
    work_block = _validate_work_block(payload.get("work"), errors)

    # ADR-0225：per-source 数据面状态（additive，可选）—— applied spec
    # 声明的源 revision（reconcile 落定面）与信封依赖的对账证据。条目级
    # 容错（非法条目跳过 + errors 披露）。
    sources: List[Dict[str, Any]] = []
    raw_sources = payload.get("sources")
    if raw_sources is not None and not isinstance(raw_sources, list):
        errors.append("sources must be an array")
    elif isinstance(raw_sources, list):
        discarded_sources = 0
        for entry in raw_sources:
            if len(sources) >= MAX_ACK_SOURCES:
                discarded_sources += 1
                continue
            normalized = _validate_source_entry(entry, errors)
            if normalized is not None:
                sources.append(normalized)
        discarded_total += discarded_sources

    stale = (
        stamped_revision is not None and ack_revision != int(stamped_revision)
    )
    normalized: Dict[str, Any] = {
        "schema_version": ACK_SCHEMA_VERSION,
        "mapspec_revision": ack_revision,
        "status": status,
        "stale": stale,
        "layers": layers,
        "components": components,
        "partial_apply": {"discarded": discarded_total},
    }
    if work_block is not None:
        normalized["work"] = work_block
    if sources:
        normalized["sources"] = sources
    if errors:
        normalized["errors"] = errors[:_MAX_ERRORS]
    return normalized, errors


def _validate_work_block(
    work: Any, errors: List[str]
) -> Optional[Dict[str, Any]]:
    """work echo 块校验：缺字段 → None（旧客户端合法）；形状非法 → 降级披露。"""
    if work is None:
        return None
    if not isinstance(work, dict):
        errors.append("work must be an object")
        return None
    work_id = _clean_id(work.get("work_id"))
    work_fp = str(work.get("work_fingerprint") or "")[:_MAX_FINGERPRINT]
    if not work_id or not work_fp:
        errors.append("work block requires work_id and work_fingerprint")
        return None
    out: Dict[str, Any] = {"work_id": work_id, "work_fingerprint": work_fp}
    epoch = work.get("style_epoch")
    if epoch is not None:
        if (
            isinstance(epoch, bool)
            or not isinstance(epoch, (int, float))
            or int(epoch) < 0
            or int(epoch) > _MAX_STYLE_EPOCH
        ):
            errors.append("work.style_epoch must be a bounded non-negative number")
        else:
            out["style_epoch"] = int(epoch)
    return out


def _validate_source_entry(
    entry: Any, errors: List[str]
) -> Optional[Dict[str, Any]]:
    """source 条目校验：source_id + status（ENTRY 词表）+ 可选 revision。

    容差不对称（有意）：layers/components 非数组 → 整块拒绝（v1 既有
    fail-closed 面）；sources 非数组 → 条目级跳过（additive 块缺席优先于
    主链路拒绝 —— 与 work 块同纪律）。
    """
    if not isinstance(entry, dict):
        errors.append("source entry must be an object")
        return None
    sid = _clean_id(entry.get("source_id"))
    if not sid:
        errors.append("source entry missing source_id")
        return None
    status = entry.get("status")
    if status not in ENTRY_STATUSES:
        errors.append(f"source {sid!r} invalid status {status!r}")
        return None
    out: Dict[str, Any] = {"source_id": sid, "status": status}
    revision = entry.get("data_revision")
    if revision is not None:
        if isinstance(revision, bool) or not isinstance(revision, (int, float, str)):
            errors.append(f"source {sid!r} invalid data_revision")
        else:
            out["data_revision"] = str(revision)[:_MAX_ID]
    # review P2-2：``cache`` 字段移除 —— 前端无真实产生点（封闭词表只描述
    # 现实纪律）；数据面命中语义后续经 telemetry 通道供给时再入词表。
    return out


def reconcile_work_echo(
    apply_ack: Dict[str, Any],
    *,
    mapspec: Any,
    session_id: str,
    revision: int,
    fingerprint: str,
) -> Dict[str, Any]:
    """服务端 work echo 对账（ADR-0225 review P2-3；纯函数，路由薄壳消费）。

    输入是 **validate_render_apply_ack 的 normalized 输出**。仅当 ACK 非
    stale 且带 work 块时，按当前权威 (mapspec, session, revision,
    fingerprint) 确定性重算期望 work_fingerprint（seq 不进指纹故可重算）
    比对客户端 echo：

    - 匹配 / 不匹配 → work 块增补 ``server_match`` 布尔（不匹配另在
      errors 追加 ``work_fingerprint mismatch``，遵守 8 条上界）；
    - 权威事实缺席（mapspec 非法 / 重算失败）→ 原样返回（fail-open，
      server_match 不落 —— 审计面不虚构）；
    - stale ACK / 无 work 块 → 原样返回（旧代证据只进诊断）。

    返回**新的** ack dict（输入不可变纪律；调用方替换落库值）。
    """
    if not isinstance(apply_ack, dict):
        return apply_ack
    work = apply_ack.get("work")
    if apply_ack.get("stale") is True or not isinstance(work, dict):
        return apply_ack
    if not isinstance(mapspec, dict) or not fingerprint:
        return apply_ack
    from app.lib.cartography.render_work_envelope import (
        project_render_work_envelope,
    )

    try:
        expected = project_render_work_envelope(
            mapspec,
            session_id=session_id,
            revision=revision,
            fingerprint=fingerprint,
        )
    except Exception:  # noqa: BLE001 — 对账失败按缺席（不虚构归因）
        return apply_ack
    matched = expected.work_fingerprint == work.get("work_fingerprint")
    out = dict(apply_ack)
    out["work"] = {**work, "server_match": matched}
    if not matched:
        errors = list(out.get("errors") or [])[:_MAX_ERRORS]
        if len(errors) < _MAX_ERRORS:
            errors.append("work_fingerprint mismatch")
        out["errors"] = errors
    return out


def _validate_entry(
    entry: Any, errors: List[str], kind: str
) -> Optional[Dict[str, Any]]:
    """条目级校验：非法条目跳过（不整块拒绝）；未知 reason_code 降级。"""
    if not isinstance(entry, dict):
        errors.append(f"{kind} entry must be an object")
        return None
    eid = _clean_id(entry.get("layer_id" if kind == "layer" else "component_id"))
    if not eid:
        errors.append(f"{kind} entry missing id")
        return None
    status = entry.get("status")
    if status not in ENTRY_STATUSES:
        errors.append(f"{kind} {eid!r} invalid status {status!r}")
        return None
    reason = entry.get("reason_code")
    normalized: Dict[str, Any] = {"id": eid, "status": status}
    if reason is not None:
        reason = str(reason)[:_MAX_REASON]
        if reason in REASON_CODES:
            normalized["reason_code"] = reason
        else:
            # 词表漂移收敛：未知码降级 apply_error + 审计披露（不虚构）。
            normalized["reason_code"] = RC_APPLY_ERROR
            errors.append(
                f"{kind} {eid!r} unknown reason_code {reason!r} downgraded"
            )
    return normalized


def derive_apply_ack_findings(observation: Dict[str, Any]) -> List[Any]:
    """observation.apply_ack → 渲染完成度 findings（stale 跳过）。

    - 无 ACK / stale ACK → 空表（stale ACK 不覆盖新状态 —— 判定只能
      来自当前 revision 的观察，旧 ACK 的失败归因对新状态是噪声）；
    - ``failed``/``skipped``/``stale`` 条目 → warning finding（transient/
      可自愈语义，与 P9 渲染族一致：re-reconcile / runtime-repair 收敛）；
      ``applied``/``pending`` 不产生 finding（pending 由后续观察覆盖）；
      ``user_pending`` 弃权项不产生 finding（用户显式操作留下的中间态
      不是 apply 失败 —— user-wins：归因不得指向用户正在进行的编辑）；
    - ADR-0225：``stale`` = 当前代 apply 但数据/信封身份落后（如 layer
      挂上了但 data revision 旧于信封依赖）—— 是当前代的真实风险，
      产生 finding（区别于事务级 stale：整份 ACK 属于旧代 → 跳过）；
      per-source ``stale`` 条目同语义（数据面对账失败）。
    """

    ack = observation.get("apply_ack") if isinstance(observation, dict) else None
    if not isinstance(ack, dict) or ack.get("stale") is True:
        return []
    findings: List[Any] = []
    for entry in ack.get("layers") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") not in (ENTRY_FAILED, ENTRY_SKIPPED, ENTRY_STALE):
            continue
        reason = str(entry.get("reason_code") or RC_APPLY_ERROR)
        if reason == RC_USER_PENDING:
            continue
        findings.append(MapCompletionFinding(
            code=F_RENDER_APPLY_FAILED,
            severity="warning",
            target=_clean_id(entry.get("id")),
            detail=f"apply ack: layer {entry.get('status')} ({reason})",
        ))
        if len(findings) >= _MAX_ERRORS:
            return findings
    for entry in ack.get("sources") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != ENTRY_STALE:
            continue
        findings.append(MapCompletionFinding(
            code=F_RENDER_APPLY_FAILED,
            severity="warning",
            target=_clean_id(entry.get("source_id")),
            detail=(
                "apply ack: source stale ("
                f"{entry.get('data_revision') or 'revision unknown'})"
            ),
        ))
        if len(findings) >= _MAX_ERRORS:
            break
    return findings


__all__ = [
    "ACK_SCHEMA_VERSION",
    "ACK_STATUS_APPLIED",
    "ACK_STATUS_PARTIAL",
    "ACK_STATUS_FAILED",
    "ACK_STATUSES",
    "ENTRY_APPLIED",
    "ENTRY_FAILED",
    "ENTRY_SKIPPED",
    "ENTRY_PENDING",
    "ENTRY_STALE",
    "ENTRY_STATUSES",
    "REASON_CODES",
    "RC_MISSING_AFTER_APPLY",
    "RC_UNSUPPORTED_LAYER_TYPE",
    "RC_SOURCE_UNRESOLVED",
    "RC_STYLE_DIVERGED",
    "RC_APPLY_ERROR",
    "RC_USER_PENDING",
    "RC_BUDGET_DEGRADED",
    "RC_STALE_DATA_REVISION",
    "MAX_ACK_LAYERS",
    "MAX_ACK_COMPONENTS",
    "MAX_ACK_SOURCES",
    "validate_render_apply_ack",
    "derive_apply_ack_findings",
    "reconcile_work_echo",
]
