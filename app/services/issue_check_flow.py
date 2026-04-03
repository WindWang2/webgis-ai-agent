"""
Issue 检查汇总服务
整合所有 Issue 检查项，执行检查并在 GitHub Issue 上评论
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import settings
from app.services.feishu_notification import (
    send_issue_created,
    send_issue_assigned,
)
from app.services.issue_workflow.classifier import (
    classify_issue,
    ISSUE_CATEGORIES,
)
from app.services.issue_workflow.prioritizer import (
    prioritize_issue,
    ISSUE_PRIORITIES,
)
from app.services.issue_workflow.assignee import (
    assign_reviewer_by_round_robin,
    ISSUE_ROLE_MAPPING,
)

logger = logging.getLogger(__name__)


@dataclass
class IssueCheckResult:
    """Issue 检查结果汇总"""
    issue_number: int
    category: str = ""
    priority: str = ""
    assignee_role: str = ""
    labels_added: list = field(default_factory=list)
    
    def to_dict(self):
        return {
            "issue_number": self.issue_number,
            "category": self.category,
            "priority": self.priority,
            "assignee_role": self.assignee_role,
            "label_added": self.label_added,
        }


def generate_issue_comment(check_result: IssueCheckResult, issue_title: str) -> str:
    """
    生成 Issue 评论内容
    
    Args:
        check_result: 检查结果
        issue_title: Issue 标题
        
    Returns:
        Markdown 格式的评论内容
    """
    category_icon = {
        "bug": "🐛",
        "feature": "✨",
        "enhancement": "🚀",
        "documentation": "📝",
        "question": "❓",
        "refactor": "🔧",
    }.get(check_result.category, "📋")
    
    priority_icon = {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🟢",
    }.get(check_result.priority, "⚪")
    
    role_label = ISSUE_ROLE_MAPPING.get(
        check_result.assignee_role, 
        check_result.assignee_role or "待分配"
    )
    
    lines = [
        f"## 🤖 Issue 自动分类 {category_icon}",
        "",
        f"**分类**: {check_result.category or '未分类'}",
        f"**优先级**: {check_result.priority or '普通'}",
        f"**负责角色**: {role_label}",
        "",
    ]
    
    if check_result.label_added:
        lines.append(f"🏷️ **已添加标签**: {', '.join(check_result.label_added)}")
        lines.append("")
    
    return "\n".join(lines)


def add_issue_labels(
    issue_number: int,
    labels_names: list[str],
) -> bool:
    """
    给 GitHub Issue 添加标签
    
    Args:
        issue_number: Issue 编号
        label_names: 标签名称列表
        
    Returns:
        是否成功
    """
    import httpx
    
    if not settings.GITHUB_TOKEN or not settings.GITHUB_REPO_OWNER or not settings.GITHUB_REPO_NAME:
        logger.warning("GitHub 配置不完整，无法添加标签")
        return False
    
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO_OWNER}/{settings.GITHUB_REPO_NAME}/issues/{issue_number}/labels"
    
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                url,
                json={"labels": label_names},
                headers={
                    "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "WebGIS-AI-Agent"
                }
            )
            
            if resp.status_code in (200, 201):
                logger.info(f"成功为 Issue #{issue_number} 添加标签: {label_names}")
                return True
            else:
                logger.error(f"添加标签失败: {resp.status_code} - {resp.text}")
                return False
    except Exception as e:
        logger.exception(f"添加标签异常: {e}")
        return False


def assign_issue_assignee(
    issue_number: int,
    assignee: str,
) -> bool:
    """
    分配 Issue 受理人
    
    Args:
        issue_number: Issue 编号
        assignee: 受让人 GitHub username
        
    Returns:
        是否成功
    """
    import httpx
    
    if not settings.GITHUB_TOKEN or not settings.GITHUB_REPO_OWNER or not settings.GITHUB_REPO_NAME:
        logger.warning("GitHub 配置不完整，无法分配 Issue")
        return False
    
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO_OWNER}/{settings.GITHUB_REPO_NAME}/issues/{issue_number}"
    
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.patch(
                url,
                json={"assignee": assignee},
                headers={
                    "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "WebGIS-AI-Agent"
                }
            )
            
            if resp.status_code in (200, 201):
                logger.info(f"成功分配 Issue #{issue_number} 给 {assignee}")
                return True
            else:
                logger.error(f"分配 Issue 失败: {resp.status_code} - {resp.text}")
                return False
    except Exception as e:
        logger.exception(f"分配 Issue 异常: {e}")
        return False


def post_issue_comment(
    issue_number: int,
    comment_body: str,
) -> bool:
    """
    在 GitHub Issue 上发布评论
    
    Args:
        issue_number: Issue 编号
        comment_body: 评论内容
        
    Returns:
        是否成功
    """
    import httpx
    
    owner = settings.GITHUB_REPO_OWNER
    name = settings.GITHUB_REPO_NAME
    
    if not settings.GITHUB_TOKEN or not owner or not name:
        logger.warning("GitHub 配置不完整，无法发布评论")
        return False
    
    url = f"https://api.github.com/repos/{owner}/{name}/issues/{issue_number}/comments"
    
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                url,
                json={"body": comment_body},
                headers={
                    "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "WebGIS-AI-Agent"
                }
            )
            
            if resp.status_code in (200, 201):
                logger.info(f"成功发布 Issue #{issue_number} 评论")
                return True
            else:
                logger.error(f"发布评论失败: {resp.status_code}")
                return False
    except Exception as e:
        logger.exception(f"发布评论异常: {e}")
        return False


def trigger_full_issue_check(
    issue_data: dict,
    repo_data: dict,
) -> IssueCheckResult:
    """
    触发完整的 Issue 检查流程：
    1. 自动分类（bug/feature/enhancement/documentation/question/refactor）
    2. 优先级判定（critical/high/medium/low）
    3. 分配给对应负责人（coder/researcher/academic）
    4. 打标签
    
    Args:
        issue_data: Issue 数据字典
        repo_data: 仓库数据字典
        
    Returns:
        IssueCheckResult 检查结果
    """
    issue_number = issue_data.get("number", 0)
    issue_title = issue_data.get("title", "")
    issue_body = issue_data.get("body", "") or ""
    issue_url = issue_data.get("html_url", "")
    author = issue_data.get("user", {}).get("login", "")
    
    logger.info(f"开始 Issue #{issue_number} 的检查...")
    
    result = IssueCheckResult(issue_number=issue_number)
    
    # Step 1: 分类
    category = classify_issue(issue_title, issue_body)
    result.category = category
    logger.info(f"Issue #{issue_number} 分类: {category}")
    
    # Step 2: 确定优先级
    priority = prioritize_issue(issue_title, issue_body)
    result.priority = priority
    logger.info(f"Issue #{issue_number} 优先级: {priority}")
    
    # Step 3: 分配负责人
    if settings.ISSUE_AUTO_ASSIGN:
        assignee_role = assign_reviewer_by_round_robin()
        result.assignee_role = assignee_role
        logger.info(f"Issue #{issue_number} 分配角色: {assignee_role}")
        
        # 从角色映射到具体人员
        assignees = settings.ISSUE_ASSIGNEES.get(assignee_role, [])
        primary_assignee = assignees[0] if assignees else ""
        
        if primary_assignee:
            assign_issue_assignee(issue_number, primary_assignee)
    else:
        result.assignee_role = ""
    
    # Step 4: 添加标签
    labels_names = []
    
    # 分类标签
    if category:
        label_names.append(category)
    
    # 优先级标签
    if priority:
        label_names.append(f"priority:{priority}")
    
    # 角色标签
    if result.assignee_role:
        label_names.append(f"role:{result.assignee_role}")
    
    if label_names:
        add_issue_label(issue_number, label_names)
        result.label_added = label_names
    
    # 生成分评论
    comment_body = generate_issue_comment(result, issue_title)
    post_issue_comment(issue_number, comment_body)
    
    # 发送飞书通知
    if settings.ENABLE_ISSUE_NOTIFY:
        send_issue_created(
            issue_number,
            issue_title,
            author,
            result.category,
            result.priority,
            result.assignee_role,
        )
    
    return result


__all__ = [
    "IssueCheckResult",
    "trigger_full_issue_check",
]