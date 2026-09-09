"""Numerical Oracle Corpus —— 回放运行器（Foundation V3 · Goal K）。

设计（docs/dev/spatial-algorithm-v3/decisions.md D9）：
- ``scripts/gen_science_oracles.py`` 在开发环境**一次性**生成
  ``tests/science_oracles/data/<domain>.json``：每个 case 是
  {id, target: "module:function", args, kwargs, expect}；
  expect 由当时的参考实现（scipy/sklearn/esda/手解析/被测实现自身的
  黄金路径）计算并**硬编码**进 JSON。
- 本运行器只做**回放**：import target → 运行 → 与 JSON 里的期望值
  比较。零期望值重计算，保证确定性、速度与回归锚定语义。
- 比较语义三种：
  - ``exact``: 逐位相等（round-trip 后 ==，用于整数/布尔/字符串/键集）；
  - ``allclose``: 数值容差（默认 rtol=1e-8, atol=1e-8；JSON 里可覆写）；
  - ``error``: 期望抛出的科学错误类型（scientific_code 或异常类名）。
"""
from __future__ import annotations

import importlib
import json
import math

import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

DATA_DIR = Path(__file__).resolve().parent / "data"

_DEFAULT_RTOL = 1e-8
_DEFAULT_ATOL = 1e-8


@dataclass(frozen=True)
class OracleCase:
    case_id: str
    target: str
    args: List[Any]
    kwargs: Dict[str, Any]
    expect: Dict[str, Any]
    domain: str

    @property
    def kind(self) -> str:
        return str(self.expect.get("kind", "allclose"))


def load_domain(domain: str) -> List[OracleCase]:
    path = DATA_DIR / f"{domain}.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    cases = []
    for entry in raw["cases"]:
        cases.append(OracleCase(
            case_id=entry["id"],
            target=entry["target"],
            args=entry.get("args", []),
            kwargs=entry.get("kwargs", {}),
            expect=entry["expect"],
            domain=domain,
        ))
    return cases


def all_domains() -> List[str]:
    return sorted(p.stem for p in DATA_DIR.glob("*.json"))


def resolve_target(target: str) -> Callable:
    module_name, _, func_name = target.partition(":")
    module = importlib.import_module(module_name)
    func: Any = module
    for part in func_name.split("."):
        func = getattr(func, part)
    return func


def _as_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _select(actual: Any, path: str) -> Any:
    """从（通常为 dict/list 的）结果中按迷你语言提取标量。

    - ``array.0.3``：点路径（dict 键 / list 下标交替）；
    - ``mean:array`` / ``max:K.2`` / ``min:g`` / ``sum:counts``：
      聚合前缀（mean 跳过 NaN；全 NaN → None）。
    """
    agg: Optional[str] = None
    if ":" in path:
        agg, _, path = path.partition(":")
        if agg not in ("mean", "max", "min", "sum"):
            raise ValueError(f"unknown aggregator {agg!r} in select {path!r}")
    cur = actual
    for part in path.split("."):
        if isinstance(cur, np.ndarray):
            cur = cur[int(part)]
        elif isinstance(cur, (list, tuple)):
            # science-v4：新 API（nscore/SK 等）返回 tuple —— 与 list 同下标语义
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(f"cannot descend into {type(cur).__name__} at {part!r}")
    if agg is None:
        return cur

    def _flatten(v: Any):
        # numpy 数组是运行时输出的常态 —— 不能只认 list
        if isinstance(v, np.ndarray):
            yield from _flatten(v.tolist())
        elif isinstance(v, (list, tuple)):
            for item in v:
                yield from _flatten(item)
        else:
            yield v

    vals = [float(x) for x in _flatten(cur)
            if isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(float(x))]
    if agg == "sum":
        return float(sum(vals)) if vals else None
    if not vals:
        return None
    if agg == "mean":
        return float(sum(vals) / len(vals))
    return float(max(vals) if agg == "max" else min(vals))


def compare(actual: Any, expect: Dict[str, Any]) -> Tuple[bool, str]:
    """比较实际输出与期望；返回 (ok, 失败原因描述)。"""
    select = expect.get("select")
    if select:
        actual = _select(actual, select)
    kind = expect.get("kind", "allclose")
    value = expect["value"]
    if kind == "exact":
        return actual == value, f"expected {value!r}, got {actual!r}"
    if kind == "allclose":
        rtol = float(expect.get("rtol", _DEFAULT_RTOL))
        atol = float(expect.get("atol", _DEFAULT_ATOL))
        if actual is None and value is None:
            return True, ""
        a = _as_float(actual)
        e = _as_float(value)
        if a is None or e is None:
            return actual == value, f"non-numeric compare: expected {value!r}, got {actual!r}"
        if math.isnan(a) and math.isnan(e):
            return True, ""
        if not math.isclose(a, e, rel_tol=rtol, abs_tol=atol):
            return False, f"expected {e!r} (rtol={rtol}, atol={atol}), got {a!r}"
        return True, ""
    if kind == "error":
        return False, "error-kind cases are handled by the runner (never reach compare)"
    return False, f"unknown expect kind {kind!r}"


def run_case(case: OracleCase) -> Tuple[bool, str]:
    """执行单个 oracle case（含 error-kind 语义）。"""
    func = resolve_target(case.target)
    if case.kind == "error":
        try:
            func(*case.args, **case.kwargs)
        except Exception as exc:  # noqa: BLE001 — 期望路径就是抛错
            code = getattr(exc, "scientific_code", None) or type(exc).__name__
            want = case.expect.get("code")
            if want is not None and code != want:
                return False, f"expected error {want!r}, got {code!r} ({exc})"
            return True, ""
        return False, f"expected error {case.expect.get('code')!r}, but call succeeded"
    try:
        actual = func(*case.args, **case.kwargs)
    except Exception as exc:  # noqa: BLE001
        return False, f"unexpected error: {type(exc).__name__}: {exc}"
    return compare(actual, case.expect)

    return x
