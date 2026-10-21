"""节点 payload 的行/要素通道投影（#1718）。

三种形状统一投影为 rows 列表：

- ``features``/``rows`` 键为**列表** → 本体；
- ``features`` 键为 rows 信封 dict（``{"rows": [...]}`` —— durable 回传面
  历史上的盲包形状；旧 ref / 既有缓存条目仍可能带此形状）→ 信封内的 rows
  列表；
- 其余（缺失 / 非列表 / 信封内非列表）→ None。

记账与计数面（executor 的 ``_count_rows``/``_measure``/``_governor_charge``）
经本函数投影：信封形状下少报为 1（len(dict)）、对 dict 切片抛异常
（``d[:64]`` → KeyError/TypeError）两类缺陷同源收敛于此。
"""
from __future__ import annotations

from typing import Any, Optional


def row_items(payload: dict[str, Any]) -> Optional[list]:
    """payload 的行/要素通道投影（契约见模块 docstring）。"""
    feats = payload.get("features")
    if isinstance(feats, list):
        return feats
    if isinstance(feats, dict):
        rows = feats.get("rows")
        if isinstance(rows, list):
            return rows
    rows = payload.get("rows")
    if isinstance(rows, list):
        return rows
    return None
