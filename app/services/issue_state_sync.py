"""
Issue 状态同步模块
T6: 基于 GitHub 事件的 Issue 状态自动转换
"""
import logging
from datetime import datetime
from enum import Enum
from typing import Optional
from app.services.issue_tracker_db import (
    IssueStatus,
    IssueTrackerDB,
)
from app.core.config import settings
logger = logging.getLogger(__name__)

# GitHub Issue Event -> IssueStatus 映射
EVENT_TO_STATUS_MAP = {
    "issue_reopened": IssueStatus.REOPENED,
}

class GitHubIssueAction(str, Enum):
    """GitHub Issue Webhook action 类型"""
    OPENED = "opened"
    REOPENED = "reopened" 
    CLOSED = "closed"
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"

def determine_status_from_event(action: str, payload: dict) -> tuple[str, Optional[datetime]]:
    """
    根据 GitHub Issue action 确定新状态
    
    Args:
        action: GitHub action (opened/reopened/closed/assigned/unassigned)
        payload: GitHub webhook payload
    
    Returns: (新状态字符串, 对应时间戳或None)
    """
    if action in ("opened", "reopened"):
        return IssueStatus.NEW.value if action=="opened" else IssueStatus.REOPENED.value, None

    if action == "closed":
        # 有关联 PR 则为解决，否则为关闭
        issue_data = payload.get("issue", {})
        if issue_data.get("pull_request"):
            return IssueStatus.RESOLVED.value, datetime.now()
        return IssueStatus.CLOSED.value, datetime.now()

    if action == "assigned":
        return IssueStatus.IN_PROGRESS.value, datetime.now()

    if action == "unassigned":
        return IssueStatus.NEW.value, None
    
    # 默认不改变
    return None, None

def sync_issue_state(
    issue_number: int,
    action: str,
    payload: dict,
    tracker: IssueTrackerDB,
) -> bool:
    """
    同步 Issue 状态变更
    
    Args:
        issue_number: Issue 编号
        action: GitHub action
        payload: GitHub webhook payload  
        tracker: IssueTrackerDB 实例
    
    Returns: 是否发生状态变更
    """
    # 尝试获取当前记录，没有则创建
    existing_record = tracker.get_by_number(issue_number)
    
    if not existing_record:
        # 新 Issue，创建记录
        created_at_str = payload.get("issue", {}).get("created_at")
        if created_at_str:
            try:
                # GitHub 格式: 2024-01-01T00:00:00Z
                created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            except:
                created_dt = datetime.now()
        else:
            created_dt = datetime.now()
        
        tracker.save(
            issue_number=issue_number,
            status=IssueStatus.NEW,
            created_at=created_dt,
        )
        existing_record = tracker.get_by_number(issue_number)

    # 根据 action 判断新状态
    new_status_str, timestamp = determine_status_from_event(action, payload)

    if new_status_str:
        try:
            new_status = IssueStatus(new_status_str)
        except ValueError:
            logger.warning(f"未知状态: {new_status_str}")
            return False

        old_status_str = existing_record.get("status") if existing_record else None
        if old_status_str != new_status_str:
            update_time = timestamp or datetime.now()
            tracker.update_status(issue_number, new_status, update_time)
            
            # 也更新对应的 assignee（如果有）
            assignees_list = payload.get("issue", {}).get("assignees", [])
            if assignees_list:
                assignee_login = assignees_list[0].get("login", "")
                if assignee_login:
                    # 保存 assignee
                    tracker.save(issue_number=issue_number, assignee=assignee_login)
            
            logger.info(f"Issue #{issue_number}: {old_status_str} → {new_status_str}")
            return True

    return False

__all__ = [
    "GitHubIssueAction",
    "determine_status_from_event", 
    "sync_issue_state",
]