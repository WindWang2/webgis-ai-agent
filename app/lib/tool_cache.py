"""工具结果缓存层 — Redis-backed, opt-in per tool.

入口：make_cache_key(name, args)、cached_tool(...) 装饰器（后续 Task 加入）。
键命名空间 tool_cache:v1:<sha256[:16]>，全量失效一条 SCAN | DEL 即可。
"""
import hashlib
import json
from typing import Optional


def make_cache_key(tool_name: str, args: dict) -> Optional[str]:
    """构造确定性缓存键。

    args 内任一叶子值是 'ref:' 开头的字符串时返回 None — 调用方据此跳过缓存。
    （ref:xxx 是会话内可变数据引用，同一引用不同时刻解析结果不同。）
    """
    if _contains_ref(args):
        return None
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(f"{tool_name}::{canonical}".encode()).hexdigest()[:16]
    return f"tool_cache:v1:{digest}"


def _contains_ref(value) -> bool:
    """递归检查任一叶子是否是 'ref:' 开头的字符串。"""
    if isinstance(value, str):
        return value.startswith("ref:")
    if isinstance(value, dict):
        return any(_contains_ref(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_ref(v) for v in value)
    return False
