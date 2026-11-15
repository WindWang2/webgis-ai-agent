"""G38 类型化键规范化 —— 空键与类型政策在本域进入任何连接原语之前落定。

政策（J05 锚点；与 federation ``_hashable_key`` 数值归一语义对齐并显式扩展）：

- null / 字段缺失：**双侧一律不参与匹配**（null 对 null 也不匹配 —— 与
  pandas merge 的 null 互配语义相反，本域以显式政策先行，杜绝依赖原语默认值）。
- 类型分族比较：``"001" != "1" != 1 != True != ""``。
  * 字符串是字符串；空串是合法值（≠ null/missing）；
  * 数值（int/float，排除 bool）统一按数值比较：1 == 1.0（与 federation
    numeric normalization 一致——同一数据经不同存储类型读出仍可对上）；
  * bool 独立成族（Python ``isinstance(True, int)`` 陷阱显式排除）；
  * 其它类型（list/dict 等）不做静默字符串化 —— typed 阻断。
- 显式字符串变换（trim/casefold/zfill）由用户声明、计入计划 digest、
  绝不自动清洗。

规范化产物是可哈希的元组（复合键 = 逐字段规范化后组成的元组），
供哈希索引/探测/诊断统一消费 —— 保证所有执行面同一政策。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_join_authoring import contracts
from app.services.data_join_authoring.errors import DataJoinError


@dataclass(frozen=True)
class KeyFieldSpec:
    """一个键字段（左/右各一列；复合键 = 多个 spec）。

    ``transform`` 仅作用于字符串键值；数值/布尔键族不受字符串变换影响
    （变换被跳过而非拒绝 —— 键族语义优先于文本清洗，不做隐式 str()）。
    """

    left_field: str
    right_field: str
    transform: Optional[str] = None
    zfill_width: int = 0

    def __post_init__(self) -> None:
        if self.transform is not None:
            if self.transform not in contracts.KEY_TRANSFORMS:
                raise DataJoinError(
                    "invalid_plan",
                    f"未知键变换 {self.transform!r}；词表 "
                    f"{sorted(contracts.KEY_TRANSFORMS)}",
                    transform=self.transform,
                )
            if self.transform == "zfill":
                if not (1 <= int(self.zfill_width) <= 32):
                    raise DataJoinError(
                        "invalid_plan", "zfill 宽度必须在 1..32",
                        zfill_width=self.zfill_width,
                    )


def _apply_transform(value: str, spec: KeyFieldSpec) -> str:
    t = spec.transform
    if t is None:
        return value
    if t == "trim":
        return value.strip()
    if t == "casefold":
        return value.casefold()
    if t == "zfill":
        return value.zfill(int(spec.zfill_width))
    raise DataJoinError("invalid_plan", f"未知键变换 {t!r}")  # pragma: no cover


def canonical_key_value(
    value: Any, spec: KeyFieldSpec
) -> Optional[Tuple[str, Any]]:
    """单值 → 规范键值；null/missing → None（不参与匹配）。

    返回 ``(族标记, 规范值)``：字符串 ``("s", v)``、数值 ``("n", float)``、
    布尔 ``("b", v)``。类型不可比（list/dict 等）→ typed 阻断。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return ("b", value)
    if isinstance(value, (int, float)):
        f = float(value)
        if f != f or f in (float("inf"), float("-inf")):  # NaN/Inf 不可作键
            return None
        return ("n", f)
    if isinstance(value, str):
        s = _apply_transform(value, spec)
        # 空串是合法字符串值（与 null/missing 区分）—— 不折叠。
        return ("s", s)
    raise DataJoinError(
        "type_conflict",
        f"键字段 {spec.left_field!r} 出现不可比类型 "
        f"{type(value).__name__}（不支持 list/dict 等作为连接键）",
        field=spec.left_field,
        python_type=type(value).__name__,
    )


def row_key(
    row: Dict[str, Any], specs: List[KeyFieldSpec], side: str
) -> Optional[Tuple[Tuple[str, Any], ...]]:
    """一行 → 复合规范键；任一字段 null/missing → 整键 None（不参与匹配）。

    ``side`` ∈ {"left","right"}：从行中取对应侧字段名。
    """
    out: List[Tuple[str, Any]] = []
    for spec in specs:
        field = spec.left_field if side == "left" else spec.right_field
        raw = row.get(field)
        if raw is None:
            return None
        canon = canonical_key_value(raw, spec)
        if canon is None:
            return None
        out.append(canon)
    return tuple(out)


def describe_key_type(pairs: List[Tuple[bool, Any]]) -> Dict[str, Any]:
    """诊断用：一个键列的值类型分布（有界统计）。

    ``pairs`` = (字段是否缺失, 值)；缺失与显式 null 分桶披露。
    """
    counts: Dict[str, int] = {"null": 0, "missing": 0, "string": 0,
                              "number": 0, "bool": 0, "other": 0}
    samples: Dict[str, List[str]] = {}
    for missing, v in pairs:
        if missing:
            counts["missing"] += 1
            continue
        if v is None:
            counts["null"] += 1
            continue
        if isinstance(v, bool):
            k = "bool"
        elif isinstance(v, (int, float)):
            k = "number"
        elif isinstance(v, str):
            k = "string"
        else:
            k = "other"
        counts[k] += 1
        if len(samples.get(k, [])) < contracts.DIAG_FIELD_SAMPLE_LIMIT:
            samples.setdefault(k, []).append(repr(v)[:64])
    return {"type_counts": counts, "samples": samples}
