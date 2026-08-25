"""numpy 标量友好的 json.dumps default。

工具结果从 GeoDataFrame 行属性等路径漏出 int64/float64 时，序列化边界
（ref 库 store、dispatch 给 LLM 的载荷 dumps）抛 ``TypeError`` 会把整个
工具调用误报为「工具执行异常」（2026-08-25 会话：h3_lisa）。源头转换在
各工具内做（geo_analysis._feature_props）；本 default 只兜漏网标量，
不吞真正不可序列化的类型（仍 raise TypeError）。
"""
from typing import Any

import numpy as np


def numpy_json_default(obj: Any):
    if isinstance(obj, np.generic):
        return obj.item()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
