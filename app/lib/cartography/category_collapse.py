"""Category Collapse Executor（F10，M5）—— 类别收纳 top-N + Other 的执行 seam.

#1480 的 ``RepresentationChoice.collapse`` 只做声明（grammar_solver.
_collapse_spec）；本模块是它在组件构建层的**唯一执行点**，把三处手写
categorical 发射器收敛到一个口径：

- 图例 entries：保留前 ``keep_classes`` 个类（首现序/传入序）+ 一个
  ``__other__`` 桶（与 cartography_service #783 既有行为逐字节兼容）；
- **数据同口径**：:func:`rewrite_features_with_collapse` 把非保留类的
  要素属性改写为收纳键（``<field>:collapsed`` 新增属性，原字段不动，
  geometry 共享不拷贝）——tooltip/label 消费同一属性即与图例/渲染同口径，
  纠正「颜色循环让第 k+1 类与第 1 类同色」的 silent misleading map；
- **披露**：``collapse`` 元数据随 legend_spec 下发（reason code
  ``GRAMMAR.REP.TOO_MANY_CATEGORIES``），review/QA 可对账。

纯函数、确定性（同输入恒同输出）；无 IO。
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from pydantic import BaseModel, Field

#: 收纳桶的规范键（与 cartography_service 既有 ``__other__`` 一致）。
OTHER_KEY = "__other__"

#: 数据侧收纳属性的后缀（``<field>:collapsed``；tooltip/label 同口径消费面）。
COLLAPSED_PROPERTY_SUFFIX = ":collapsed"

#: 执行披露 reason code（与 grammar_solver._collapse_spec 同码）。
REASON_TOO_MANY_CATEGORIES = "GRAMMAR.REP.TOO_MANY_CATEGORIES"


class CollapseMeta(BaseModel):
    """收纳元数据（legend_spec.collapse 契约面，可序列化、有界）。"""

    kept_keys: List[str] = Field(default_factory=list)
    other_key: str = OTHER_KEY
    other_label: str = "Other"
    observed_classes: int = 0
    reason_code: str = REASON_TOO_MANY_CATEGORIES
    collapsed_property: str = ""      # "<field>:collapsed"（数据侧同口径属性）


class CollapseOutcome(BaseModel):
    """一次收纳执行的结果（entries + 映射 + 元数据 + 披露）。"""

    entries: List[Dict[str, Any]] = Field(default_factory=list)
    kept_values: List[Any] = Field(default_factory=list)
    meta: CollapseMeta = Field(default_factory=CollapseMeta)
    collapsed_count: int = 0          # 被收纳的类别数（非要素数）
    disclosures: List[str] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)

    def collapse_key_for(self, value: Any) -> str:
        """原始类别值 → 收纳后键（保留类=原值字符串；其余=other）。"""
        for kept in self.kept_values:
            if value == kept:
                return str(kept)
        return self.meta.other_key


def apply_collapse(
    categorical_values: Sequence[Any],
    *,
    colors: Sequence[str],
    keep_classes: int,
    other_label: str = "Other",
) -> CollapseOutcome:
    """类别收纳（确定性：保持传入序 = 首现序；颜色按序取模）。

    ``keep_classes``：保留类数（其余并入 Other 桶）。桶色 =
    ``colors[keep_classes % len(colors)]``——与 cartography_service #783
    既有的 ``colors[(k-1) % len]`` 逐字节一致（keep_classes = k-1 时）。
    """
    kept = list(categorical_values[:max(0, int(keep_classes))])
    entries: List[Dict[str, Any]] = [
        {"key": v, "color": colors[i % len(colors)], "label": str(v)}
        for i, v in enumerate(kept)
    ]
    other_color = colors[keep_classes % len(colors)] if colors else "#cccccc"
    entries.append({"key": OTHER_KEY, "color": other_color, "label": other_label})
    collapsed_count = max(0, len(categorical_values) - len(kept))
    return CollapseOutcome(
        entries=entries,
        kept_values=kept,
        meta=CollapseMeta(
            kept_keys=[str(v) for v in kept],
            other_key=OTHER_KEY,
            other_label=other_label,
            observed_classes=len(categorical_values),
            collapsed_property="",
        ),
        collapsed_count=collapsed_count,
        disclosures=[
            f"类别数 {len(categorical_values)} 超过保留上限 {keep_classes}——"
            f"其余 {collapsed_count} 类并入「{other_label}」桶"
            f"（{REASON_TOO_MANY_CATEGORIES}）"
        ],
        reason_codes=[REASON_TOO_MANY_CATEGORIES],
    )


def rewrite_features_with_collapse(
    features: Sequence[Any],
    *,
    field: str,
    outcome: CollapseOutcome,
) -> Tuple[List[Any], int]:
    """要素属性同口径改写（纯函数；原字段不动，新增 ``<field>:collapsed``）。

    返回 (新 features 列表, 改写要素数)。feature/properties 浅拷贝，
    geometry 引用共享（不深拷贝大几何）；非 dict 要素原样透传。
    """
    prop_name = f"{field}{COLLAPSED_PROPERTY_SUFFIX}"
    out: List[Any] = []
    rewritten = 0
    for f in features:
        if not isinstance(f, dict):
            out.append(f)
            continue
        props = f.get("properties")
        new_props = dict(props) if isinstance(props, dict) else {}
        raw = new_props.get(field)
        if raw is not None:
            new_props[prop_name] = outcome.collapse_key_for(raw)
            if new_props[prop_name] == outcome.meta.other_key:
                rewritten += 1
        else:
            new_props[prop_name] = None
        out.append({**f, "properties": new_props})
    return out, rewritten


def attach_collapse_to_spec(
    spec: Dict[str, Any],
    *,
    field: str,
    outcome: CollapseOutcome,
) -> None:
    """把收纳元数据附到 legend_spec（mutation 仅限本键；幂等覆盖）。"""
    if isinstance(spec, dict):
        meta = outcome.meta.model_copy(
            update={"collapsed_property": f"{field}{COLLAPSED_PROPERTY_SUFFIX}"})
        spec["collapse"] = meta.model_dump()


__all__ = [
    "OTHER_KEY",
    "COLLAPSED_PROPERTY_SUFFIX",
    "REASON_TOO_MANY_CATEGORIES",
    "CollapseMeta",
    "CollapseOutcome",
    "apply_collapse",
    "rewrite_features_with_collapse",
    "attach_collapse_to_spec",
]
