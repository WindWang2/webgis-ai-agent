"""Bounded typed relation graph with cycle-safe traversal."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from .contracts import (
    MAX_TRAVERSAL_DEPTH,
    MAX_TRAVERSAL_NODES,
    RelationEdge,
    RelationType,
)
from .store import ClaimStore


@dataclass
class TraversalResult:
    nodes: List[str] = field(default_factory=list)
    edges: List[RelationEdge] = field(default_factory=list)
    truncated: bool = False
    cycle_detected: bool = False

    def to_bounded_dict(self) -> Dict:
        return {
            "nodes": self.nodes[:MAX_TRAVERSAL_NODES],
            "edges": [e.to_bounded_dict() for e in self.edges[:MAX_TRAVERSAL_NODES]],
            "truncated": self.truncated,
            "cycle_detected": self.cycle_detected,
            "node_count": len(self.nodes),
        }


class RelationGraph:
    """Read API over ClaimStore edges — never dumps unbounded graphs."""

    def __init__(self, store: ClaimStore) -> None:
        self._store = store

    def neighbors(
        self,
        node_id: str,
        *,
        relations: Optional[Iterable[RelationType]] = None,
        direction: str = "out",
    ) -> List[RelationEdge]:
        rel_set = {r.value if isinstance(r, RelationType) else str(r) for r in relations} if relations else None
        if direction == "in":
            edges = self._store.edges_to(node_id)
        elif direction == "both":
            edges = self._store.edges_from(node_id) + self._store.edges_to(node_id)
        else:
            edges = self._store.edges_from(node_id)
        if rel_set is None:
            return edges
        return [e for e in edges if e.relation.value in rel_set]

    def traverse(
        self,
        start: str,
        *,
        relations: Optional[Iterable[RelationType]] = None,
        direction: str = "out",
        max_depth: int = MAX_TRAVERSAL_DEPTH,
        max_nodes: int = MAX_TRAVERSAL_NODES,
    ) -> TraversalResult:
        max_depth = max(0, min(int(max_depth), MAX_TRAVERSAL_DEPTH))
        max_nodes = max(1, min(int(max_nodes), MAX_TRAVERSAL_NODES))
        result = TraversalResult()
        visited: Set[str] = set()
        path: Set[str] = set()

        def _next(edge: RelationEdge, node: str) -> str:
            if direction == "in":
                return edge.src
            if direction == "both":
                return edge.dst if edge.src == node else edge.src
            return edge.dst

        def dfs(node: str, depth: int) -> None:
            if node in path:
                result.cycle_detected = True
                return
            if node in visited:
                return
            if len(result.nodes) >= max_nodes:
                result.truncated = True
                return
            visited.add(node)
            result.nodes.append(node)
            if depth >= max_depth:
                return
            path.add(node)
            for edge in self.neighbors(node, relations=relations, direction=direction):
                if len(result.edges) < max_nodes:
                    result.edges.append(edge)
                nxt = _next(edge, node)
                if nxt in path:
                    result.cycle_detected = True
                    continue
                if result.truncated:
                    break
                if nxt not in visited:
                    dfs(nxt, depth + 1)
            path.discard(node)

        dfs(start, 0)
        return result

    def ancestors(
        self,
        start: str,
        *,
        max_depth: int = MAX_TRAVERSAL_DEPTH,
        max_nodes: int = MAX_TRAVERSAL_NODES,
    ) -> TraversalResult:
        """Upstream via derived_from / depends_on / computed_by / aggregated_from."""
        return self.traverse(
            start,
            relations=(
                RelationType.DERIVED_FROM,
                RelationType.DEPENDS_ON,
                RelationType.COMPUTED_BY,
                RelationType.AGGREGATED_FROM,
                RelationType.FILTERED_FROM,
            ),
            direction="out",
            max_depth=max_depth,
            max_nodes=max_nodes,
        )

    def descendants(
        self,
        start: str,
        *,
        max_depth: int = MAX_TRAVERSAL_DEPTH,
        max_nodes: int = MAX_TRAVERSAL_NODES,
    ) -> TraversalResult:
        """Downstream consumers via inbound lineage edges."""
        return self.traverse(
            start,
            relations=(
                RelationType.DERIVED_FROM,
                RelationType.DEPENDS_ON,
                RelationType.COMPUTED_BY,
                RelationType.AGGREGATED_FROM,
                RelationType.FILTERED_FROM,
                RelationType.SUPPORTS,
                RelationType.VISUALIZED_AS,
                RelationType.SUMMARIZED_BY,
            ),
            direction="in",
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
