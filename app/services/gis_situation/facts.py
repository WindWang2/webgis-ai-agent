"""SitFact —— GIS Situation 事实元语（方向 2 S1 契约）。

每个事实尽量具备：value / source（权威源标识）/ status（known·unknown·
stale·unavailable）/ observed_at / revision / ref（指向大 payload，绝不
内联 payload）/ confidence（仅来源提供校准置信时）。

unknown 语义（DC-4）：unknown ≠ 缺省键。编译器把"该维度无证据"编译成
``status="unknown"`` 的显式事实 —— 下游（模型/查询 API）看到的是
"不知道"，不是"默认 0/false"。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

#: 封闭状态词表。stale = 有证据但 revision/时间已落后；unavailable = 权威源
#: 本次编译读取失败（partial source unavailable 降级面）。
STATUS_KNOWN = "known"
STATUS_UNKNOWN = "unknown"
STATUS_STALE = "stale"
STATUS_UNAVAILABLE = "unavailable"
FACT_STATUSES = (STATUS_KNOWN, STATUS_UNKNOWN, STATUS_STALE, STATUS_UNAVAILABLE)


class SitFact(BaseModel):
    """一条有界事实。``model_dump()`` 只保留非 None 字段（紧凑序列化）。"""

    model_config = ConfigDict(extra="forbid")

    value: Optional[Any] = None
    status: str = STATUS_UNKNOWN
    source: str = ""
    observed_at: str = ""
    revision: Optional[int] = None
    ref: Optional[str] = None
    confidence: Optional[float] = None

    def to_dict(self) -> dict:
        """紧凑序列化：known 才携带 value；空 observed_at 不落盘。"""
        out = self.model_dump(exclude_none=True)
        if not self.observed_at:
            out.pop("observed_at", None)
        if self.status != STATUS_KNOWN and "value" in out:
            # 未知/过期/失败事实不允许伪装默认值外泄（value 应为 None，
            # 这里是防御：序列化面再次保证 unknown 无值）。
            out.pop("value", None)
        return out


def known(
    value: Any,
    *,
    source: str,
    observed_at: str = "",
    revision: Optional[int] = None,
    ref: Optional[str] = None,
    confidence: Optional[float] = None,
) -> SitFact:
    return SitFact(
        value=value, status=STATUS_KNOWN, source=source,
        observed_at=observed_at, revision=revision, ref=ref,
        confidence=confidence,
    )


def unknown(*, source: str) -> SitFact:
    """显式未知：该维度在当前权威源中无证据（不猜 0/false/空串）。"""
    return SitFact(status=STATUS_UNKNOWN, source=source)


def unavailable(*, source: str) -> SitFact:
    """权威源读取失败（partial source unavailable 的降级面）。"""
    return SitFact(status=STATUS_UNAVAILABLE, source=source)


def stale(value: Any, *, source: str, observed_at: str = "",
          revision: Optional[int] = None) -> SitFact:
    """有证据但已落后（调用方给出 stale 判据，本模块不定义时效策略）。"""
    return SitFact(
        value=value, status=STATUS_STALE, source=source,
        observed_at=observed_at, revision=revision,
    )


def is_usable(fact: Optional[SitFact]) -> bool:
    """查询 API 的"可用"判定：只有 known 参与决策（stale 需显式豁免）。"""
    return fact is not None and fact.status == STATUS_KNOWN
