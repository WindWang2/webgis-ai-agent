"""Analysis Quality Evidence —— 分析产物的轻量质量证据（V2 P9，§26）。

每个证据 dict 是**有界、可序列化、可复算**的：input/output/dropped 计数
+ working_crs + 算法近似性声明。用途：debugging、artifact lineage、
复现、resolver evidence —— 不生成长报告，不进 LLM 大上下文（经
to_llm_response 的有界透传）。

确定性（§27）：``approximate`` 直接取算法声明，不猜测；同输入同参数的
deterministic 分析，evidence 中除 working_crs 外的计数字段必须稳定。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# 证据键上界：证据是 metadata，不是报告。
_MAX_EVIDENCE_KEYS = 16
_MAX_EXTRA_VALUE_CHARS = 64


def build_quality_evidence(
    *,
    input_count: Optional[int] = None,
    output_count: Optional[int] = None,
    working_crs: str = "",
    dropped_invalid: int = 0,
    empty_count: int = 0,
    approximate: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造有界质量证据 dict（全部值可 JSON 序列化）。

    - 计数一律 int（None 缺席 —— 未知不虚构为 0）；
    - working_crs 是投影后实际参与米制计算的 CRS 字符串（如 EPSG:32648）；
    - extra 只收标量/短串（截断），超界键丢弃。
    """
    ev: Dict[str, Any] = {}
    if input_count is not None:
        ev["input_count"] = int(input_count)
    if output_count is not None:
        ev["output_count"] = int(output_count)
    if dropped_invalid:
        ev["dropped_invalid"] = int(dropped_invalid)
    if empty_count:
        ev["empty_count"] = int(empty_count)
    if working_crs:
        ev["working_crs"] = str(working_crs)[:64]
    if approximate:
        ev["approximate"] = True
    for k, v in (extra or {}).items():
        if len(ev) >= _MAX_EVIDENCE_KEYS:
            break
        if isinstance(v, bool) or isinstance(v, int):
            ev[str(k)[:32]] = v
        elif isinstance(v, float):
            ev[str(k)[:32]] = v
        elif isinstance(v, str):
            ev[str(k)[:32]] = v[:_MAX_EXTRA_VALUE_CHARS]
    return ev
