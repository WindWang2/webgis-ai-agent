"""
PR 超时检测模块
检测待审核 PR 是否超过超时时间，发送提醒
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import httpx
from app.core.config import settings
from app.services.feishu_notifier import send_pr_timeout, send_feishu_notification
logger = logging.getLogger(__name__)
@dataclass
class TimeoutInfo:
    """超时检测信息"""
    pr_number: int
    pr_title: str
    pr_url: str
    author: str
    hours_waiting: int
    is_timed_out: bool
    last_reminder_sent: Optional[str] = None
def get_pending_prs() -> list[dict]:
    """
    获取待审核的 PR 列表（通过 GitHub API）
    
    Returns:
        PR 信息列表
    """
    if not settings.GITHUB_TOKEN or not settings.GITHUB_REPO_OWNER or not settings.GITHUB_REPO_NAME:
        logger.warning("GitHub 配置不完整，无法获取 PR 列表")
        return []
    
    # 搜索状态为 open 且没有合并的 PR
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO_OWNER}/{settings.GITHUB_REPO_NAME}/pulls"
    
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                url,
                params={"state": "open"},
                headers={
                    "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "WebGIS-AI-Agent"
                }
            )
            if resp.status_code == 200:
                prs = resp.json()
                result = []
                for pr in prs:
                    # 排除已经是 merge 状态的
                    if not pr.get("merged"):
                        result.append({
                            "number": pr.get("number"),
                            "title": pr.get("title"),
                            "url": pr.get("html_url"),
                            "user": pr.get("user", {}).get("login", ""),
                            "created_at": pr.get("created_at"),
                            "updated_at": pr.get("updated_at"),
                        })
                return result
            else:
                logger.error(f"获取 PR 列表失败: {resp.status_code}")
                return []
    except Exception as e:
        logger.exception(f"获取 PR 列表异常: {e}")
        return []

def get_pr_review_requests(pr_number: int) -> list[str]:
    """
    获取 PR 的待处理审核请求
    
    Args:
        pr_number: PR 编号
        
    Returns:
        审核人列表
    """
    if not settings.GITHUB_TOKEN or not settings.GITHUB_REPO_OWNER or not settings.GITHUB_REPO_NAME:
        return []
    
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO_OWNER}/{settings.GITHUB_REPO_NAME}/pulls/{pr_number}/requested_reviewers"
    
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
                reviewers = resp.json().get("users", [])
                return [r.get("login", "") for r in reviewers]
            return []
    except Exception as e:
        logger.exception(f"获取审核人列表异常: {e}")
        return []

def calculate_wait_hours(created_at: str) -> int:
    """
    计算自创建以来的等待小时数
    
    Args:
        created_at: ISO 8601 时间字符串
        
    Returns:
        小时数
    """
    try:
        # GitHub 返回的时间格式: 2026-04-03T12:34:56Z
        created_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(created_time.tzinfo)
        delta = now - created_time
        return int(delta.total_seconds() / 3600)
    except Exception:
        return 0

def check_single_pr_timeout(pr_info: dict) -> TimeoutInfo:
    """
    检查单个 PR 是否超时
    
    Args:
        pr_info: PR 信息字典
        
    Returns:
        TimeoutInfo
    """
    pr_number = pr_info.get("number", 0)
    pr_title = pr_info.get("title", "")
    pr_url = pr_info.get("url", "")
    author = pr_info.get("user", "")
    created_at = pr_info.get("created_at", "")
    
    hours = calculate_wait_hours(created_at)
    timeout_hours = settings.PR_TIMEOUT_HOURS
    is_timed_out = hours >= timeout_hours
    
    return TimeoutInfo(
        pr_number=pr_number,
        pr_title=pr_title,
        pr_url=pr_url,
        author=author,
        hours_waiting=hours,
        is_timed_out=is_timed_out
    )

# 已发送过提醒的 PR 记录（内存存储，生产环境应该用 Redis）
_reminded_prs: dict[int, str] = {}

def check_timeout() -> list[TimeoutInfo]:
    """
    主函数：扫描所有待审核 PR，检测超时并发送提醒
    
    Returns:
        超时的 PR 列表
    """
    if not settings.PR_ENABLE_TIMEOUT_REMINDER:
        logger.info("超时提醒已禁用")
        return []
    
    if not settings.ENABLE_FEISHU_NOTIFY:
        logger.warning("飞书通知未启用，跳过超时提醒")
        return []
    
    pending_prs = get_pending_prs()
    timed_out_prs = []
    
    for pr_info in pending_prs:
        timeout_info = check_single_pr_timeout(pr_info)
        
        if timeout_info.is_timed_out:
            # 检查是否已经发送过提醒（同一次超时只提醒一次）
            if timeout_info.pr_number not in _reminded_prs:
                # 发送飞书提醒
                reviewer_list = get_pr_review_requests(timeout_info.pr_number)
                
                if reviewer_list:
                    # @具体审核人
                    mentions = " ".join([f"@{r}" for r in reviewer_list])
                    content = (
                        f"**PR #{timeout_info.pr_number}: {timeout_info.pr_title}**\\n\\n"
                        f"⏰ 已等待 {timeout_info.hours_waiting} 小时，超过阈值 {settings.PR_TIMEOUT_HOURS}h\\n"
                        f"👤 作者: {timeout_info.author}\\n"
                        f"审核人: {mentions}\\n"
                        f"📎 地址: [{timeout_info.pr_url}]({timeout_info.pr_url})"
                    )
                    send_feishu_notification(content, "⏰ PR 审核超时提醒")
                    
                    # 记录已发送
                    _reminded_prs[timeout_info.pr_number] = datetime.now().isoformat()
                    
                    logger.info(f"已发送 PR #{timeout_info.pr_number} 超时提醒")
            
            timed_out_prs.append(timeout_info)
    
    if timed_out_prs:
        logger.info(f"发现 {len(timed_out_prs)} 个超时 PR")
    
    return timed_out_prs

def reset_reminder_record(pr_number: int):
    """
    当 PR 被审核后，重置提醒记录
    
    Args:
        pr_number: PR 编号
    """
    if pr_number in _reminded_prs:
        del _reminded_prs[pr_number]
        logger.info(f"已重置 PR #{pr_number} 的提醒记录")

__all__ = [
    "check_timeout",
    "check_single_pr_timeout",
    "get_pending_prs",
    "TimeoutInfo"
]