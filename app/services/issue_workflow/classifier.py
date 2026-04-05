"""
Issue 分类器模块
根据 Issue 标题和内容自动识别类型
"""
import logging
import re
from typing import Final
logger = logging.getLogger(__name__)
# Issue 类别常量
ISSUE_CATEGORIES: Final[list[str]] = [
    "bug",
    "feature",
    "enhancement",
    "documentation",
    "question",
    "refactor",
]
# 分类关键字模式（用于规则匹配）
CLASSIFIER_PATTERNS = {
    "bug": [
        r"\bbug\b", r"\berror\b", r"\bfail(?:ed|s)?\b", r"\bcrash\b", r"\bbroken\b",
        r"\bincorrect\b", r"\bwrong\b", r"\b异常\b", r"\b错误\b", r"\b崩溃\b",
        r"\b闪退\b", r"\b卡死\b", r"\b无响应\b", r"\b不工作\b",
    ],
    "feature": [
        r"\bfeat(?:ure)?\b", r"\brequest\b", r"\badd\s+(?:support|ability)\b",
        r"\bnew\s+(?:feature|function)\b", r"\bimplement\b", r"\b功能\b",
        r"\b需求\b", r"\b增加\s*(?:功能|支持)\b",
    ],
    "enhancement": [
        r"\benhance(?:ment)?\b", r"\bimprove(?:ment)?\b", r"\boptimiz(?:e|ation)\b",
        r"\brefactor(?:ing)?\b", r"\bupgrade\b", r"\boptimiz(?:e|ation)\b",
        r"\b优化\b", r"\b改进\b", r"\b提升\b", r"\b增强\b",
    ],
    "documentation": [
        r"\bdoc(?:ument(?:ation)?)?\b", r"\breadme\b", r"\bwiki\b",
        r"\bcomment\b", r"\btutorial\b", r"\bguide\b", r"\b文档\b",
        r"\b说明\b", r"\b手册\b", r"\b教程\b",
    ],
    "question": [
        r"\bquestion\b", r"\bhow\s+to\b", r"\bhow\s+can\b", r"\bwonder\b",
        r"\bhelp\b", r"\bask\b", r"\bwhy\b", r"\bwhat(?:\s+is|\s+are)\b",
        r"\b请问\b", r"\b怎么\b", r"\b如何\b", r"\b为什么\b", r"\b咨询\b",
    ],
    "refactor": [
        r"\brefactor\b", r"\brestructure\b", r"\brewrite\b",
        r"\bcleanup\b", r"\bclean\s*up\b", r"\bredesign\b",
        r"\b重构\b", r"\b整理\b", r"\b代码.*优化\b",
    ],
}


def classify_issue(title: str, body: str) -> str:
    """
    根据 Issue 标题和内容自动分类
    
    Args:
        title: Issue 标题
        body: Issue 正文内容
        
    Returns:
        分类名称（如 bug/feature/enhancement/documentation/question/refactor）
    """
    combined_text = f"{title} {body or ''}".lower()
    
    scores = {}
    for category, patterns in CLASSIFIER_PATTERNS.items():
        score = 0
        for pattern in patterns:
            matches = re.findall(pattern, combined_text)
            score += len(matches)
        
        if score > 0:
            scores[category] = score
    
    if not scores:
        return ""
    
    # 返回得分最高的分类
    best_category = max(scores.keys(), key=lambda k: scores[k])
    logger.debug(f"分类得分: {scores}, 选择: {best_category}")
    
    return best_category


__all__ = [
    "classify_issue",
    "ISSUE_CATEGORIES",
    "CLASSIFIER_PATTERNS",
]