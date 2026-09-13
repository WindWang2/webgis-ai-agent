"""SkillComposition —— 作业阶段级技能组合（ADR-0182 §2.4；goal S5）。

真实任务常需要多个技能协作（例：遥感变化检测分析 + 变化结果制图 +
行政统计 + 成果交付）。组合是**显式声明**的有序结构，不是任意递归：

- depth ≤ ``MAX_COMPOSITION_DEPTH``（缺省 4）；
- 无环（成员间 depends_on 显式边，Kahn 检测）；
- 悬空成员引用 fatal（loader/validation 期拦下）；
- 执行顺序 = 确定性拓扑序（Kahn；同层按声明序 → id 字典序）。

与 ``workflow_families.CompositeRecipe`` 的划界：CompositeRecipe 编排
**plan 内制图层**（base 产品 + 条件 supporting 层）；SkillComposition
编排**作业阶段**（分析 → 制图 → 统计 → 交付）。二者可嵌套引用但不互替。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from app.services.gis_harness.skills._base import SkillAssetModel

#: 组合深度上限。V1 成员是**扁平**技能引用（depth=1，结构上不可嵌套
#: 组合）；常量为将来嵌套引用演进预留的护栏，见 ADR-0182 §2.4。
MAX_COMPOSITION_DEPTH = 4

#: 成员角色词表（作业阶段语义）。
COMPOSITION_ROLES = (
    "primary",        # 主分析/主产品技能（verdict 承载者）
    "supporting",     # 支撑分析（统计/聚合/……）
    "presentation",   # 表达与交付（制图设计/出版/……）
)


class CompositionMember(SkillAssetModel):
    """一个被组合的技能：角色 + 显式依赖。"""
    skill_id: str
    role: str = "supporting"           # ⊆ COMPOSITION_ROLES
    depends_on: List[str] = Field(default_factory=list)  # 前置成员 skill_id
    # 并入条件（资格状态语义，对齐 CompositeRecipe.supporting_requires_states）
    requires_states: List[str] = Field(default_factory=list)
    disclosure: str = ""               # 该成员的并入语义披露（空=无）


class SkillComposition(SkillAssetModel):
    """显式技能组合（审定资产；引用必须真实存在）。"""
    composition_id: str
    label_zh: str
    label_en: str = ""
    description: str = ""
    members: List[CompositionMember] = Field(min_length=1)

    def validate_composition(self, skill_id_exists: Any) -> List[str]:
        violations: List[str] = []
        ids = [m.skill_id for m in self.members]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            violations.append(f"composition[{self.composition_id}]: duplicate members {dupes}")
        known = set(ids)
        for m in self.members:
            if m.role not in COMPOSITION_ROLES:
                violations.append(
                    f"composition[{self.composition_id}]: member {m.skill_id} "
                    f"unknown role {m.role}")
            for dep in m.depends_on:
                if dep not in known:
                    violations.append(
                        f"composition[{self.composition_id}]: member {m.skill_id} "
                        f"依赖未声明成员 {dep}")
            for st in m.requires_states:
                if st not in ("eligible", "transform_required", "degraded"):
                    violations.append(
                        f"composition[{self.composition_id}]: member {m.skill_id} "
                        f"unknown require state {st}")
        if skill_id_exists is not None:
            for sid in ids:
                if not skill_id_exists(sid):
                    violations.append(
                        f"composition[{self.composition_id}]: unknown skill {sid}")
        violations.extend(_check_cycle(self.composition_id, self.members))
        # 必须有恰好一个 primary（verdict 承载者唯一）
        primaries = [m for m in self.members if m.role == "primary"]
        if len(primaries) != 1:
            violations.append(
                f"composition[{self.composition_id}]: 需要恰好 1 个 primary 成员"
                f"（当前 {len(primaries)}）")
        return violations

    def execution_order(self) -> List[str]:
        """确定性执行顺序：Kahn 拓扑（同层按声明序 → id 字典序）。"""
        declared = {m.skill_id: i for i, m in enumerate(self.members)}
        indeg: Dict[str, int] = {sid: 0 for sid in declared}
        out: Dict[str, List[str]] = {sid: [] for sid in declared}
        for m in self.members:
            for dep in m.depends_on:
                if dep in declared:
                    out[dep].append(m.skill_id)
                    indeg[m.skill_id] += 1
        def _key(sid: str) -> tuple:
            return (declared[sid], sid)
        queue = sorted((sid for sid, d in indeg.items() if d == 0), key=_key)
        order: List[str] = []
        while queue:
            sid = queue.pop(0)
            order.append(sid)
            for nxt in sorted(out[sid], key=_key):
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)
        return order if len(order) == len(declared) else sorted(declared)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "composition_id": self.composition_id[:64],
            "label_zh": self.label_zh[:48],
            "execution_order": self.execution_order()[:8],
            "members": [
                {"skill_id": m.skill_id[:64], "role": m.role,
                 "depends_on": list(m.depends_on)[:4]}
                for m in self.members[:8]
            ],
        }


def _check_cycle(composition_id: str, members: List[CompositionMember]) -> List[str]:
    declared = {m.skill_id for m in members}
    indeg: Dict[str, int] = {sid: 0 for sid in declared}
    out: Dict[str, List[str]] = {sid: [] for sid in declared}
    for m in members:
        for dep in m.depends_on:
            if dep in declared:
                out[dep].append(m.skill_id)
                indeg[m.skill_id] += 1
    queue = sorted(sid for sid, d in indeg.items() if d == 0)
    seen = 0
    while queue:
        sid = queue.pop(0)
        seen += 1
        for nxt in sorted(out[sid]):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if seen != len(declared):
        cyclic = sorted(sid for sid, d in indeg.items() if d > 0)
        return [f"composition[{composition_id}]: depends_on 环 involving {cyclic[:8]}"]
    return []


class CompositionRegistry:
    """组合登记表（审定工件；V1 提供 core 组合，纯加法演进）。"""

    def __init__(self, compositions: Optional[List[SkillComposition]] = None) -> None:
        self._by_id: Dict[str, SkillComposition] = {}
        for c in compositions or []:
            if c.composition_id in self._by_id:
                raise ValueError(f"CompositionRegistry: duplicate {c.composition_id}")
            self._by_id[c.composition_id] = c

    def get(self, composition_id: str) -> Optional[SkillComposition]:
        return self._by_id.get(composition_id)

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id)

    def __len__(self) -> int:
        return len(self._by_id)


__all__ = [
    "MAX_COMPOSITION_DEPTH",
    "COMPOSITION_ROLES",
    "CompositionMember",
    "SkillComposition",
    "CompositionRegistry",
]
