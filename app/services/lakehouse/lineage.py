"""Object lineage — Spatial Lakehouse V7 (ADR-0119, Scope lineage 集成).

manifest 层的血缘祖先遍历（元数据级、有界）：`source_refs` 的
``data-object:<id>`` 边 + virtual children 边。会话工件的血缘真相仍在
``artifact_registry``（ArtifactGraph）；项目发布血缘在
``artifact_revisions``/``artifact_lineages`` —— 本模块只补 DataObject
身份层的祖先视图（catalog/STAC/publish 面共用的证据投影）。

有界：深度 ≤8、节点 ≤10_000（与 virtual 解析同族契约）。
"""
from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional

from app.services.lakehouse.data_object import resolve_data_object

MAX_LINEAGE_DEPTH = 8
MAX_LINEAGE_NODES = 10_000


def _parents_of(manifest: Dict[str, Any]) -> List[str]:
    parents: List[str] = []
    for ref in manifest.get("source_refs") or []:
        text = str(ref)
        if text.startswith("data-object:"):
            parents.append(text[len("data-object:"):])
    children = list(
        (manifest.get("payload") or {}).get("virtual", {}).get("children")
        or []
    )
    parents.extend(str(c) for c in children)
    seen: set = set()
    ordered: List[str] = []
    for p in parents:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


def object_lineage(
    data_object_id: str, *, store: Optional[Any] = None
) -> Dict[str, Any]:
    """祖先视图（BFS；节点/深度双闸）。返回
    ``{"root", "ancestors": [{id, kind, depth}], "edges", "truncated"}``。"""
    visited: set = set()
    queue: deque = deque([(data_object_id, 0)])
    ancestors: List[Dict[str, Any]] = []
    edges: List[Dict[str, str]] = []
    truncated = False
    while queue:
        oid, depth = queue.popleft()
        if oid in visited:
            continue
        if len(visited) >= MAX_LINEAGE_NODES or depth > MAX_LINEAGE_DEPTH:
            truncated = True
            break
        visited.add(oid)
        manifest = resolve_data_object(oid, store=store)
        if manifest is None:
            ancestors.append({"id": oid, "kind": "<missing>", "depth": depth})
            continue
        if oid != data_object_id:
            ancestors.append({
                "id": oid,
                "kind": str(manifest.get("kind") or ""),
                "depth": depth,
            })
        for parent in _parents_of(manifest):
            edges.append({"child": oid, "parent": parent})
            queue.append((parent, depth + 1))
    return {
        "root": data_object_id,
        "ancestors": ancestors,
        "edges": edges,
        "truncated": truncated,
    }
