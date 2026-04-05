"""
Issue 负责人分配模块
根据 Issue 分类自动匹配对应负责人，或按轮询分配
"""
import logging
import random
from typing import Dict, List, Final
logger = logging.getLogger(__name__)

# Issue 角色映射到具体人员的配置（在 config 中可覆盖）
DEFAULT_ROLE_MAPPING: Final[Dict[str, List[str]]] = {
    "coder": ["developer1", "developer2"],
    "researcher": ["researcher1", "researcher2"],
    "academic": ["academic1"],
}

# Issue 角色对应的职责描述
ROLE_DESCRIPTIONS: Final[Dict[str, str]] = {
    "coder": "负责代码实现、Bug 修复、功能开发",
    "researcher": "负责技术调研、可行性分析、新技术预研",
    "academic": "负责学术研究、论文分析、理论验证",
}


# Issue 分类到角色的默认映射
CATEGORY_TO_ROLE: Final[Dict[str, str]] = {
    "bug": "coder",
    "feature": "coder",
    "enhancement": "coder",
    "refactor": "coder",
    "documentation": "coder",
    "question": "researcher",
}


# 上次分配的审核人索引（用于轮询）
_last_assignee_index = 0


def get_role_mapping() -> Dict[str, List[str]]:
    """
    获取当前的角色到人员映射
    
    Returns:
        角色到人员列表的字典
    """
    from app.core.config import settings
    
    custom_mapping = getattr(settings, "ISSUE_ROLE_MAPPING", None)
    if custom_mapping:
        return custom_mapping
    
    return DEFAULT_ROLE_MAPPING.copy()


def get_all_roles_names() -> List[str]:
    """
    获取所有可用角色名称
    
    Returns:
        角色名称列表
    """
    mapping = get_role_mapping()
    return list(mapping.keys())


def get_role_by_category(category: str) -> str:
    """
    根据分类获取对应的角色
    
    Args:
        category: Issue 分类（bug/feature/enhancement/documentation/question/refactor）
        
    Returns:
        角色名称（如 coder/researcher/academic）
    """
    # 直接映射
    if category in CATEGORY_TO_ROLE:
        return CATEGORY_TO_ROLE[category]
    
    # 默认分配给 coder
    return "coder"


def assign_reviewer_by_round_robin() -> str:
    """
    按轮询方式分配负责人（只看角色）
    
    Returns:
        被分配的 GitHub username
    """
    global _last_assignee_index
    
    roles = get_all_role_names()
    if not roles:
        logger.warning("未配置 ISSUE_ASSIGNEES，使用默认角色")
        roles = list(DEFAULT_ROLE_MAPPING.keys())
    
    role_name = roles[_last_assignee_index % len(roles)]
    _last_assignee_index += 1
    
    logger.info(f"轮询分配角色: {role_name}")
    return role_name


def assign_reviewer_by_category(category: str) -> str:
    """
    根据 Issue 分类分配负责人
    
    Args:
        category: Issue 分类
        
    Returns:
        被分配的 GitHub username
    """
    role = get_role_by_category(category)
    
    role_members = get_role_mapping().get(role, [])
    
    if not role_members:
        logger.warning(f"角色 {role} 没有配置成员，改用轮询")
        return assign_reviewer_by_round_robin()
    
    # 随机选择一个
    assignee = random.choice(role_members)
    logger.info(f"根据分类 {category} 分配给角色 {role} 的成员: {assignee}")
    
    return assignee


def assign_reviewer(
    category: str,
    auto_assign: bool = True,
) -> Dict[str, any]:
    """
    分配 Issue 负责人的主入口函数
    
    Args:
        category: Issue 分类
        auto_assign: 是否自动分配
        
    Returns:
        分配结果字典：
        {
            "success": bool,
            "role": str,           # 分配的角色
            "assignee": str,       # 具体人员 GitHub username
            "method": str         # 分配方式 "category"/"round_robin"
        }
    """
    from app.core.config import settings
    
    if not auto_assign or not settings.ISSUE_AUTO_ASSIGN:
        return {"success": False, "reason": "auto_assign_disabled"}
    
    # 判断使用哪种分配方式
    use_category_based = getattr(settings, "ISSUE_USE_CATEGORY_ASSIGN", True)
    
    if use_category_based:
        role = get_role_by_category(category)
        method = "category"
    else:
        role = assign_reviewer_by_round_robin()
        method = "round_robin"
    
    # 获取角色对应的成员列表
    role_members = get_role_mapping().get(role, [])
    
    if not role_members:
        logger.warning(f"角色 {role} 无成员，改用 coder")
        role_members = get_role_mapping().get("coder", [""])
        role = "coder"
    
    # 选择具体的 assignee
    assignee = role_members[0] if role_members else ""
    
    return {
        "success": bool(assignee),
        "role": role,
        "assignee": assignee,
        "method": method,
    }


# 为了兼容，也提供别名
assign_reviewr_by_round_robin = assign_reviewer_by_round_robin


__all__ = [
    "assign_reviewer",
    "assign_reviewer_by_round_robin",
    "assign_reviewer_by_category",
    "get_role_by_category",
    "get_role_mapping",
    "get_all_role_names",
    "ROLE_DESCRIPTIONS",
    "DEFAULT_ROLE_MAPPING",
    "CATEGORY_TO_ROLE",
]