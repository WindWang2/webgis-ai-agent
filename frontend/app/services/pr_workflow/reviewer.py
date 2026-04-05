"""
PR 审核人分配模块
根据 PR 修改的模块自动匹配对应负责人，或按轮询分配
"""
import logging
import random
from datetime import datetime
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# 上次分配的审核人索引（用于轮询）
_last_reviewer_index = 0


def parse_pr_files(pr_number: int) -> list[str]:
    """
    通过 GitHub API 获取 PR 变更的文件列表
    
    Args:
        pr_number: PR 编号
        
    Returns:
        文件路径列表
    """
    if not settings.GITHUB_TOKEN or not settings.GITHUB_REPO_OWNER or not settings.GITHUB_REPO_NAME:
        logger.warning("GitHub 配置不完整，无法获取 PR 文件")
        return []
    
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO_OWNER}/{settings.GITHUB_REPO_NAME}/pulls/{pr_number}/files"
    
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                url,
                headers={
                    "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "WebGIS-AI-Agent"
                }
            )
            if resp.status_code == 200:
                files = resp.json()
                return [f.get("filename", "") for f in files]
            else:
                logger.error(f"获取 PR 文件失败: {resp.status_code}")
                return []
    except Exception as e:
        logger.exception(f"获取 PR 文件异常: {e}")
        return []


# 模块负责人映射（可根据实际情况配置）
MODULE_REVIEWERS = {
    "frontend": ["frontend-reviewer"],
    "backend": ["backend-reviewer"],
    "api": ["api-reviewer"],
    "auth": ["security-reviewer"],
    "gis": ["gis-reviewer"],
}


def detect_module(files: list[str]) -> str:
    """
    根据变更文件检测涉及的模块
    
    Args:
        files: 文件路径列表
        
    Returns:
        模块名称（如 frontend/backend/api/gis/multiple）
    """
    modules = set()
    
    for filepath in files:
        lower_path = filepath.lower()
        
        if "frontend" in lower_path or "web/" in lower_path or "react" in lower_path or "vue" in lower_path:
            modules.add("frontend")
        elif "api" in lower_path or "route" in lower_path:
            modules.add("api")
        elif "auth" in lower_path or "login" in lower_path or "jwt" in lower_path:
            modules.add("auth")
        elif "gis" in lower_path or "map" in lower_path or "spatial" in lower_path:
            modules.add("gis")
        elif "backend" in lower_path or "service" in lower_path or "model" in lower_path:
            modules.add("backend")
    
    if len(modules) > 1:
        return "multiple"
    elif len(modules) == 1:
        return list(modules)[0]
    return "general"


def assign_reviewer_by_round_robin() -> Optional[str]:
    """
    轮询分配审核人
    
    Returns:
        分配的审核人 GitHub username
    """
    global _last_reviewer_index
    
    reviewers = settings.PR_REVIEWERS
    if not reviewers:
        logger.warning("未配置 PR_REVIEWERS")
        return None
    
    reviewer = reviewers[_last_reviewer_index % len(reviewers)]
    _last_reviewer_index += 1
    
    logger.info(f"轮询分配审核人: {reviewer}")
    return reviewer


def assign_reviewer_by_module(module: str) -> Optional[str]:
    """
    根据模块分配审核人
    
    Args:
        module: 检测到的模块名称
        
    Returns:
        分配的审核人 GitHub username
    """
    module_reviewers = MODULE_REVIEWERS.get(module, [])
    
    # 如果找不到特定模块的审核人，使用通用列表
    if not module_reviewers:
        module_reviewers = settings.PR_REVIEWERS
    
    if not module_reviewers:
        return None
    
    # 随机选择一个
    reviewer = random.choice(module_reviewers)
    logger.info(f"模块 {module} 分配审核人: {reviewer}")
    return reviewer


def assign_reviewer(pr_number: int, force_module: Optional[str] = None) -> dict:
    """
    分配 PR 审核人
    
    Args:
        pr_number: PR 编号
        force_module: 强制指定的模块（可选）
        
    Returns:
        分配结果 {'success': bool, 'reviewer': str, 'method': str}
    """
    if not settings.PR_AUTO_ASSIGN_REVIEWER:
        return {"success": False, "reason": "auto_assign_disabled"}
    
    # 如果有配置的从模块匹配，使用模块匹配
    if settings.PR_REVIEWERS and force_module:
        reviewer = assign_reviewer_by_module(force_module)
    else:
        # 否则使用轮询
        reviewer = assign_reviewer_by_round_robin()
    
    if not reviewer:
        return {"success": False, "reason": "no_reviewer_available"}
    
    # 通过 GitHub API 分配审核人
    if settings.GITHUB_TOKEN and settings.GITHUB_REPO_OWNER and settings.GITHUB_REPO_NAME:
        _assign_github_reviewer(pr_number, reviewer)
    
    return {
        "success": True,
        "reviewer": reviewer,
        "method": "module" if force_module else "round_robin"
    }


def _assign_github_reviewer(pr_number: int, reviewer: str) -> bool:
    """
    通过 GitHub API 分配审核人
    
    Args:
        pr_number: PR 编号
        reviewer: 审核人 GitHub username
        
    Returns:
        是否成功
    """
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO_OWNER}/{settings.GITHUB_REPO_NAME}/pulls/{pr_number}/requested_reviewers"
    
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                url,
                json={"reviewers": [{"username": reviewer}]},
                headers={
                    "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "WebGIS-AI-Agent"
                }
            )
            if resp.status_code in (200, 201):
                logger.info(f"成功分配审核人 {reviewer} 到 PR #{pr_number}")
                return True
            else:
                logger.error(f"分配审核人失败: {resp.status_code} - {resp.text}")
                return False
    except Exception as e:
        logger.exception(f"分配审核人异常: {e}")
        return False


__all__ = [
    "assign_reviewer",
    "parse_pr_files",
    "detect_module"
]