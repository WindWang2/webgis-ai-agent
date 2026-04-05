"""
Issue 优先级判定模块
根据 Issue 内容判断严重程度
"""
import logging
import re
from typing import Final
logger = logging.getLogger(__name__)

# Issue 优先级常量
ISSUE_PRIORITIES: Final[list[str]] = [
    "critical",
    "high",
    "medium",
    "low",
]

# 紧急关键字（Critical）
CRITICAL_KEYWORDS = [
    "critical", "urgent", "emergency", "asap", "immediately",
    "security", "vulnerability", "exploit", "hack", "breach",
    "data loss", "corruption", "deadlock", "outage", "down",
    "致命", "紧急", "立即", "漏洞", "安全", "宕机", "崩溃",
    "丢失", "数据损坏", "服务不可用",
]

# 高优先级关键字（High）
HIGH_KEYWORDS = [
    "important", "high priority", "serious", "severe",
    "blocker", "blocking", "can't proceed", "workaround",
    "major", "significant", "影响很大", "阻塞", "阻碍", "重要",
    "严重", "无法进行", "无工作区",
]

# 低优先级关键字（Low）
LOW_KEYWORDS = [
    "minor", "trivial", "small", "tiny", "cosmetic",
    "nice to have", "whenever", "future", "later",
    "次要", "微小", "美化", "装饰性", "锦上添花", "未来",
]


def _calculate_keyword_score(text: str, keywords: list[str]) -> int:
    """
    计算关键词匹配得分
    
    Args:
        text: 待检查文本（小写）
        keywords: 关键字列表
        
    Returns:
        匹配得分
    """
    score = 0
    text_lower = text.lower()
    
    for kw in keywords:
        # 使用单词边界精确匹配
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, text_lower):
            score += 1
    
    return score


def prioritize_issue(title: str, body: str) -> str:
    """
    根据 Issue 标题和内容判定优先级
    
    Args:
        title: Issue 标题
        body: Issue 正文内容
        
    Returns:
        优先级（critical/high/medium/low）
    """
    combined_text = f"{title} {body or ''}"
    
    # 计算各级别得分
    critical_score = _calculate_keyword_score(combined_text, CRITICAL_KEYWORDS)
    high_score = _calculate_keyword_score(combined_text, HIGH_KEYWORDS)
    low_score = _calculate_keyword_score(combined_text, LOW_KEYWORDS)
    
    # 判定优先级（critical > high > medium > low）
    if critical_score > 0:
        logger.debug(f"Issue 优先级判定: Critical (score={critical_score})")
        return "critical"
    
    if high_score > 0:
        logger.debug(f"Issue 优先级判定: High (score={high_score})")
        return "high"
    
    if low_score > 0:
        logger.debug(f"Issue 优先级判定: Low (score={low_score})")
        return "low"
    
    # 默认中等优先级
    logger.debug("Issue 优先级判定: Medium (default)")
    return "medium"


# ============ 扩展：基于模板匹配的高级优先级判定 ============

# 问题严重性指示词（在 body 中出现会加分）
SEVERITY_INDICATORS = {
    # 影响范围
    "全局": 2,
    "所有用户": 3,
    "全部": 2,
    "系统级": 3,
    "核心功能": 2,
    # 影响频率
    "总是": 2,
    "每次": 3,
    "经常": 1,
    "偶尔": 0,
    # 工作影响
    "完全无法": 3,
    "基本无法": 2,
    "严重影响": 2,
    "部分影响": 1,
}


def calculate_severity_score(title: str, body: str) -> int:
    """
    计算问题严重性分数（用于辅助人工判断）
    
    Args:
        title: Issue 标题
        body: Issue 正文
        
    Returns:
        严重性分数（0-10+）
    """
    score = 0
    text = f"{title} {body or ''}"
    
    for indicator, points in SEVERITY_INDICATORS.items():
        if indicator in text:
            score += points
    
    return score


__all__ = [
    "prioritize_issue",
    "calculate_severity_score",
    "ISSUE_PRIORITIES",
    "CRITICAL_KEYWORDS",
    "HIGH_KEYWORDS",
    "LOW_KEYWORDS",
]