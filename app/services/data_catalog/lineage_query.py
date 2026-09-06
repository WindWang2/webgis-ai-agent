"""Lineage Query V3 —— 统一血缘查询（§十；session 图 + project DB 桥）。

两个既有真相（审计 Agent A/C）：
- 会话：``ArtifactGraph``（artifact_registry 派生图，inputs 边）；
- 项目：DB ``ArtifactLineage``（lineage_service，cycle-checked 持久边）。

二者从不互通且形状各异。本模块提供**统一查询面**：

- 节点/边归一化形状（``LineageNode`` / ``LineageEdge``）—— V3 消费方
  （agent tools / workspace / replay）只学一种形状；
- 会话作用域全功能：parents/children/roots/上游闭包/下游闭包/
  深度有界视图/相关产物（兄弟 + 替换链）；
- 项目作用域：对 ``LineageService.get_lineage_graph`` 的**只读**桥接
  （租户过滤语义原样保留：调用方必须传 project_id）；
- 一切遍历深度有界 + 环安全（图实现已带 seen 集，这里再加深度闸）。

本模块不写血缘 —— 记录仍由 dispatch seam / workflow_engine 完成。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field, field_validator

from app.lib.data.artifact_contract import from_artifact_record

logger = logging.getLogger(__name__)

_MAX_VIEW_NODES = 256
_MAX_DEPTH = 16


class LineageNode(BaseModel):
    """归一化血缘节点（契约投影 + 有界）。"""

    artifact_id: str
    artifact_type: str = "unknown"
    artifact_subtype: str = ""
    logical_role: str = ""
    lifecycle: str = ""
    producer_tool: str = ""
    feature_count: Optional[int] = None
    depth: int = 0

    @field_validator("artifact_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("artifact_id required")
        return str(v)[:128]


class LineageEdge(BaseModel):
    """归一化血缘边（from → to；kind: lineage / replacement）。"""

    from_id: str
    to_id: str
    kind: str = "lineage"       # lineage（inputs 边）/ replacement（replaces 边）
    producer_tool: str = ""

    @field_validator("kind")
    @classmethod
    def _kind_enum(cls, v: str) -> str:
        if v not in ("lineage", "replacement"):
            raise ValueError("kind must be lineage|replacement")
        return v


class LineageView(BaseModel):
    """血缘视图（§十有界投影；节点/边封顶，截断显式声明）。"""

    root: str
    scope: str = "session"                  # session / project
    nodes: List[LineageNode] = Field(default_factory=list)
    edges: List[LineageEdge] = Field(default_factory=list)
    truncated: bool = False
    parents: List[str] = Field(default_factory=list)
    children: List[str] = Field(default_factory=list)
    roots: List[str] = Field(default_factory=list)

    def to_summary(self, *, max_nodes: int = 32) -> Dict[str, Any]:
        """LLM 有界摘要（§三十二）。"""
        return {
            "root": self.root,
            "scope": self.scope,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "truncated": self.truncated,
            "parents": self.parents,
            "children": self.children,
            "nodes": [n.model_dump() for n in self.nodes[:max_nodes]],
        }


def _node_from_record(record: Any, depth: int = 0) -> LineageNode:
    contract = from_artifact_record(record)
    return LineageNode(
        artifact_id=contract.artifact_id,
        artifact_type=contract.artifact_type,
        artifact_subtype=contract.artifact_subtype,
        logical_role=contract.logical_role.value,
        lifecycle=contract.lifecycle.value,
        producer_tool=contract.produced_by.tool,
        feature_count=contract.feature_count,
        depth=depth,
    )


class SessionLineageQuery:
    """会话作用域血缘查询（基于 ArtifactGraph 派生图；只读）。"""

    def __init__(self, records: Dict[str, Any]) -> None:
        from app.services.artifact_registry import build_artifact_graph

        self.records = records
        self.graph = build_artifact_graph(records)

    # ── 基本查询 ────────────────────────────────────────────────────
    def parents(self, artifact_id: str) -> List[str]:
        return self.graph.producers(artifact_id)

    def children(self, artifact_id: str) -> List[str]:
        return self.graph.consumers(artifact_id)

    def upstream(self, artifact_id: str, *, max_depth: int = _MAX_DEPTH) -> List[str]:
        """上游传递闭包（深度有界，环安全）。"""
        seen: Dict[str, int] = {artifact_id: 0}
        stack = [(p, 1) for p in self.parents(artifact_id)]
        while stack:
            cur, depth = stack.pop()
            if cur in seen or depth > max_depth:
                continue
            seen[cur] = depth
            stack.extend((p, depth + 1) for p in self.parents(cur))
        seen.pop(artifact_id, None)
        return sorted(seen)

    def downstream(self, artifact_id: str, *, max_depth: int = _MAX_DEPTH) -> List[str]:
        """下游传递闭包（深度有界，环安全）。"""
        seen: Dict[str, int] = {artifact_id: 0}
        stack = [(c, 1) for c in self.children(artifact_id)]
        while stack:
            cur, depth = stack.pop()
            if cur in seen or depth > max_depth:
                continue
            seen[cur] = depth
            stack.extend((c, depth + 1) for c in self.children(cur))
        seen.pop(artifact_id, None)
        return sorted(seen)

    def roots(self) -> List[str]:
        """全部根来源（无 inputs 边的记录；§十 root source）。"""
        return sorted(
            aid for aid, rec in self.records.items()
            if not [d for d in (getattr(rec, "inputs", None) or []) if d in self.records]
        )

    def replacement_chain(self, artifact_id: str) -> List[str]:
        return self.graph.replacement_chain(artifact_id)

    def siblings(self, artifact_id: str) -> List[str]:
        """兄弟产物（共享任一直接 parent；不含自身）。"""
        sib: set = set()
        for p in self.parents(artifact_id):
            for c in self.children(p):
                if c != artifact_id:
                    sib.add(c)
        return sorted(sib)

    # ── 视图投影 ────────────────────────────────────────────────────
    def view(
        self,
        artifact_id: str,
        *,
        max_depth: int = 3,
        max_nodes: int = _MAX_VIEW_NODES,
    ) -> LineageView:
        """以 artifact 为根的双向深度有界视图（§十 ASCII 图的机器形状）。"""
        nodes: Dict[str, LineageNode] = {}
        edges: List[LineageEdge] = []
        truncated = False

        def _visit_up(aid: str, depth: int) -> None:
            nonlocal truncated
            if len(nodes) >= max_nodes:
                truncated = True
                return
            rec = self.records.get(aid)
            if rec is not None and aid not in nodes:
                nodes[aid] = _node_from_record(rec, depth)
            if depth >= max_depth:
                truncated = truncated or bool(self.parents(aid))
                return
            for p in self.parents(aid):
                edges.append(LineageEdge(from_id=p, to_id=aid, producer_tool=_tool(p)))
                _visit_up(p, depth + 1)

        def _visit_down(aid: str, depth: int) -> None:
            nonlocal truncated
            if depth >= max_depth:
                truncated = truncated or bool(self.children(aid))
                return
            for c in self.children(aid):
                if len(nodes) >= max_nodes:
                    truncated = True
                    return
                edges.append(LineageEdge(from_id=aid, to_id=c, producer_tool=_tool(aid)))
                rec = self.records.get(c)
                if rec is not None and c not in nodes:
                    nodes[c] = _node_from_record(rec, depth + 1)
                _visit_down(c, depth + 1)

        def _tool(aid: str) -> str:
            rec = self.records.get(aid)
            return str(getattr(rec, "producer_tool", "") or "") if rec is not None else ""

        _visit_up(artifact_id, 0)
        _visit_down(artifact_id, 0)
        # 替换边（head 的链）并入视图
        rec = self.records.get(artifact_id)
        if rec is not None and getattr(rec, "replaces", None):
            edges.append(LineageEdge(
                from_id=str(rec.replaces), to_id=artifact_id, kind="replacement",
                producer_tool=_tool(artifact_id),
            ))
        return LineageView(
            root=artifact_id,
            scope="session",
            nodes=list(nodes.values()),
            edges=edges,
            truncated=truncated,
            parents=self.parents(artifact_id),
            children=self.children(artifact_id),
            roots=self.roots(),
        )


async def session_lineage(
    session_id: str,
    artifact_id: str,
    *,
    max_depth: int = 3,
) -> Optional[LineageView]:
    """便捷入口：session 账本 → 视图（账本空/缺失 → None）。"""
    from app.services.artifact_registry import list_artifacts

    records = {r.artifact_id: r for r in await list_artifacts(session_id)}
    if artifact_id not in records:
        return None
    return SessionLineageQuery(records).view(artifact_id, max_depth=max_depth)


def project_lineage(
    db: Any,
    artifact_id: str,
    *,
    project_id: Optional[str] = None,
    max_depth: int = 5,
) -> Dict[str, Any]:
    """项目作用域只读桥：DB lineage → 归一化视图 dict。

    语义原样保留 ``LineageService.get_lineage_graph``（含 DATA-01 租户
    过滤 —— ``project_id`` 必须由调用方传入入口 artifact 的归属）。
    DB 不可用时抛出的异常原样上抛（调用方决定降级），这里不做静默。
    """
    from app.services.lineage_service import LineageService

    raw = LineageService.get_lineage_graph(
        db, artifact_id, max_depth=max_depth, project_id=project_id
    )
    parents = [
        LineageEdge(
            from_id=p["parent_artifact_id"],
            to_id=p["artifact_id"],
            kind="lineage",
            producer_tool=str(p.get("producing_tool") or ""),
        ).model_dump()
        for p in raw.get("parents", [])
    ]
    consumers = [
        LineageEdge(
            from_id=c["parent_artifact_id"],
            to_id=c["consumer_artifact_id"],
            kind="lineage",
            producer_tool=str(c.get("producing_tool") or ""),
        ).model_dump()
        for c in raw.get("consumers", [])
    ]
    node_ids = {artifact_id}
    for e in parents + consumers:
        node_ids.update((e["from_id"], e["to_id"]))
    return {
        "root": artifact_id,
        "scope": "project",
        "nodes": sorted(node_ids)[:_MAX_VIEW_NODES],
        "parent_edges": parents,
        "consumer_edges": consumers,
        "truncated": len(node_ids) > _MAX_VIEW_NODES,
    }
