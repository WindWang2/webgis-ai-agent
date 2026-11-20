"""G44 执行链接缝 —— active 合同解析 + 候选态统一门禁（feature_edit/G32 共用）。

接线纪律（WP06）：
- ``resolve_active_contract``：DB 冷读 active 行 + 绑定有效性（descriptor
  content_revision ≥ 激活时 revision；回退 = stale，不随机重绑）。
  DB 不可读 → ``ContractStateUnavailable``（fail-closed —— 合同存在性无法证明时
  不盲写，F36）；无 active → None（旧行为完全保留，F01）。
- 候选态校验：preview / commit / undo / redo / G32 merge 都在「完整候选属性状态」
  上调用 ``validate_candidate_fc``（与完整评估同一 validator 内核）。
- 声明字段覆盖观测词表的业务判断（跳过 observed enum），未声明字段行为不变。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from app.services.field_contracts.contracts import (
    BINDING_MISSING,
    BINDING_OK,
    BINDING_STALE,
)
from app.services.field_contracts.validator import (
    NormalizedContract,
    normalize_document,
    validate_candidate_state,
)

__all__ = [
    "ContractStateUnavailable",
    "EnforcedContract",
    "resolve_active_contract",
    "contract_binding_status",
    "validate_candidate_fc",
    "candidate_findings_projection",
    "contract_plan_view",
]


class ContractStateUnavailable(RuntimeError):
    """合同 DB 不可读 —— 存在性无法证明；编辑链 fail-closed。"""


@dataclass(frozen=True)
class EnforcedContract:
    contract_id: str
    contract_key: str
    revision: int
    digest: str
    normalized: NormalizedContract
    binding: Dict[str, Any]
    title: str = ""

    def declared_fields(self) -> Set[str]:
        return set(self.normalized.field_map)

    def plan_view(self) -> Dict[str, Any]:
        """plan 的 read-set 绑定（只存身份三元组，规则由 commit 时冷读复核）。"""
        return {
            "contract_id": self.contract_id,
            "revision": self.revision,
            "digest": self.digest,
        }


def contract_plan_view(enforced: Optional[EnforcedContract]) -> Optional[Dict[str, Any]]:
    return enforced.plan_view() if enforced else None


def _normalize_row(row: Dict[str, Any]) -> EnforcedContract:
    try:
        normalized = normalize_document(row.get("rules") or {})
    except ValueError as exc:  # noqa: BLE001 — 行内容损坏：fail-closed
        raise ContractStateUnavailable(f"active contract row unparsable: {exc}") from exc
    return EnforcedContract(
        contract_id=str(row.get("id")),
        contract_key=str(row.get("contract_key")),
        revision=int(row.get("revision") or 0),
        digest=str(row.get("normalized_digest") or ""),
        normalized=normalized,
        binding=row.get("binding") or {},
        title=str(row.get("title") or ""),
    )


async def resolve_active_contract(
    session_id: str, ref_id: str
) -> Optional[EnforcedContract]:
    """当前 active 且绑定有效的合同；无 → None；DB 故障 → ContractStateUnavailable。

    绑定有效性（ADR）：激活时记录 content_revision R 与数据指纹 D。编辑演进单调
    递增 descriptor.content_revision —— 当前 ≥ R 视为同源演化（有效）；当前 < R
    说明 ref 被替换/重置（binding_stale —— 合同显式失效，不自动重绑，F32）。
    """
    from app.services.field_contracts.store import field_contract_store

    def _read() -> Optional[Dict[str, Any]]:
        return field_contract_store.get_active_contract(session_id, ref_id)

    try:
        row = await asyncio.to_thread(_read)
    except ContractStateUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — DB 不可达：无法证明「无合同」
        raise ContractStateUnavailable(
            f"contract store unreadable for {ref_id[:48]}: {exc}"
        ) from exc
    if row is None or row.get("status") != "active":
        return None
    enforced = _normalize_row(row)
    activation = enforced.binding.get("activation") or {}
    rev_at_activation = activation.get("content_revision")
    if isinstance(rev_at_activation, int):
        from app.services.session_data import session_data_manager

        descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
        current_revision = int(descriptor.get("content_revision") or 0)
        if current_revision < rev_at_activation:
            # ref 被替换/重置：显式 stale —— 不在新数据上执行旧合同
            return None
    return enforced


async def contract_binding_status(session_id: str, ref_id: str) -> Dict[str, Any]:
    """工作台/回执视角：active 合同 + 绑定状态（ok/stale/missing）。"""
    from app.services.field_contracts.store import field_contract_store

    def _read() -> Optional[Dict[str, Any]]:
        return field_contract_store.get_active_contract(session_id, ref_id)

    try:
        row = await asyncio.to_thread(_read)
    except Exception as exc:  # noqa: BLE001 — DB 不可读：typed 披露（非裸 500）
        return {"status": "unavailable", "error": f"contract store unreadable: {exc}"[:200], "contract": None}
    if row is None:
        return {"status": BINDING_MISSING, "contract": None}
    activation = (row.get("binding") or {}).get("activation") or {}
    rev_at_activation = activation.get("content_revision")
    state = BINDING_OK
    if isinstance(rev_at_activation, int):
        from app.services.session_data import session_data_manager

        descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
        current_revision = int(descriptor.get("content_revision") or 0)
        if current_revision < rev_at_activation:
            state = BINDING_STALE
    return {
        "status": state,
        "contract": {
            "id": row.get("id"),
            "contract_key": row.get("contract_key"),
            "revision": row.get("revision"),
            "digest": row.get("normalized_digest"),
            "title": row.get("title"),
            "activation": activation,
        },
    }


def validate_candidate_fc(
    candidate_fc: Dict[str, Any],
    enforced: EnforcedContract,
    *,
    changed_wfids: Optional[Set[str]] = None,
    schema_changed: bool = False,
) -> List[Dict[str, Any]]:
    """候选态完整校验（唯一性恒全量；字段规则按变更面或 schema 变化决定）。"""
    return validate_candidate_state(
        candidate_fc,
        enforced.normalized,
        changed_wfids=changed_wfids,
        schema_changed=schema_changed,
    )


def candidate_findings_projection(
    findings: List[Dict[str, Any]], *, limit: int = 8
) -> List[Dict[str, Any]]:
    """违规 → 有界 EditFinding detail 投影（进 preview/commit 回执）。"""
    out: List[Dict[str, Any]] = []
    for v in findings[:limit]:
        item: Dict[str, Any] = {"code": v.get("code")}
        for key in ("rule_id", "field", "wfid", "row"):
            if v.get(key) is not None:
                item[key] = v[key]
        if v.get("value") is not None:
            item["value"] = str(v["value"])[:64]
        if v.get("detail"):
            item["detail"] = v["detail"]
        if v.get("fields"):
            item["fields"] = v["fields"]
        if v.get("group_size"):
            item["group_size"] = v["group_size"]
        out.append(item)
    if len(findings) > limit:
        out.append({
            "code": "more_violations",
            "total": len(findings),
            "materialized": limit,
        })
    return out
