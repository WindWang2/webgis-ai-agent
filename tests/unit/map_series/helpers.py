"""map_series 单测共享 fixtures —— 合成成员计划 payload。"""
from __future__ import annotations

from typing import Any, Dict


def series_payload(name: str = "成都系列图计划", **kw) -> Dict[str, Any]:
    """最小合法 map_series.v1 payload（成员引用合成 session id）。"""
    base: Dict[str, Any] = {
        "series_version": "map_series.v1",
        "name": name,
        "design_spec_id": "spec-pin-0001",
        "members": [
            {
                "member_id": "m1",
                "title": "主图",
                "session_id": "sess-series-m1",
                "role": "primary",
            },
            {
                "member_id": "m2",
                "title": "对比图",
                "session_id": "sess-series-m2",
                "role": "comparison",
            },
        ],
        "comparability": {"shared_classification": True, "shared_palette": True},
        "dimension": {"axis": "indicator", "labels": ["人口", "GDP"]},
        "output": {"targets": ["png", "pdf"], "dpi": 300},
    }
    base.update(kw)
    return base


__all__ = ["series_payload"]
