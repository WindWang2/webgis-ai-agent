"""Large Data Policy —— 小/中/大数据分级策略（§三十二）。

Agent 永远主要看到 ref + profile + summary，不是数据本体。本模块把
「多大算大」与「各档允许什么」显式成契约（纯函数、零 I/O）：

- small  （< SOFT，默认 1 MiB）：可直接读取/采样进会话处理；
- medium （< HARD，默认 50 MiB，与会话 store 单会话字节预算同源）：
  profile + chunk/window，禁止整体进上下文；
- large  （≥ HARD，或多 GB raster refs）：ref + metadata，读取走
  Data Fabric / 流式通道。

判档证据缺失（size 未知）→ UNKNOWN：按 medium 的保守纪律处理
（不因「不知道多大」就放行全量读取）。
"""
from __future__ import annotations

import enum
from typing import Optional

# 分级阈值（字节）。SOFT 对齐 LLM 工具结果的轻量口径；HARD 对齐
# 会话 store 单会话预算（SESSION_STORE_MAX_BYTES 默认 50 MB）。
SMALL_LIMIT_BYTES = 1 * 1024 * 1024
LARGE_LIMIT_BYTES = 50 * 1024 * 1024


class DataSizeClass(str, enum.Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    UNKNOWN = "unknown"


class AccessPolicy(str, enum.Enum):
    """各档允许的访问形态（§三十二）。"""

    DIRECT_READ = "direct_read"          # 可整体读取/采样
    PROFILE_CHUNKED = "profile_chunked"  # profile + chunk/window
    REF_METADATA_ONLY = "ref_metadata"   # ref + metadata（读取走流式/fabric）


_POLICY: "dict" = {
    DataSizeClass.SMALL: AccessPolicy.DIRECT_READ,
    DataSizeClass.MEDIUM: AccessPolicy.PROFILE_CHUNKED,
    DataSizeClass.LARGE: AccessPolicy.REF_METADATA_ONLY,
    DataSizeClass.UNKNOWN: AccessPolicy.PROFILE_CHUNKED,  # 未知按保守
}


def classify_size(size_bytes: Optional[int]) -> DataSizeClass:
    if size_bytes is None or size_bytes < 0:
        return DataSizeClass.UNKNOWN
    if size_bytes < SMALL_LIMIT_BYTES:
        return DataSizeClass.SMALL
    if size_bytes < LARGE_LIMIT_BYTES:
        return DataSizeClass.MEDIUM
    return DataSizeClass.LARGE


def classify_features(feature_count: Optional[int], bytes_per_feature: int = 100) -> DataSizeClass:
    """按要素数估档（与 RefDescriptor.estimate_bytes 同一 100B/要素启发）。"""
    if feature_count is None or feature_count < 0:
        return DataSizeClass.UNKNOWN
    return classify_size(int(feature_count) * bytes_per_feature)


def access_policy(size: DataSizeClass) -> AccessPolicy:
    return _POLICY[size]


def llm_view_allowed(size: DataSizeClass) -> bool:
    """该档是否允许数据本体进入 LLM 上下文：只有 small 允许（且仍受
    llm_result_formatter 的 2500 字符硬闸约束）。"""
    return size is DataSizeClass.SMALL
