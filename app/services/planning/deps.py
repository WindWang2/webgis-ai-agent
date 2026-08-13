"""Static dependency validation and ``${stepId[.path]}`` reference resolution.

Typed companion to plan_mode.py's ``validate_plan`` / ``resolve_refs``
(app/services/plan_mode.py:88-193). Two differences are the point of this
module (design-v3 §deps):

- ``validate_static_refs`` returns a *list* of issue strings (empty = valid)
  instead of a single error, covering unknown ids, forward refs, self-deps and
  cycles.
- ``resolve_arg_refs`` raises ``MissingRefError`` instead of silently returning
  ``None`` / ``""`` when a placeholder names an existing step but a bad path —
  plan_mode.py:148-193 bug fixed here.
"""
import re
from collections import deque
from typing import Any, Optional

from .models import CanonicalStep

# Placeholder grammar — must stay in sync with plan_mode.py:70.
REF_PATTERN = re.compile(r"\$\{([a-zA-Z_][\w]*?)(?:\.([\w\.]+))?\}")


def _extract_refs(value: Any) -> set[str]:
    """Recursively collect the ``${stepId...}`` step ids from an args value."""
    refs: set[str] = set()
    if isinstance(value, str):
        for m in REF_PATTERN.finditer(value):
            refs.add(m.group(1))
    elif isinstance(value, dict):
        for v in value.values():
            refs.update(_extract_refs(v))
    elif isinstance(value, list):
        for v in value:
            refs.update(_extract_refs(v))
    return refs


def validate_static_refs(steps: list[CanonicalStep]) -> list[str]:
    """Validate step ids and dependency edges without executing anything.

    Returns a deterministic list of issue strings (empty list = valid):

    - duplicate step ids
    - references to unknown step ids
    - forward references (dependency declared after the referencing step)
    - self-dependencies
    - dependency cycles (explicit topological check; catches ref-only cycles)

    Dependencies are taken from ``depends_on`` plus the ``${stepId}``
    placeholders inside ``args`` (same union plan_mode uses).
    """
    issues: list[str] = []
    order_index: dict[str, int] = {}
    for i, step in enumerate(steps):
        if step.id in order_index:
            issues.append(f"duplicate step id '{step.id}'")
        order_index[step.id] = i

    for step in steps:
        inferred = set(step.depends_on) | _extract_refs(step.args)
        if step.id in inferred:
            issues.append(f"step '{step.id}' depends on itself")
        for ref in inferred - {step.id}:
            if ref not in order_index:
                issues.append(f"step '{step.id}' references unknown step '{ref}'")
            elif order_index[ref] > order_index[step.id]:
                issues.append(
                    f"step '{step.id}' forward-references step '{ref}' declared later"
                )

    if _topological_order(steps, order_index) is None:
        issues.append("dependency graph contains a cycle")
    return issues


def _topological_order(
    steps: list[CanonicalStep], order_index: dict[str, int]
) -> Optional[list[str]]:
    """Kahn's algorithm over ``depends_on`` ∪ args refs; None when cyclic."""
    in_degree: dict[str, int] = {sid: 0 for sid in order_index}
    edges: dict[str, list[str]] = {sid: [] for sid in order_index}
    for step in steps:
        for dep in (set(step.depends_on) | _extract_refs(step.args)) - {step.id}:
            if dep not in in_degree:
                continue
            edges[dep].append(step.id)
            in_degree[step.id] += 1

    queue: deque[str] = deque([sid for sid, d in in_degree.items() if d == 0])
    order: list[str] = []
    while queue:
        sid = queue.popleft()
        order.append(sid)
        for nxt in edges[sid]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    return order if len(order) == len(in_degree) else None


class MissingRefError(Exception):
    """A ``${stepId[.path]}`` placeholder could not be resolved at runtime.

    Raised when the named step has no result yet, or an existing step's result
    does not contain the requested path segment — never silently returning
    ``None`` / ``""`` for a placeholder (kills the plan_mode.py:148-193 bug).

    Attributes carry the structured facts for the caller to build a
    MISSING_REF correction hint: ``step_id``, ``path`` and the keys actually
    available in ``completed_results``.
    """

    def __init__(self, step_id: str, path: str, available_keys: list[str]):
        self.step_id = step_id
        self.path = path
        self.available_keys = list(available_keys)
        ref = f"${{{step_id}{('.' + path) if path else ''}}}"
        super().__init__(
            f"cannot resolve ref {ref}: step '{step_id}' has no resolvable value "
            f"at path '{path or '<root>'}' "
            f"(available step results: {self.available_keys})"
        )


def _resolve_path_ref(
    step_id: str, path: str, completed_results: dict[str, Any]
) -> Any:
    """Resolve one placeholder against completed step results (raises on miss)."""
    if step_id not in completed_results:
        raise MissingRefError(step_id, path, list(completed_results))
    cur = completed_results[step_id]
    if not path:
        return cur
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                raise MissingRefError(step_id, path, list(completed_results))
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                idx = int(part)
            except ValueError:
                raise MissingRefError(step_id, path, list(completed_results))
            if idx < 0 or idx >= len(cur):
                raise MissingRefError(step_id, path, list(completed_results))
            cur = cur[idx]
        else:
            raise MissingRefError(step_id, path, list(completed_results))
    return cur


def resolve_arg_refs(value: Any, completed_results: dict[str, Any]) -> Any:
    """Resolve ``${stepId}`` / ``${stepId.path.to.field}`` placeholders.

    Happy-path semantics mirror plan_mode.py's ``resolve_refs``: a full-match
    placeholder resolves to the referenced object (preserving dict/list
    structure), an embedded placeholder stringifies it, and non-placeholder
    values (including nested dicts/lists inside args) pass through untouched.

    Any unresolvable placeholder — a step with no result yet, or an existing
    step whose result lacks the requested path — raises ``MissingRefError``
    instead of silently yielding ``None`` / ``""``.
    """
    if isinstance(value, str):
        m = REF_PATTERN.fullmatch(value)
        if m:
            return _resolve_path_ref(m.group(1), m.group(2) or "", completed_results)

        def _sub(m: re.Match[str]) -> str:
            resolved = _resolve_path_ref(m.group(1), m.group(2) or "", completed_results)
            return str(resolved)

        return REF_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: resolve_arg_refs(v, completed_results) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_arg_refs(v, completed_results) for v in value]
    return value
