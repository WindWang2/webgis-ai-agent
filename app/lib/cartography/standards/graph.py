"""RuleGraph — dependency + conflict handling over a frozen rule set (ADR-0200).

Build-time fail-closed: unknown references, cycles, and require+conflict
contradictions raise :class:`RuleGraphError` at pack construction — never
silently at evaluation. Evaluation order is a deterministic topological order
(Kahn's algorithm, lexicographic tie-break by rule_id). Conflicts are
undirected; when two conflicting rules are *both* applicable and both produce
violations, :meth:`conflict_violations` emits a single RULE_CONFLICT disclosure
naming both rules — the point is honesty, not arbitration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Tuple

from app.lib.cartography.standards.rule import CartographicRule


class RuleGraphError(ValueError):
    """Fail-closed structural error in a rule graph."""


@dataclass(frozen=True)
class StandardsViolation:
    """Deterministic violation with stable ids and evidence refs.

    ``evidence`` stays bounded (the QA layer projects context through the
    credential-safe cartographic projection). ``fix_hint`` is a routed hint
    only — applying it is always the existing mutation/autofill executor's
    decision.
    """

    rule_id: str
    kind: str
    severity: str
    message: str
    layer_id: str = ""
    source_id: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    fix_hint: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "layer_id": self.layer_id,
            "source_id": self.source_id,
            "evidence": self.evidence,
            "fix_hint": dict(self.fix_hint) if self.fix_hint else None,
        }


@dataclass(frozen=True)
class RuleGraph:
    """Validated, ordered view over a rule set. Build via :meth:`build`."""

    rules: Tuple[CartographicRule, ...]
    _order: Tuple[str, ...] = ()
    _deps: FrozenSet[Tuple[str, str]] = frozenset()  # (rule, dependency)
    _conflicts: FrozenSet[Tuple[str, str]] = frozenset()  # normalized pair

    @classmethod
    def build(cls, rules: Iterable[CartographicRule]) -> "RuleGraph":
        materialized = tuple(rules)
        if not materialized:
            raise RuleGraphError("规则集为空：标准包至少需要一条规则")
        ids = [r.rule_id for r in materialized]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise RuleGraphError(f"rule_id 重复：{', '.join(dupes)}")
        for rule in materialized:
            if not isinstance(rule, CartographicRule):
                raise RuleGraphError("规则集含非 CartographicRule 成员")
        known = set(ids)
        deps: List[Tuple[str, str]] = []
        conflict_pairs: List[Tuple[str, str]] = []
        for rule in materialized:
            for dep in rule.requires:
                if dep not in known:
                    raise RuleGraphError(
                        f"{rule.rule_id} 依赖未知规则 {dep!r}（fail-closed）")
                if dep == rule.rule_id:
                    raise RuleGraphError(f"{rule.rule_id} 依赖自身")
                deps.append((rule.rule_id, dep))
            for other in rule.conflicts_with:
                if other not in known:
                    raise RuleGraphError(
                        f"{rule.rule_id} 冲突引用未知规则 {other!r}（fail-closed）")
                if other == rule.rule_id:
                    raise RuleGraphError(f"{rule.rule_id} 与自身冲突")
                if other in rule.requires:
                    raise RuleGraphError(
                        f"{rule.rule_id} 同时 require 与 conflict {other!r}（矛盾声明）")
                conflict_pairs.append(tuple(sorted((rule.rule_id, other))))  # type: ignore[arg-type]
        order = _topological_order(ids, deps)
        return cls(
            rules=materialized,
            _order=tuple(order),
            _deps=frozenset(deps),
            _conflicts=frozenset(conflict_pairs),
        )

    # ── queries ──────────────────────────────────────────────────────────

    def by_id(self, rule_id: str) -> CartographicRule:
        for rule in self.rules:
            if rule.rule_id == rule_id:
                return rule
        raise RuleGraphError(f"未知规则 {rule_id!r}")

    def evaluation_order(self) -> Tuple[CartographicRule, ...]:
        by_id = {r.rule_id: r for r in self.rules}
        return tuple(by_id[rid] for rid in self._order)

    def dependencies_of(self, rule_id: str) -> FrozenSet[str]:
        return frozenset({dep for rid, dep in self._deps if rid == rule_id})

    def conflicts_with(self, rule_id: str) -> FrozenSet[str]:
        out: set = set()
        for a, b in self._conflicts:
            if a == rule_id:
                out.add(b)
            elif b == rule_id:
                out.add(a)
        return frozenset(out)

    def conflict_violations(
        self, *, applied: FrozenSet[str], severity: str = "error",
    ) -> Tuple[StandardsViolation, ...]:
        """Dual-disclosure violations for conflicting rules violated together.

        Deterministic pair order (sorted), one violation per conflicting pair
        whose both sides are in ``applied``.
        """
        out: List[StandardsViolation] = []
        for a, b in sorted(self._conflicts):
            if a in applied and b in applied:
                out.append(StandardsViolation(
                    rule_id="RULE_CONFLICT",
                    kind="rule_conflict",
                    severity=severity,
                    message=(
                        f"冲突规则 {a} 与 {b} 同时触发：义务相互矛盾，"
                        "需显式裁量（两规则证据均保留，不静默丢弃）。"
                    ),
                    evidence={"rule_ids": [a, b]},
                ))
        return tuple(out)


def _topological_order(ids: List[str], deps: List[Tuple[str, str]]) -> List[str]:
    """Kahn's algorithm with lexicographic tie-break (deterministic total order)."""
    remaining_deps: Dict[str, int] = {rid: 0 for rid in ids}
    dependents: Dict[str, List[str]] = {rid: [] for rid in ids}
    for rid, dep in deps:
        remaining_deps[rid] += 1
        dependents[dep].append(rid)
    ready = sorted(rid for rid, n in remaining_deps.items() if n == 0)
    order: List[str] = []
    while ready:
        rid = ready.pop(0)
        order.append(rid)
        for dependent in sorted(dependents[rid]):
            remaining_deps[dependent] -= 1
            if remaining_deps[dependent] == 0:
                ready.append(dependent)
        ready.sort()
    if len(order) != len(ids):
        stuck = sorted(set(ids) - set(order))
        raise RuleGraphError(f"规则依赖存在环：{', '.join(stuck)}")
    return order


__all__ = [
    "RuleGraph",
    "RuleGraphError",
    "StandardsViolation",
]
