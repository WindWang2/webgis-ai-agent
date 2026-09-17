"""StandardsPack — a versioned, frozen set of cartographic rules (ADR-0200).

A pack is immutable content-addressed data: ``fingerprint`` is the sha256 of
its canonical projection (rules sorted by rule_id), so a QA report can pin
exactly which rule semantics produced it. Registration into a
:class:`StandardsRegistry` is fail-closed on (pack_id, version) duplicates;
``resolve`` supports exact semver or the latest registered version.

Versioning + backward compatibility contract: new obligations ship as a new
pack version; old maps evaluate under whatever pack/profile the caller pins
(or the registry default), and legacy behavior is guarded by the QA layer's
severity-escalation rules — never by silently rewriting old packs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Dict, Iterable, List, Optional, Tuple

from app.lib.cartography.standards.graph import RuleGraph
from app.lib.cartography.standards.rule import (
    CartographicRule,
    _SEMVER,
)


class StandardsPackError(ValueError):
    """Fail-closed pack construction / registry error."""


@dataclass(frozen=True)
class StandardsPack:
    pack_id: str
    version: str
    rules: Tuple[CartographicRule, ...]
    description: str = ""
    _graph: RuleGraph = field(default=None, repr=False, compare=False)  # type: ignore[assignment]

    @classmethod
    def build(
        cls,
        *,
        pack_id: str,
        version: str,
        rules: Iterable[CartographicRule],
        description: str = "",
    ) -> "StandardsPack":
        if not isinstance(pack_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", pack_id):
            raise StandardsPackError(
                f"pack_id {pack_id!r} 必须是小写标识符（如 core）")
        if not isinstance(version, str) or not _SEMVER.fullmatch(version):
            raise StandardsPackError(
                f"version {version!r} 必须是 MAJOR.MINOR.PATCH semver")
        materialized = tuple(rules)
        if not materialized:
            raise StandardsPackError("StandardsPack 至少需要一条规则")
        for rule in materialized:
            if not isinstance(rule, CartographicRule):
                raise StandardsPackError("pack rules 含非 CartographicRule 成员")
        graph = RuleGraph.build(materialized)
        return cls(
            pack_id=pack_id,
            version=version,
            rules=materialized,
            description=description,
            _graph=graph,
        )

    def graph(self) -> RuleGraph:
        if self._graph is None:  # only for directly-constructed (test) instances
            return RuleGraph.build(self.rules)
        return self._graph

    @property
    def fingerprint(self) -> str:
        projection = {
            "pack_id": self.pack_id,
            "version": self.version,
            "rules": sorted(
                (r.to_dict() for r in self.rules), key=lambda d: d["rule_id"],
            ),
        }
        payload = json.dumps(
            projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return f"stdpack-sha256:{hashlib.sha256(payload).hexdigest()}"

    def to_dict(self) -> Dict[str, object]:
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "description": self.description,
            "fingerprint": self.fingerprint,
            "rule_count": len(self.rules),
            "rules": [r.to_dict() for r in self.graph().evaluation_order()],
        }


class StandardsRegistry:
    """Fail-closed (pack_id, version) registry; ``resolve`` pins or defaults."""

    def __init__(self) -> None:
        self._packs: Dict[Tuple[str, str], StandardsPack] = {}

    def register(self, pack: StandardsPack) -> None:
        key = (pack.pack_id, pack.version)
        if key in self._packs:
            raise StandardsPackError(
                f"标准包 {pack.pack_id}@{pack.version} 已注册（fail-closed）")
        self._packs[key] = pack

    def resolve(
        self, pack_id: str, version: Optional[str] = None, *,
        required: bool = True,
    ) -> Optional[StandardsPack]:
        if version is not None:
            pack = self._packs.get((pack_id, version))
            if pack is None and required:
                raise StandardsPackError(f"标准包 {pack_id}@{version} 未注册")
            return pack
        candidates = sorted(
            (v for (pid, v) in self._packs if pid == pack_id),
            key=lambda v: tuple(int(p) for p in v.split(".")),
        )
        if not candidates:
            if required:
                raise StandardsPackError(f"标准包 {pack_id} 未注册任何版本")
            return None
        return self._packs[(pack_id, candidates[-1])]

    def packs(self) -> List[StandardsPack]:
        return [self._packs[k] for k in sorted(self._packs)]


__all__ = [
    "StandardsPack",
    "StandardsPackError",
    "StandardsRegistry",
]
