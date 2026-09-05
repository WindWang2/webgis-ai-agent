"""执行计划规范化（ADR-0101 D2）：指纹前的 canonical 归一化。

目标（V4 §5）：同一语义计划在不同等价写法（字典顺序、等价 CRS 拼写、
tuple/list、set 顺序、非 JSON 原生值的稳定表示）下必须产出**同一指纹**。

设计约束：
- `canonicalize` 是纯函数、有界（深度上限），失败降级为确定性标记而非
  抛异常 —— 指纹路径绝不允许因奇异参数值中途崩溃；
- `canonicalize_strict` 用于**校验路径**：出现不可归一化对象（降级标记）
  时显式拒绝，把问题在 admission 前暴露，而不是执行中。
"""
from __future__ import annotations

import math
import re
from typing import Any

#: canonical 化的最大递归深度（防深嵌套参数；超出按字符串稳定表示）。
MAX_CANONICAL_DEPTH = 24

#: 降级标记前缀：非 JSON 原生对象在 canonical 形式里以此前缀出现。
_PY_MARKER = "__py__"

_HEX_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")

#: CRS 拼写归一化：`urn:ogc:def:crs:EPSG::4326` / `epsg:4326` / `EPSG:4326`
#: 是同一语义。仅处理可证等价的形式，其余原样返回（不猜）。
_URN_EPSG_RE = re.compile(r"^urn:ogc:def:crs:([A-Za-z0-9_]+)::(\d+)$", re.IGNORECASE)
_AUTHORITY_CODE_RE = re.compile(r"^([A-Za-z0-9_]+):(\d+)$")


def scrub_addresses(text: str) -> str:
    """把默认 repr 中的内存地址（0x…）擦成 0x —— 同值对象跨进程稳定。"""
    return _HEX_ADDR_RE.sub("0x", text)


def normalize_crs_ref(crs: Any) -> str:
    """CRS 引用归一化（仅大小写/URN 前缀等可证等价形式）。

    ``urn:ogc:def:crs:EPSG::4326`` → ``EPSG:4326``；
    ``epsg:4326`` → ``EPSG:4326``。其余输入 strip 后原样返回。
    """
    text = str(crs).strip()
    m = _URN_EPSG_RE.match(text)
    if m:
        return f"{m.group(1).upper()}:{m.group(2)}"
    m = _AUTHORITY_CODE_RE.match(text)
    if m and m.group(1).upper() in {"EPSG", "ESRI", "IAU", "OGC"}:
        return f"{m.group(1).upper()}:{m.group(2)}"
    return text


def canonicalize(value: Any, *, _depth: int = 0) -> Any:
    """递归 canonical 形式（指纹输入）。

    - dict → 键排序（键强制 str）；tuple → list；set/frozenset → 排序列表；
    - Enum → .value；非有限 float → 确定性字符串标记；
    - 非 JSON 原生对象 → ``__py__:<qualname>:<scrubbed repr>``（确定性但
      **不可证明稳定** —— strict 模式会拒绝它）。
    """
    if _depth > MAX_CANONICAL_DEPTH:
        return f"{_PY_MARKER}:depth_overflow:{type(value).__qualname__}"
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "__float__:nan"
        if math.isinf(value):
            return "__float__:+inf" if value > 0 else "__float__:-inf"
        return value
    if isinstance(value, dict):
        out = {}
        for k in sorted(value.keys(), key=lambda k: str(k)):
            out[str(k)] = canonicalize(value[k], _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [canonicalize(v, _depth=_depth + 1) for v in value]
    if isinstance(value, (set, frozenset)):
        items = [canonicalize(v, _depth=_depth + 1) for v in value]
        return sorted(items, key=lambda v: json_key(v))
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, bool, float)):
        return canonicalize(enum_value, _depth=_depth + 1)
    # 非 JSON 原生对象：确定性降级表示（repr 擦地址；越界截断）。
    try:
        repr_text = scrub_addresses(repr(value))
    except Exception:  # pragma: no cover - repr 自爆的对象
        repr_text = "<unrepr>"
    return f"{_PY_MARKER}:{type(value).__qualname__}:{repr_text[:512]}"


def json_key(value: Any) -> str:
    """canonical 值的排序键（set 归一用；不必可逆）。"""
    import json

    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # pragma: no cover - canonicalize 输出必可 dumps
        return str(value)


def canonical_dumps(value: Any) -> str:
    """canonical JSON 序列化（键排序、无 ASCII 转义、确定性降级）。"""
    import json

    return json.dumps(canonicalize(value), sort_keys=True, ensure_ascii=False)


def canonicalize_strict(value: Any, *, _depth: int = 0) -> Any:
    """同 canonicalize，但拒绝任何 ``__py__`` 降级标记（校验路径用）。"""
    canonical = canonicalize(value, _depth=_depth)
    if _contains_py_marker(canonical):
        raise ValueError(
            "plan parameters contain non-JSON-native values; "
            "use JSON-serializable parameters for a stable fingerprint"
        )
    return canonical


def _contains_py_marker(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith(_PY_MARKER + ":") or value.startswith("__float__:")
    if isinstance(value, dict):
        return any(_contains_py_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_py_marker(v) for v in value)
    return False
