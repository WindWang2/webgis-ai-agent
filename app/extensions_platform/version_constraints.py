"""依赖版本约束语法（ADR-0105 V2 / Wave 8 的确定性叶子模块）。

语法（有意保持最小、零依赖、与 PEP 440 无关——扩展版本就是 X.Y[.Z]
semver，见 api_version.parse_version）::

    constraint   := spec ("," spec)*
    spec         := op version
    op           := ">=" | "<" | "<=" | ">" | "==" | "!="

- 逗号分隔的多个 spec 是 AND 语义；
- 空串 / 纯空白非法（声明了就说明有约束意图）；
- 满足判定是纯函数：``satisfied(version, constraint) -> bool``。

任何解析失败返回可读错误消息（不抛异常），由 manifest 解析期与 resolver
分别决定 fail-closed 策略。
"""

from __future__ import annotations

from typing import Optional, Tuple

from .api_version import parse_version

_OPERATORS: Tuple[str, ...] = (">=", "<=", "==", "!=", "<", ">")
_MAX_SPECS = 8
_MAX_CONSTRAINT_CHARS = 64


def validate_constraint_syntax(constraint: str) -> Optional[str]:
    """校验约束串语法。合法返回 None；非法返回可读错误消息。"""
    if not isinstance(constraint, str):
        return f"constraint must be a string, got {type(constraint).__name__}"
    if not constraint.strip():
        return "constraint must not be empty"
    if len(constraint) > _MAX_CONSTRAINT_CHARS:
        return f"constraint exceeds {_MAX_CONSTRAINT_CHARS} characters"
    specs = [s.strip() for s in constraint.split(",")]
    if len(specs) > _MAX_SPECS:
        return f"constraint has more than {_MAX_SPECS} specs"
    for spec in specs:
        op = next((o for o in _OPERATORS if spec.startswith(o)), None)
        if op is None:
            return f"spec {spec!r} must start with one of {', '.join(_OPERATORS)}"
        version = spec[len(op):].strip()
        if parse_version(version) is None:
            return f"spec {spec!r}: {version!r} is not X.Y[.Z] semver"
    return None


def satisfied(version: str, constraint: str) -> bool:
    """版本是否满足约束（语法非法一律 False——fail closed）。"""
    if validate_constraint_syntax(constraint) is not None:
        return False
    v = parse_version(version)
    if v is None:
        return False
    for spec in constraint.split(","):
        spec = spec.strip()
        op = next((o for o in _OPERATORS if spec.startswith(o)), "")
        bound = parse_version(spec[len(op):].strip())
        assert bound is not None
        if op == ">=" and not v >= bound:
            return False
        if op == "<" and not v < bound:
            return False
        if op == "<=" and not v <= bound:
            return False
        if op == ">" and not v > bound:
            return False
        if op == "==" and v != bound:
            return False
        if op == "!=" and v == bound:
            return False
    return True
