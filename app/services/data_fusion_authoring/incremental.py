"""增量维护 —— 来源版本刷新的影响面与人工锁定保留。

语义（WP4；reviewer 重点）：
- 变化检测以**实体内容指纹**为锚（pair_key 同源）：added/removed/
  changed 三态，绝不以行号/顺序判断"同一实体"；
- 人工锁定（locked decision）跨刷新保留：两端指纹都未变的决定自动
  继承；一端变化的决定**不删除**但标 stale（审阅面板可见，绝不无声
  推翻 —— 显式改决或解除后生效）；
- 安全阈：受影响实体占比超 ``INCREMENTAL_AFFECTED_RATIO_CAP`` →
  typed ``refresh_not_safe``（要求显式全量重算预算），绝不静默放大
  变更面；
- 本模块是纯计算：diff/影响面/挂靠 —— 重放执行仍走
  ``service.execute_plan``（durable job 同一路径），不另起执行器。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.services.data_fusion_authoring import (
    adjudication,
    contracts,
)
from app.services.data_fusion_authoring.errors import DataFusionError
from app.services.data_fusion_authoring.plan import FusionPlan


@dataclass
class EntityDiff:
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)
    unchanged: int = 0

    @property
    def affected(self) -> int:
        return len(self.added) + len(self.removed) + len(self.changed)

    def to_dict(self, *, sample_limit: int = 20) -> Dict[str, Any]:
        return {
            "added": self.added[:sample_limit],
            "removed": self.removed[:sample_limit],
            "changed": self.changed[:sample_limit],
            "added_total": len(self.added),
            "removed_total": len(self.removed),
            "changed_total": len(self.changed),
            "unchanged": self.unchanged,
            "affected": self.affected,
            "sample_limit": sample_limit,
        }


def fingerprint_records(
    recs: List, plan: FusionPlan, source_ref: str, side: str
) -> Dict[str, Dict[str, Any]]:
    """记录列表 → {指纹: 实体投影}（diff 锚；键与 pair_key 同函数）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for rec in recs:
        fp = adjudication.entity_fingerprint(side, rec, plan, source_ref)
        out[fp] = {
            "fingerprint": fp,
            "index": rec.index,
            "name_norm": rec.name_norm,
            "ext_id": rec.ext_id,
            "temporal": rec.temporal,
        }
    return out


def diff_side(
    old_recs: List, new_recs: List,
    plan: FusionPlan, source_ref: str, side: str,
) -> EntityDiff:
    """单侧实体 diff（内容指纹三态；变更 = 键同而内容异）。

    ``changed``：指纹不同但业务身份（id 或归一化名称）可对应的实体
    —— 即"同一实体的内容变化"（名称/几何/属性更新），区别于纯增删。
    """
    old_map = fingerprint_records(old_recs, plan, source_ref, side)
    new_map = fingerprint_records(new_recs, plan, source_ref, side)
    diff = EntityDiff()
    # 业务身份键（用于 changed 归并）：id 优先，其次归一化名称
    def _identity(entry: Dict[str, Any]) -> str:
        if entry.get("ext_id"):
            return f"id:{entry['ext_id']}"
        return f"name:{entry.get('name_norm') or ''}"

    old_by_identity: Dict[str, Dict[str, Any]] = {}
    for fp, entry in old_map.items():
        old_by_identity.setdefault(_identity(entry), entry)
    for fp, entry in new_map.items():
        ident = _identity(entry)
        old_entry = old_map.get(fp)
        if old_entry is not None:
            diff.unchanged += 1
            continue
        prev = old_by_identity.get(ident)
        if prev is not None:
            diff.changed.append(ident)
        else:
            diff.added.append(ident)
    new_identities = {_identity(e) for e in new_map.values()}
    for ident in old_by_identity:
        if ident not in new_identities:
            diff.removed.append(ident)
    return diff


@dataclass
class RefreshImpact:
    """刷新影响面（typed 报告；决定挂靠预测）。"""

    added_a: int
    removed_a: int
    added_b: int
    removed_b: int
    total_new: int
    affected_ratio: float
    safe: bool
    decisions_inherited: int
    decisions_stale: List[str] = field(default_factory=list)
    decisions_orphan: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "side_a": {"added": self.added_a, "removed": self.removed_a},
            "side_b": {"added": self.added_b, "removed": self.removed_b},
            "total_new": self.total_new,
            "affected_ratio": round(self.affected_ratio, 6),
            "cap": contracts.INCREMENTAL_AFFECTED_RATIO_CAP,
            "safe": self.safe,
            "decisions_inherited": self.decisions_inherited,
            "decisions_stale": self.decisions_stale[:20],
            "decisions_stale_total": len(self.decisions_stale),
            "decisions_orphan": self.decisions_orphan,
            "verdict": (
                "incremental_safe" if self.safe
                else "refresh_not_safe（全量重算并给出预算）"),
        }


def compute_impact_from_key_sets(
    plan: FusionPlan,
    new_recs_a: List, new_recs_b: List,
    old_keys_a: set, old_keys_b: set,
    ref_a: str, ref_b: str,
    decisions_by_key: Dict[str, Dict[str, Any]],
) -> RefreshImpact:
    """键集合口径的影响面（基底包冻结完整旧键集 → diff 完整诚实）。

    决定三态：
    - inherited：两端实体在新快照中均存在（指纹对仍成立）→ 自动继承；
    - stale：一端消失 —— 决定保留但需复审（绝不无声推翻）；
    - orphan：两端都消失 —— 决定悬空（保留在决定表，commit 不应用）。
    """
    new_keys_a = set(fingerprint_records(new_recs_a, plan, ref_a, "a"))
    new_keys_b = set(fingerprint_records(new_recs_b, plan, ref_b, "b"))
    added_a, removed_a = new_keys_a - old_keys_a, old_keys_a - new_keys_a
    added_b, removed_b = new_keys_b - old_keys_b, old_keys_b - new_keys_b
    total = max(1, len(new_keys_a) + len(new_keys_b))
    affected = (len(added_a) + len(removed_a)
                + len(added_b) + len(removed_b))
    ratio = affected / total

    inherited_n = 0
    stale: List[str] = []
    orphan_n = 0
    for key in decisions_by_key:
        if not key.startswith("fp:"):
            continue
        parts = key[3:].split(":")
        if len(parts) != 2:
            continue
        fa, fb = parts
        a_ok = fa in new_keys_a
        b_ok = fb in new_keys_b
        if a_ok and b_ok:
            inherited_n += 1
        elif a_ok or b_ok:
            stale.append(key)
        else:
            orphan_n += 1

    return RefreshImpact(
        added_a=len(added_a), removed_a=len(removed_a),
        added_b=len(added_b), removed_b=len(removed_b),
        total_new=total, affected_ratio=ratio,
        safe=ratio <= contracts.INCREMENTAL_AFFECTED_RATIO_CAP,
        decisions_inherited=inherited_n, decisions_stale=stale,
        decisions_orphan=orphan_n,
    )


def assert_refresh_safe(impact: RefreshImpact) -> None:
    if not impact.safe:
        raise DataFusionError(
            "refresh_not_safe",
            f"来源变更面 {impact.affected_ratio:.1%} 超过安全阈 "
            f"{contracts.INCREMENTAL_AFFECTED_RATIO_CAP:.0%}："
            "请执行全量重算（同计划直接 execute；决定仍按内容键继承）",
            affected_ratio=round(impact.affected_ratio, 6),
            cap=contracts.INCREMENTAL_AFFECTED_RATIO_CAP,
        )
