"""Layout Constraint Spec v1 — 可序列化布局约束投影（ADR-0283 D1）.

把 V2–V4 求解器的容量/宽度/折叠**入参**升级为可持久化、可对拍的约束
文档：同一份组件序列 + 同一 page_profile 在任何调用点解出同一版面。

与既有事实的关系：
- 求解结果仍是**派生投影**（ADR-0084：不持久化解）—— 本模块序列化的
  是**输入**约束，不是第二真相；
- 缺省派生（:func:`derive_layout_constraints`）从组件描述符词表推导
  （单例 must_fit、可折叠族 collapse、关键说明 no_truncate）—— 缺省输入
  经 :func:`~app.lib.cartography.layout_solver.solve_layout_v5` 的行为与
  V4 一致（回归由 test_layout_constraint_spec_v1 锁定）；
- MapSpec schema 不变：约束文档是组件序列的旁路投影；如后续需要
  ``layout.constraints`` 字段，另行最小 additive 审查（ADR-0283 D1）。

词表来源（只读复用，不复制事实）：
- SINGLETON_TYPES / CANVAS_TYPES：layout_solver；
- COLLAPSIBLE_TYPES：component_composer（AC-07 单一语义源）；
- 关键说明族（no_truncate）：methodology_note / attribution / title /
  subtitle / annotation / uncertainty_panel（文本承载型组件）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.lib.cartography.component_composer import COLLAPSIBLE_TYPES
from app.lib.cartography.layout_solver import SINGLETON_TYPES

#: 约束文档版本标签（closed；消费方按此判别投影形态）。
SPEC_VERSION = "layout_constraint_spec.v1"

#: 溢出处置策略 closed vocab：
#: - keep：保留原位 + 披露（required_overflow_kept 语义）；
#: - collapse：折叠进溢出面板（V4 L3；像素足迹极小）；
#: - suppress：仅 optional 合法 —— 移除 + overflow_suppressed 披露。
OVERFLOW_POLICIES: tuple = ("keep", "collapse", "suppress")

#: 关键说明族（文本承载型；no_truncate 缺省 True —— wrap-not-clip，
#: ADR-0283 D2 在 layout-description 孪生消费）。
KEY_NOTE_TYPES: tuple = (
    "methodology_note", "attribution", "title", "subtitle",
    "annotation", "uncertainty_note", "uncertainty_panel",
)


class SafeAreaMM(BaseModel):
    """出版版心安全边距（毫米；四边独立、非负）。

    live 几何层的 ``layout_geometry.SafeArea`` 是像素口径；本模型是
    **输入**声明的毫米口径，由导出链按 dpi 折算后与图框几何对拍
    （safe_area_violation 发现由几何层发布，本模块只承载声明）。
    """

    top: float = Field(default=10.0, ge=0)
    right: float = Field(default=10.0, ge=0)
    bottom: float = Field(default=10.0, ge=0)
    left: float = Field(default=10.0, ge=0)


class ComponentLayoutConstraint(BaseModel):
    """单组件约束（缺省值 = V4 行为，零回归面）。"""

    component_id: str = Field(min_length=1)
    #: must_fit：溢出画幅下不消失（折叠/保位兜底），绝不出现在 suppressed。
    must_fit: bool = False
    #: no_truncate：关键文本 wrap-not-clip（layout-description 孪生消费）。
    no_truncate: bool = False
    min_width_units: int = Field(default=1, ge=1, le=4)
    max_width_units: int = Field(default=4, ge=1, le=4)
    overflow_policy: Literal["keep", "collapse", "suppress"] = "collapse"

    @model_validator(mode="after")
    def _min_le_max(self) -> "ComponentLayoutConstraint":
        if self.min_width_units > self.max_width_units:
            raise ValueError(
                f"min_width_units ({self.min_width_units}) > "
                f"max_width_units ({self.max_width_units}) for component "
                f"{self.component_id!r}")
        return self


class LayoutConstraintDocument(BaseModel):
    """约束文档（可序列化；``spec_version`` 闭口标签）。"""

    spec_version: str = Field(default=SPEC_VERSION)
    page_profile: str = "viewport"
    key_component_ids: List[str] = Field(default_factory=list)
    safe_area_mm: Optional[SafeAreaMM] = None
    components: Dict[str, ComponentLayoutConstraint] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _version_tag(self) -> "LayoutConstraintDocument":
        if self.spec_version != SPEC_VERSION:
            raise ValueError(
                f"unsupported layout_constraint_spec version "
                f"{self.spec_version!r} (expected {SPEC_VERSION!r})")
        return self

    def constraint_for(self, component_id: str) -> Optional[ComponentLayoutConstraint]:
        return self.components.get(component_id)


def _comp_field(comp: Any, name: str, default: Any = None) -> Any:
    """鸭子型读取（MapSpecComponent / CartographyComponent / Mapping 兼容）。"""
    if isinstance(comp, dict):
        return comp.get(name, default)
    return getattr(comp, name, default)


def derive_layout_constraints(
    components: Iterable[Any],
    *,
    page_profile: str = "viewport",
    safe_area_mm: Optional[SafeAreaMM] = None,
) -> LayoutConstraintDocument:
    """从组件序列推导缺省约束文档（确定性、无 I/O）。

    规则（缺省即 V4 语义的显式化）：
    - enabled=False 的组件不进约束表（不参与布局）；
    - SINGLETON_TYPES（title/north_arrow/scale_bar/attribution…）→
      must_fit=True（V4 required_overflow_kept 语义的声明面）；
    - KEY_NOTE_TYPES → no_truncate=True（wrap-not-clip 消费依据）；
    - COLLAPSIBLE_TYPES → overflow_policy=collapse（V4 L3 准入声明）；
    - 其余组件 → 全缺省（overflow_policy=collapse 与 V4 L3 兜底一致）。
    """
    doc_components: Dict[str, ComponentLayoutConstraint] = {}
    key_ids: List[str] = []
    for comp in components:
        cid = _comp_field(comp, "id")
        ctype = _comp_field(comp, "type")
        if not cid or not ctype:
            continue
        enabled = _comp_field(comp, "enabled", True)
        if enabled is False:
            continue
        must_fit = ctype in SINGLETON_TYPES or ctype in KEY_NOTE_TYPES
        no_truncate = ctype in KEY_NOTE_TYPES
        if must_fit or no_truncate:
            key_ids.append(cid)
        doc_components[cid] = ComponentLayoutConstraint(
            component_id=cid, must_fit=must_fit, no_truncate=no_truncate,
            overflow_policy="collapse" if ctype in COLLAPSIBLE_TYPES else "collapse",
        )
    return LayoutConstraintDocument(
        page_profile=page_profile, key_component_ids=key_ids,
        safe_area_mm=safe_area_mm, components=doc_components,
    )


__all__ = [
    "SPEC_VERSION",
    "OVERFLOW_POLICIES",
    "KEY_NOTE_TYPES",
    "SafeAreaMM",
    "ComponentLayoutConstraint",
    "LayoutConstraintDocument",
    "derive_layout_constraints",
]
