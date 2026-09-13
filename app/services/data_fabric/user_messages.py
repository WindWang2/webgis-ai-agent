"""ads-v1 bilingual user-facing messages (DS9, ADR-0179).

The degradation / availability vocabulary a user actually sees. Templates
are the single wording source — code never inlines these sentences. Both
locales ship together; ``t()`` picks by lang with an honest fallback to zh
(never an empty string, never a raised error for a missing key).
"""
from __future__ import annotations

from typing import Dict

_MESSAGES: Dict[str, Dict[str, str]] = {
    # 降级换源提示（DS4 D3 决策的用户面）
    "data_from_fallback_source": {
        "zh": "数据来自 {source}（备用源）",
        "en": "Data served from {source} (fallback source)",
    },
    # 不可比标注（comparable=False 的硬约束用户面）
    "result_not_comparable": {
        "zh": "结果不可比：备用源与原源的粒度/覆盖不一致，跨源对比结论慎用",
        "en": "Results are not comparable: the fallback source differs in granularity/coverage — interpret cross-source comparisons with care",
    },
    # 版本语义
    "version_latest_notice": {
        "zh": "当前为最新版本（latest，未锁定）",
        "en": "Latest version (latest, not pinned)",
    },
    "version_pinned_notice": {
        "zh": "已锁定版本 {version}",
        "en": "Version pinned to {version}",
    },
    # 本地资产不可用（DS7：显式 unavailable + 灌数指引）
    "local_asset_unavailable": {
        "zh": "本地数据 {library} 未灌数：{hint}",
        "en": "Local dataset {library} not ingested: {hint}",
    },
    # 漂移告警（DS5 分级）
    "drift_detected": {
        "zh": "检测到数据结构变化（{change_class}，{severity}）",
        "en": "Schema drift detected ({change_class}, {severity})",
    },
    # 澄清（DS2 低置信度）
    "clarification_needed": {
        "zh": "候选数据集置信度较低（{confidence}），请确认目标数据集/范围/时间",
        "en": "Low confidence ({confidence}) on the top dataset candidate — please confirm dataset/scope/time",
    },
    # 成本告警（DS8）
    "cost_budget_exceeded": {
        "zh": "取数代价超预算：{metric} = {value}（上限 {budget}）",
        "en": "Acquisition cost over budget: {metric} = {value} (cap {budget})",
    },
}


def t(key: str, lang: str = "zh", **kwargs) -> str:
    """Translate a message key; falls back to zh, then to the key itself."""
    entry = _MESSAGES.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get("zh") or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def available_keys() -> list:
    return sorted(_MESSAGES)


__all__ = ["t", "available_keys"]
