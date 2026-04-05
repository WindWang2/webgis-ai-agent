"""
Issue 超时检测 Celery 任务
T3: 超时检测任务
T5: 升级提醒机制
"""
import logging
from datetime import datetime
from app.core.config import settings
from app.services.issue_tracker_db import (
    IssueStatus,
    get_tracker,
)
from app.services.feishu_notification import (
    send_feishu_notification,
)

logger = logging.getLogger(__name__)
celery_app = None  # 延迟初始化

# 升级阈值配置
ESCALATION_LEVELS = {
    1: {"hours": 72, "desc": "@责任人"},
    2: {"hours": 120, "desc": "@责任人 + 团队负责人"},
    3: {"hours": 168, "desc": "@全体群成员"},
}


def determine_escalation_level(reminder_count: int) -> int:
    """
    根据已提醒次数确定升级级别
    Args:
        reminder_count: 当前已提醒次数
    Returns: 升级级别 1/2/3
    """
    if reminder_count <= 0:
        return 1
    elif reminder_count == 1:
        return 2
    else:
        return 3


def get_escalation_message(level: int, issue_data: dict) -> tuple[str, str]:
    """
    生成升级消息内容
    Args:
        level: 升级级别
        issue_data: Issue 数据
    Returns: (消息内容, 消息标题)
    """
    issue_num = issue_data.get("issue_number", "?")
    title = issue_data.get("title", "")
    assignee = issue_data.get("assignee", "待分配")

    hours_cfg = ESCALATION_LEVELS.get(level, {"hours": 72})
    hours = hours_cfg.get("hours", 72)

    if level == 1:
        msg_title = f"⏰ Issue #{issue_num} 处理超时提醒"
        content = (
            f"**Issue #{issue_num}: {title}**\n\n"
            f"⏱️ 已等待超过 {hours} 小时，仍未处理\n"
            f"👨‍💼 负责人员: @{assignee}\n"
            f"请尽快处理！"
        )
    elif level == 2:
        msg_title = f"🚨 Issue #{issue_num} 二次超时警告"
        content = (
            f"**🚨 Issue #{issue_num} 等待超过 {hours} 小时**\n\n"
            f"⚠️ 仍未得到处理，需要关注！\n"
            f"👨‍💼 负责: @{assignee}\n"
            f"请团队负责人协助跟进。"
        )
    else:
        msg_title = f"🔥 Issue #{issue_num} 已超时一周！"
        content = (
            f"**🔥 Issue #{issue_num} 超时一周以上**\n\n"
            f"📋 标题: {title}\n"
            f"⏱️ 等待时间: {hours}+ 小时\n"
            f"👨‍💼 最初负责: @{assignee}\n"
            f"请全体关注，尽快解决！"
        )

    return content, msg_title


def send_issue_timeout_reminder(
    issue_number: int,
    issue_title: str,
    assignee: str,
    reminder_count: int,
    age_hours: float,
) -> bool:
    """
    发送 Issue 超时提醒飞书消息
    Args:
        issue_number: Issue 编号
        issue_title: Issue 标题
        assignee: 受让人
        reminder_count: 已提醒次数（决定升级级别）
        age_hours: 已等待小时数
    Returns: 是否发送成功
    """
    issue_data = {
        "issue_number": issue_number,
        "title": issue_title,
        "assignee": assignee,
    }

    level = determine_escalation_level(reminder_count)
    content, title = get_escalation_message(level, issue_data)

    return send_feishu_notification(content=content, title=title)


def check_all_issues_timeouts() -> dict:
    """
    Celery 定时任务：检查所有 Issue 是否超时并发送提醒
    Returns: 检查结果统计
    """
    if not settings.ISSUE_ENABLE_TIMEOUT_REMINDER:
        logger.info("Issue 超时提醒已禁用，跳过检查")
        return {"skipped": True, "reason": "disabled"}

    timeout_hours = settings.ISSUE_TIMEOUT_HOURS
    tracker = get_tracker()

    # 找超时 Issue（未达到最大提醒次数）
    timeout_issues = tracker.find_timeout_issues(timeout_hours=timeout_hours, max_reminders=10)

    checked_count = 0
    reminded_count = 0

    for issue in timeout_issues:
        issue_num = issue.get("issue_number")
        reminder_count = issue.get("reminder_count", 0)

        # 计算已等待小时数
        created_at_str = issue.get("created_at") or ""
        if created_at_str:
            created_dt = datetime.fromisoformat(created_at_str)
            age_hours = (datetime.now() - created_dt).total_seconds() / 3600
        else:
            age_hours = 0

        checked_count += 1

        title = f"Issue #{issue_num}"
        assignee = issue.get("assignee", "")

        success = send_issue_timeout_reminder(
            issue_num, title, assignee, reminder_count, age_hours
        )

        if success:
            tracker.increment_reminder(issue_num)
            reminded_count += 1
            logger.info(f"Issue #{issue_num} 超时提醒已发送，累计 {reminder_count + 1} 次")

    result = {
        "checked_count": checked_count,
        "reminded_count": reminded_count,
        "timestamp": datetime.now().isoformat(),
    }
    logger.info(f"Issue 超时检查完成: {result}")
    return result


def register_celery_task(celery_instance):
    """
    向 Celery 应用注册定时任务
    Args:
        celery_instance: Celery 应用实例
    """
    global celery_app
    celery_app = celery_instance

    celery_instance.conf.beat_schedule = {
        "check-issue-timeouts-every-hour": {
            "task": "app.services.celery_issue_tasks.check_all_issues_timeouts",
            "schedule": 3600.0,  # 每小时
        },
    }


__all__ = [
    "check_all_issues_timeouts",
    "send_issue_timeout_reminder",
    "determine_escalation_level",
    "get_escalation_message",
    "register_celery_task",
]