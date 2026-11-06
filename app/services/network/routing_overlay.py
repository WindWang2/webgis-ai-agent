"""Per-route edits over a shared DiGraph, without copying its nodes/edges."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import networkx as nx


class _Atlas(Mapping):
    def __init__(self, base: Mapping, edits: Mapping, *, prefer_edits: bool = True) -> None:
        self.base, self.edits, self.prefer_edits = base, edits, prefer_edits

    def __iter__(self) -> Iterator[Any]:
        yield from self.base
        yield from (key for key in self.edits if key not in self.base)

    def __len__(self) -> int:
        return len(self.base) + sum(key not in self.base for key in self.edits)

    def __getitem__(self, key: Any) -> Any:
        first, second = (self.edits, self.base) if self.prefer_edits else (self.base, self.edits)
        return first[key] if key in first else second[key]

    def copy(self) -> dict:
        return dict(self.items())


class _Adjacency(_Atlas):
    def __getitem__(self, key: Any) -> Mapping:
        if key not in self.base and key not in self.edits:
            raise KeyError(key)
        return _Atlas(self.base.get(key, {}), self.edits.get(key, {}))


class RoutingGraphOverlay(nx.DiGraph):
    """A read-compatible NetworkX graph with writes restricted to a small overlay.

    Routing changes edge attributes through add_edge, which copies that edge's
    attributes. The shared base graph is never mutated. NetworkX's DiGraph
    adjacency protocol is exercised by routing, A*, OD and barrier regressions.
    """
    def __init__(self, base: nx.DiGraph | None = None) -> None:
        super().__init__()
        base = nx.DiGraph() if base is None else base
        self._base_graph = base
        self._edits = nx.DiGraph()
        self._removed: set[tuple[Any, Any]] = set()
        view = nx.subgraph_view(base, filter_edge=lambda u, v: (u, v) not in self._removed)
        self.graph = dict(base.graph)
        self._node = _Adjacency(base._node, self._edits._node)
        self._succ = _Adjacency(view._succ, self._edits._succ)
        self._adj = self._succ
        self._pred = _Adjacency(view._pred, self._edits._pred)

    def add_node(self, node_for_adding: Any, **attr: Any) -> None:
        self._edits.add_node(node_for_adding, **attr)
        nx._clear_cache(self)

    def add_edge(self, u_of_edge: Any, v_of_edge: Any, **attr: Any) -> None:
        self._edits.add_edge(u_of_edge, v_of_edge, **attr)
        nx._clear_cache(self)

    def add_nodes_from(self, nodes_for_adding: Any, **attr: Any) -> None:
        for node in nodes_for_adding:
            if isinstance(node, tuple) and len(node) == 2 and isinstance(node[1], Mapping):
                self.add_node(node[0], **{**attr, **node[1]})
            else:
                self.add_node(node, **attr)

    def add_edges_from(self, ebunch_to_add: Any, **attr: Any) -> None:
        for edge in ebunch_to_add:
            self.add_edge(edge[0], edge[1], **{**attr, **(edge[2] if len(edge) == 3 else {})})

    def remove_edge(self, u: Any, v: Any) -> None:
        if not self.has_edge(u, v):
            raise nx.NetworkXError(f"edge {u!r}-{v!r} is absent")
        if self._edits.has_edge(u, v):
            self._edits.remove_edge(u, v)
        self._removed.add((u, v))
        nx._clear_cache(self)

    def remove_edges_from(self, ebunch: Any) -> None:
        for edge in ebunch:
            if self.has_edge(edge[0], edge[1]):
                self.remove_edge(edge[0], edge[1])
