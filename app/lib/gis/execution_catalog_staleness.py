"""ExecutionCatalog Staleness —— per-entry 指纹与精确 stale 解释（F07）。

runtime_manifest 的 ``is_stale_plan`` 回答「这枚全局指纹是否还新鲜」；
本模块补上**归因面**：plan 携带 catalog 快照凭证（generation 指纹 +
可选 per-entry 指纹）后，registry 变化可以精确解释——

- 哪些条目**新增 / 移除 / 内容变化**（版本、绑定、约束…）；
- 变化传导到哪些**消费者**（capability ← algorithm ← tool；recipe ←
  capability）；
- provider/version 弃用关系（supersession map）。

纪律：
- 指纹原语复用 catalog 的 canonical-JSON SHA-256，不发明第二套；
- 空/损坏存储指纹**不判 stale**（与 ``is_stale_plan`` 同一诚实规则，
  防升级即全量作废与损坏记录永久标记）;
- 产出有界（MAX_CHANGED_ENTRIES 披露截断）、排序确定;
- 纯函数：diff 只依赖两份快照/目录内容，不依赖时钟。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.lib.gis.execution_catalog import (
    CATALOG_KINDS,
    KIND_ALGORITHM,
    KIND_CAPABILITY,
    KIND_RECIPE,
    KIND_TOOL,
    ExecutionCatalog,
)

#: 快照凭证 schema 版本（演进时升版；解释器拒绝未知版本并如实披露）。
SNAPSHOT_SCHEMA_VERSION = 1

#: 解释输出中变更条目明细上限（有界披露，超出计数不展开）。
MAX_CHANGED_ENTRIES = 32

#: 变化原因码（稳定词表）。
REASON_CATALOG_VERSION_CHANGED = "catalog_version_changed"
REASON_GENERATION_CHANGED = "catalog_generation_changed"
REASON_ENTRY_ADDED = "entry_added"
REASON_ENTRY_REMOVED = "entry_removed"
REASON_ENTRY_CONTENT_CHANGED = "entry_content_changed"
REASON_MANIFEST_GENERATION_CHANGED = "manifest_generation_changed"
REASON_SNAPSHOT_SCHEMA_UNKNOWN = "snapshot_schema_unknown"
REASON_SNAPSHOT_CORRUPT = "snapshot_corrupt"

def _entry_refs(entry) -> List[str]:
    """条目引用的 catalog 键（``kind:id``；直接引用面，不递归）。"""
    refs: List[str] = []
    if entry.kind in (KIND_CAPABILITY, KIND_ALGORITHM, KIND_RECIPE, KIND_TOOL):
        for cap in entry.capabilities:
            if entry.kind != KIND_CAPABILITY:
                refs.append(f"{KIND_CAPABILITY}:{cap}")
    if entry.kind == KIND_ALGORITHM:
        for t in entry.detail.get("tool_candidates", ()):
            refs.append(f"{KIND_TOOL}:{t}")
    for target in entry.fallback_targets:
        refs.append(f"{entry.kind}:{target}")
    if entry.superseded_by:
        refs.append(f"{entry.kind}:{entry.superseded_by}")
    return refs


def _affected_consumers(
    catalog: ExecutionCatalog,
    changed_keys: Sequence[str],
) -> Dict[str, List[str]]:
    """变化条目 → **直接**消费者（引用它的条目；有界 ≤16/键）。

    单遍引用索引（O(entries + refs)），不递归闭包 —— 归因面要可解释，
    传递影响由消费方沿本映射自行展开。
    """
    index: Dict[str, List[str]] = {}
    for entry in catalog.entries.values():
        consumer = f"{entry.kind}:{entry.id}"
        for ref in _entry_refs(entry):
            bucket = index.setdefault(ref, [])
            if consumer not in bucket:
                bucket.append(consumer)
    out: Dict[str, List[str]] = {}
    for key in changed_keys:
        consumers = index.get(str(key))
        if consumers:
            out[str(key)] = sorted(consumers)[:16]
    return dict(sorted(out.items()))


def catalog_snapshot_ref(
    catalog: ExecutionCatalog,
    entry_keys: Optional[Sequence[str]] = None,
    *,
    manifest_fingerprint: str = "",
) -> Dict[str, Any]:
    """构造 plan 可携带的最小 stale 凭证。

    ``entry_keys``：``"kind:id"`` 子集（plan 实际消费的条目）→ scope=
    ``partial``（只对这些键的 removed/changed 负责 —— 无关条目变化不判
    stale，这正是「精确 stale」的语义）；缺省 = 全量快照（scope=``full``，
    超出 MAX_CHANGED_ENTRIES 的部分截断披露）。
    """
    fingerprints = catalog.entry_fingerprints()
    if entry_keys is not None:
        wanted = [str(k) for k in entry_keys[:512]]
        fingerprints = {k: v for k, v in fingerprints.items() if k in set(wanted)}
        scope = "partial"
    else:
        scope = "full"
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "scope": scope,
        "catalog_version": catalog.catalog_version,
        "generation_fingerprint": catalog.generation_fingerprint,
        "manifest_fingerprint": str(manifest_fingerprint or "")[:64],
        "entry_fingerprints": dict(sorted(fingerprints.items())),
    }
    return payload


def validate_snapshot_ref(snapshot: Mapping[str, Any]) -> Optional[str]:
    """快照凭证健康检查；返回损坏/不认识原因码，健康返回 None。

    损坏 ≠ 不新鲜：损坏的快照**不构成**「registry 世代变化」的证据。
    """
    if not isinstance(snapshot, Mapping):
        return REASON_SNAPSHOT_CORRUPT
    version = snapshot.get("schema_version")
    if version is None:
        # 历史快照（无版本字段）：按 v1 语义可读 —— 结构不符会在下方暴露。
        version = 1
    if version != SNAPSHOT_SCHEMA_VERSION:
        return REASON_SNAPSHOT_SCHEMA_UNKNOWN
    gen = snapshot.get("generation_fingerprint")
    if not gen or not isinstance(gen, str) or len(gen) != 32:
        return REASON_SNAPSHOT_CORRUPT
    fps = snapshot.get("entry_fingerprints", {})
    if fps is not None and not isinstance(fps, Mapping):
        return REASON_SNAPSHOT_CORRUPT
    return None


@dataclass
class CatalogDiff:
    """两份快照的条目级差异（有界、排序确定）。"""

    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def truncated_total(self) -> int:
        return max(0, len(self.added) + len(self.removed) + len(self.changed)
                   - MAX_CHANGED_ENTRIES)

    def bounded(self) -> "CatalogDiff":
        return CatalogDiff(
            added=self.added[:MAX_CHANGED_ENTRIES],
            removed=self.removed[:MAX_CHANGED_ENTRIES],
            changed=self.changed[:MAX_CHANGED_ENTRIES],
        )

    def to_dict(self) -> Dict[str, Any]:
        bounded = self.bounded()
        return {
            "added": list(bounded.added),
            "removed": list(bounded.removed),
            "changed": list(bounded.changed),
            "truncated": self.truncated_total(),
        }


def diff_snapshots(
    old_entry_fingerprints: Mapping[str, str],
    new_entry_fingerprints: Mapping[str, str],
) -> CatalogDiff:
    """条目指纹 diff（纯函数；键 ``kind:id``，排序确定）。"""
    old_keys = set(old_entry_fingerprints.keys())
    new_keys = set(new_entry_fingerprints.keys())
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    changed = sorted(
        k for k in (old_keys & new_keys)
        if str(old_entry_fingerprints[k]) != str(new_entry_fingerprints[k])
    )
    return CatalogDiff(added=added, removed=removed, changed=changed)


def supersession_map(catalog: ExecutionCatalog) -> Dict[str, Dict[str, str]]:
    """弃用 → 替代关系（``kind:id → {successor, kind}``；有界排序）。"""
    out: Dict[str, Dict[str, str]] = {}
    for entry in sorted(catalog.entries.values(), key=lambda e: (e.kind, e.id)):
        if not entry.deprecated:
            continue
        successor = entry.superseded_by
        if not successor and entry.fallback_targets:
            successor = list(entry.fallback_targets)[0]
        out[f"{entry.kind}:{entry.id}"] = {
            "successor": successor[:128],
            "kind": entry.kind,
            "successor_known": bool(
                successor and catalog.get(entry.kind, successor) is not None),
        }
    return dict(sorted(out.items()))


def explain_staleness(
    stored_snapshot: Mapping[str, Any],
    current_catalog: ExecutionCatalog,
    *,
    current_manifest_fingerprint: str = "",
) -> Dict[str, Any]:
    """解释一份存储快照相对当前 catalog 的 stale 面貌（有界、确定）。

    返回::

        {stale, reasons[], diff{}, affected_consumers{}, summary}

    - 健康快照 + 同 generation → stale=False（无理由）;
    - 健康快照 + 异 generation → 条目级 diff + 消费者归因;
    - 损坏/未知版本快照 → stale=True 但 reasons 只含披露码，diff 为空
      （诚实：无法归因 ≠ 无变化）。
    """
    health = validate_snapshot_ref(stored_snapshot)
    reasons: List[str] = []
    diff_payload: Dict[str, Any] = {}
    affected: Dict[str, List[str]] = {}

    if health is not None:
        reasons.append(health)
        return {
            "stale": True,
            "reasons": reasons,
            "diff": diff_payload,
            "affected_consumers": affected,
            "summary": {
                "changed_total": 0,
                "truncated": False,
                "catalog_version_changed": False,
                "note": "snapshot unreadable; staleness not attributable",
            },
        }

    current_fps = current_catalog.entry_fingerprints()
    stored_fps = dict(stored_snapshot.get("entry_fingerprints", {}) or {})
    stored_gen = str(stored_snapshot.get("generation_fingerprint", ""))
    stored_catalog_version = int(
        stored_snapshot.get("catalog_version", current_catalog.catalog_version))
    scope = str(stored_snapshot.get("scope", "full") or "full")
    if stored_catalog_version != current_catalog.catalog_version:
        reasons.append(REASON_CATALOG_VERSION_CHANGED)
    manifest_fp = str(stored_snapshot.get("manifest_fingerprint", "") or "")
    if (current_manifest_fingerprint and manifest_fp
            and manifest_fp != current_manifest_fingerprint):
        reasons.append(REASON_MANIFEST_GENERATION_CHANGED)

    if scope == "partial":
        # partial 快照只对自己携带的键负责：removed / changed 可判；
        # added 与 generation 面无归因依据（不判，绝不猜）。
        removed = sorted(k for k in stored_fps if k not in current_fps)
        changed = sorted(
            k for k in (set(stored_fps) & set(current_fps))
            if str(stored_fps[k]) != str(current_fps[k]))
        diff = CatalogDiff(removed=removed, changed=changed)
    else:
        diff = diff_snapshots(stored_fps, current_fps)
        if stored_gen != current_catalog.generation_fingerprint:
            reasons.append(REASON_GENERATION_CHANGED)
        if diff.added:
            reasons.append(REASON_ENTRY_ADDED)
    if diff.removed:
        reasons.append(REASON_ENTRY_REMOVED)
    if diff.changed:
        reasons.append(REASON_ENTRY_CONTENT_CHANGED)

    changed_keys = list(diff.added) + list(diff.removed) + list(diff.changed)
    affected = _affected_consumers(current_catalog, changed_keys)

    stale = bool(reasons)
    diff_payload = diff.to_dict()
    return {
        "stale": stale,
        "reasons": reasons,
        "diff": diff_payload,
        "affected_consumers": affected,
        "summary": {
            "changed_total": len(changed_keys),
            "truncated": bool(diff.truncated_total()),
            "catalog_version_changed": (
                stored_catalog_version != current_catalog.catalog_version),
        },
    }


__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "MAX_CHANGED_ENTRIES",
    "REASON_CATALOG_VERSION_CHANGED",
    "REASON_GENERATION_CHANGED",
    "REASON_ENTRY_ADDED",
    "REASON_ENTRY_REMOVED",
    "REASON_ENTRY_CONTENT_CHANGED",
    "REASON_MANIFEST_GENERATION_CHANGED",
    "REASON_SNAPSHOT_SCHEMA_UNKNOWN",
    "REASON_SNAPSHOT_CORRUPT",
    "CatalogDiff",
    "catalog_snapshot_ref",
    "validate_snapshot_ref",
    "diff_snapshots",
    "supersession_map",
    "explain_staleness",
]
