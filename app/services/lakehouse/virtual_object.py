"""Virtual DataObject — Spatial Lakehouse V7 (ADR-0119, Scope E).

**零字节复制**的组合对象：manifest 引用不可变 children（普通 DataObject
或嵌套 virtual），配一个有界 selection 描述符（时序选择/空间窗口/查询
投影的记录）与物化策略。

单一事实源边界：children 字节仍在 BlobStore（CAS 共享）；virtual
manifest 只新增**引用身份**（content root = 有序 children ids —— 见
``data_object._virtual_content_root``）。

契约（评审 R0-4/28）：

- **verify 递归**：children 缺失/损坏 ≠ verified（``virtual_children_missing``
  / ``virtual_child_corrupt``）—— 绝不让"零 blob"假绿；
- **全局解析预算**：visited-id 集 + ``MAX_VIRTUAL_NODES`` —— 菱形 DAG
  （共享 child 被多条路径重复展开）不指数；无环（child id 必须先于
  parent manifest 存在 —— 内容寻址的构造序）；
- **owner 一致**：children 必须同 owner scope（跨域组合 = typed 拒绝；
  发布通道是唯一跨域机制）；
- **物化策略**：``lazy``（默认）= 输出 children 清单不复制字节；
  ``inline`` ≤ inline_max_bytes 时真实物化并披露升级语义。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Union
from pathlib import Path

from app.services.lakehouse.data_object import (
    DataObjectError,
    is_data_object_id,
    owner_scope_allows,
    publish_manifest_only,
    resolve_data_object,
)

logger = logging.getLogger(__name__)

#: 解析深度/全局节点预算（菱形 DAG 不指数 —— 评审 R0-28）。
MAX_VIRTUAL_DEPTH = 8
MAX_VIRTUAL_NODES = 10_000

#: 物化策略。
MATERIALIZATION_POLICIES = ("lazy", "inline")


class VirtualObjectError(DataObjectError):
    """virtual 组合契约违例。"""

    code = "VIRTUAL_OBJECT_INVALID"


def publish_virtual_object(
    children: List[str],
    *,
    kind_label: str,
    owner_scope: Mapping[str, str],
    selection: Optional[Mapping[str, Any]] = None,
    materialization: str = "lazy",
    inline_max_bytes: int = 0,
    producer: Optional[Mapping[str, Any]] = None,
    input_fingerprint: Optional[str] = None,
    store: Optional[Any] = None,
) -> Dict[str, Any]:
    """发布 virtual DataObject（children 引用身份；零字节复制）。

    返回 ``{"data_object_id", "manifest", "children", "deduped"}``。
    """
    if materialization not in MATERIALIZATION_POLICIES:
        raise VirtualObjectError(
            f"materialization {materialization!r} not in {MATERIALIZATION_POLICIES}"
        )
    if not children or not all(is_data_object_id(c) for c in children):
        raise VirtualObjectError(
            "children must be a non-empty list of data object ids"
        )
    payload: Dict[str, Any] = {
        "virtual": {
            "kind_label": str(kind_label),
            "children": list(children),  # build 内部去重排序（确定性）
            "selection": dict(selection or {}),
            "materialization": materialization,
            "inline_max_bytes": int(inline_max_bytes),
        },
    }
    identity = publish_manifest_only(
        [],
        kind="virtual",
        owner_scope=owner_scope,
        payload=payload,
        producer=producer,
        source_refs=[f"data-object:{c}" for c in sorted(set(children))],
        input_fingerprint=input_fingerprint,
        store=store,
    )
    return {
        "data_object_id": identity.data_object_id,
        "manifest": identity.manifest_location,
        "children": sorted(set(children)),
        "deduped": identity.deduped,
    }


def resolve_virtual_object(
    data_object_id: str,
    *,
    owner_session_id: Optional[str] = None,
    owner_project_id: Optional[str] = None,
    store: Optional[Any] = None,
) -> Dict[str, Any]:
    """递归解析（全局预算；状态四态：ok / children_missing /
    child_corrupt / owner_mismatch）。返回可审计的报告（不抛异常 ——
    状态即语义；调用方决定降级/拒绝）。"""
    store = store or _store()
    visited: set = set()
    missing: List[str] = []
    corrupt: List[str] = []
    owner_mismatch: List[str] = []
    expanded: List[str] = []

    def _walk(oid: str, depth: int, *, check_owner: bool) -> None:
        if oid in visited:
            return
        if len(visited) >= MAX_VIRTUAL_NODES or depth > MAX_VIRTUAL_DEPTH:
            corrupt.append(oid)
            return
        visited.add(oid)
        manifest = resolve_data_object(oid, store=store)
        if manifest is None:
            missing.append(oid)
            return
        if check_owner and not owner_scope_allows(
            manifest,
            session_id=owner_session_id,
            project_id=owner_project_id,
        ):
            owner_mismatch.append(oid)
            return
        if manifest.get("kind") != "virtual":
            expanded.append(oid)
            return
        children = list(
            (manifest.get("payload") or {}).get("virtual", {}).get("children")
            or []
        )
        if not children:
            corrupt.append(oid)
            return
        for child in children:
            _walk(str(child), depth + 1, check_owner=check_owner)

    root_manifest = resolve_data_object(data_object_id, store=store)
    if root_manifest is None:
        return {"state": "children_missing", "root_missing": True,
                "children": [], "expanded": [], "missing": [data_object_id],
                "corrupt": [], "owner_mismatch": []}
    # 根对象自身的 owner 校验由调用方（REST/服务层）完成 —— 解析以根为
    # 信任锚，只对嵌套 children 做 owner 一致性检查（发布是唯一跨域机制）。
    _walk(data_object_id, 0, check_owner=False)
    if missing:
        state = "children_missing"
    elif corrupt:
        state = "child_corrupt"
    elif owner_mismatch:
        state = "owner_mismatch"
    else:
        state = "ok"
    return {
        "state": state,
        "children": list(
            (root_manifest.get("payload") or {}).get("virtual", {}).get(
                "children", []
            )
        ),
        "expanded": sorted(set(expanded)),
        "missing": sorted(set(missing)),
        "corrupt": sorted(set(corrupt)),
        "owner_mismatch": sorted(set(owner_mismatch)),
        "visited": len(visited),
    }


def verify_data_object_deep(
    data_object_id: str, *, store: Optional[Any] = None
) -> str:
    """含 virtual 递归的深度完整性判定（DR 语义扩展）。

    普通对象 → 既有 ``verify_data_object``；virtual → 递归解析 + 每个
    叶子对象的 digest 校验。返回状态字符串（与 V6 四态同族 + virtual
    扩展态）。
    """
    from app.services.lakehouse.data_object import verify_data_object

    manifest = resolve_data_object(data_object_id, store=store)
    if manifest is None:
        return "manifest_missing"
    if manifest.get("kind") != "virtual":
        return verify_data_object(data_object_id, store=store)
    report = resolve_virtual_object(data_object_id, store=store)
    if report["state"] == "children_missing":
        return "virtual_children_missing"
    if report["state"] == "child_corrupt":
        return "virtual_child_corrupt"
    if report["state"] == "owner_mismatch":
        return "virtual_owner_mismatch"
    for leaf in report["expanded"]:
        leaf_state = verify_data_object(leaf, store=store)
        if leaf_state != "verified":
            return f"virtual_child_{leaf_state}"
    return "verified"


def materialize_virtual_object(
    data_object_id: str,
    target_dir: Union[str, Path],
    *,
    owner_session_id: Optional[str] = None,
    owner_project_id: Optional[str] = None,
    store: Optional[Any] = None,
) -> Dict[str, Any]:
    """按物化策略落地（lazy = 引用清单；inline = 预算内真实物化）。

    返回 ``{"materialized": "lazy"|"inline"|"refused", "files": [...],
    "children": [...]}``。owner 校验强制 —— 绝不物化越权内容。
    """
    from app.services.lakehouse.data_object import materialize_data_object

    manifest = resolve_data_object(data_object_id, store=store)
    if manifest is None:
        raise VirtualObjectError(
            f"virtual object not found or corrupt: {str(data_object_id)[:16]}"
        )
    if manifest.get("kind") != "virtual":
        raise VirtualObjectError("not a virtual object")
    if not owner_scope_allows(
        manifest,
        session_id=owner_session_id,
        project_id=owner_project_id,
    ):
        raise VirtualObjectError("virtual object belongs to a different owner")
    spec = (manifest.get("payload") or {}).get("virtual") or {}
    report = resolve_virtual_object(
        data_object_id,
        owner_session_id=owner_session_id,
        owner_project_id=owner_project_id,
        store=store,
    )
    if report["state"] != "ok":
        # invalid child 检测：绝不静默跳过 —— typed 拒绝（半组合比无组合危险）。
        raise VirtualObjectError(
            f"virtual children not fully valid: state={report['state']} "
            f"missing={report['missing'][:4]} corrupt={report['corrupt'][:4]}"
        )
    base = Path(target_dir)
    if str(spec.get("materialization")) == "inline":
        budget = int(spec.get("inline_max_bytes") or 0)
        total = sum(
            int((resolve_data_object(leaf, store=store) or {}).get(
                "byte_size", 0
            ))
            for leaf in report["expanded"]
        )
        if total <= budget:
            files: List[str] = []
            for i, leaf in enumerate(report["expanded"]):
                leaf_dir = base / f"child_{i:04d}"
                files.extend(materialize_data_object(
                    leaf, leaf_dir,
                    owner_session_id=owner_session_id,
                    owner_project_id=owner_project_id,
                    store=store,
                ))
            return {
                "materialized": "inline",
                "files": files,
                "children": report["children"],
            }
        return {
            "materialized": "refused",
            "reason": "inline budget exceeded",
            "byte_size": total,
            "children": report["children"],
        }
    # lazy：只写 children 引用清单（零字节复制 —— 语义即证据）。
    base.mkdir(parents=True, exist_ok=True)
    listing = base / "VIRTUAL_CHILDREN.json"
    listing.write_text(
        "\n".join(report["expanded"]) + "\n", encoding="utf-8"
    )
    return {
        "materialized": "lazy",
        "files": [str(listing.relative_to(base))],
        "children": report["children"],
    }


def _store() -> Any:
    from app.services.s3_blob_store import get_object_store

    return get_object_store()
