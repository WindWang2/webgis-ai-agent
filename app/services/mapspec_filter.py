"""Bounded MapLibre filter subset shared with the SVG frontend export.

Unknown expression nodes reject the entire layer, including unused branches.
"""
from __future__ import annotations

from typing import Any
from app.contracts.geojson import GeoJSONFeature


def supported_filter(expr: Any, depth: int = 0) -> bool:
    if depth > 32:
        return False
    if isinstance(expr, bool):
        return True
    if not isinstance(expr, list) or not expr:
        return False

    def operand(value: Any, level: int = depth) -> bool:
        if level > 32:
            return False
        if not isinstance(value, list):
            return True
        if not value:
            return False
        op = value[0]
        if op == "get":
            return len(value) == 2 and isinstance(value[1], str)
        if op in ("id", "geometry-type"):
            return len(value) == 1
        if op == "literal":
            return len(value) == 2
        return op == "to-string" and len(value) == 2 and operand(value[1], level + 1)

    op = expr[0]
    if op in ("all", "any"):
        return all(supported_filter(v, depth + 1) for v in expr[1:])
    if op == "!":
        return len(expr) == 2 and supported_filter(expr[1], depth + 1)
    if op in ("==", "!=", ">", ">=", "<", "<=", "in"):
        return len(expr) == 3 and operand(expr[1]) and operand(expr[2])
    if op == "has":
        return len(expr) == 2 and isinstance(expr[1], str)
    if op == "match" and len(expr) >= 5 and len(expr) % 2 == 1 and operand(expr[1]):
        return all(supported_filter(expr[i], depth + 1) for i in range(3, len(expr), 2)) and supported_filter(expr[-1], depth + 1)
    return False


def evaluate_filter(expr: Any, feature: GeoJSONFeature) -> bool:
    """Evaluate a prevalidated expression; no evaluation of user source code."""
    props = feature.get("properties") or {}
    geometry = feature.get("geometry")
    geometry_type = (geometry["type"] if geometry is not None else "").removeprefix("Multi")

    def value(node: Any) -> Any:
        if not isinstance(node, list):
            return node
        op = node[0]
        if op == "get":
            return props.get(node[1])
        if op == "id":
            return feature.get("id")
        if op == "geometry-type":
            return geometry_type
        if op == "literal":
            return node[1]
        inner = value(node[1])
        if inner is None:
            return ""
        if isinstance(inner, bool):
            return "true" if inner else "false"
        return str(inner)

    def equal(left: Any, right: Any) -> bool:
        if isinstance(left, bool) != isinstance(right, bool):
            return False
        return left == right

    if isinstance(expr, bool):
        return expr
    op = expr[0]
    if op == "all":
        return all(evaluate_filter(v, feature) for v in expr[1:])
    if op == "any":
        return any(evaluate_filter(v, feature) for v in expr[1:])
    if op == "!":
        return not evaluate_filter(expr[1], feature)
    if op == "has":
        return expr[1] in props
    left = value(expr[1])
    if op == "match":
        for i in range(2, len(expr) - 1, 2):
            labels = expr[i] if isinstance(expr[i], list) else [expr[i]]
            if any(equal(left, label) for label in labels):
                return evaluate_filter(expr[i + 1], feature)
        return evaluate_filter(expr[-1], feature)
    right = value(expr[2])
    if op == "in":
        return isinstance(right, list) and any(equal(left, item) for item in right)
    if expr[1] == "$type":
        left = geometry_type
    elif expr[1] == "$id":
        left = feature.get("id")
    if op in ("==", "!="):
        if left is None:
            return op == "!="
        result = equal(left, right)
        return result if op == "==" else not result
    if type(left) not in (int, float) or type(right) not in (int, float):
        return False
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    return left <= right
