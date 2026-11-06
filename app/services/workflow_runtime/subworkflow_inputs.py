"""Bind child data roles from the parent's named incoming ports."""
from __future__ import annotations

from app.contracts.workflow import NodeRecord, WorkflowDAG
from app.services.workflow_runtime.machine import upstream_ports
from app.services.workflow_runtime.contracts import NodeState


def child_input_bindings(
    parent_dag: WorkflowDAG, parent_nodes: list[NodeRecord], parent_node_id: str,
    child_dag: WorkflowDAG, child_nodes: list[NodeRecord], input_refs: list[str],
) -> dict[str, str]:
    """Resolve role/port aliases, rejecting ambiguous multi-input legacy data."""
    refs = {ref for ref in input_refs if ref and not ref.startswith("wi:")}
    if not refs:
        return {}
    definitions = {node.get("node_id", ""): node for node in parent_dag.get("nodes", [])}
    stored = {node["node_id"]: node for node in parent_nodes}
    aliases: dict[str, set[str]] = {}
    for source, port in upstream_ports(parent_dag).get(parent_node_id, []):
        row = stored.get(source, {})
        ref = row.get("bound_ref") or row.get("output_ref") or ""
        if ref not in refs:
            continue
        for alias in (port, definitions.get(source, {}).get("role", ""), source.removeprefix("data:")):
            if alias:
                aliases.setdefault(alias, set()).add(ref)
    child_definitions = {node.get("node_id", ""): node for node in child_dag.get("nodes", [])}
    pending = [node for node in child_nodes if node["state"] == NodeState.PENDING and not node.get("bound_ref")
               and child_definitions.get(node["node_id"], {}).get("kind") == "data_input"]
    bindings: dict[str, str] = {}
    for node in pending:
        definition = child_definitions[node["node_id"]]
        # Role and data-node identity take precedence over generic output ports.
        names = (definition.get("role", ""), node["node_id"].removeprefix("data:"))
        candidates = set().union(*(aliases.get(name, set()) for name in names))
        if not candidates:
            candidates = set().union(*(aliases.get(port.get("name", ""), set())
                                       for port in definition.get("outputs", [])))
        if len(candidates) == 1:
            bindings[node["node_id"]] = next(iter(candidates))
        elif len(pending) == 1 and len(refs) == 1:
            bindings[node["node_id"]] = next(iter(refs))
        else:
            raise ValueError(f"Ambiguous parent input for child role {definition.get('role') or node['node_id']}")
    if refs - set(bindings.values()):
        raise ValueError("Parent inputs have no matching child data role")
    return bindings
