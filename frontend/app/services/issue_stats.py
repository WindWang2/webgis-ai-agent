"""
Issue 统计计算模块
T8: Issue 指标统计引擎
"""
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import math

from app.services.issue_tracker_db import (
    IssueStatus,
    IssueTrackerDB,
)
from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class IssueStats:
    """Issue 统计数据"""
    total_count: int
    open_count: int
    closed_count: int
    backlog_count: int
    processing_rate_percent: float
    
    avg_duration_days: float
    median_duration_days: float
    
    # Category breakdown
    categories: dict[str, int]
    
    # Priority breakdown 
    priorities: dict[str, int]
    
    # Age stats
    oldest_open_age_days: float
    newest_open_age_days: float
    
    # Period info
    period_start: str
    period_end: str


def calculate_duration_hours(start_str: str, end_str: str) -> float:
    """计算两个 ISO 时间字符串之间的时长（小时）"""
    if not start_str or not end_str:
        return 0
    try:
        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        delta = end_dt - start_dt
        return delta.total_seconds() / 3600
    except Exception as e:
        logger.warning(f"Duration parse failed: {e}")
        return 0


def compute_statistics(
    tracker: IssueTrackerDB,
    period_start: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
) -> IssueStats:
    """
    计算 Issue 统计指标

    Args:
        tracker: IssueTrackerDB 实例
        period_start: 统计起始时间（可选，默认全部）
        period_end: 统计结束时间

    Returns:
        IssueStats 统计结果
    """
    all_records = []
    
    # 获取所有数据: 合并 get_all_open + get_all_closed
    all_records.extend(tracker.get_all_open())
    all_records.extend(tracker.get_all_closed())
        
    if not all_records:
        return IssueStats(
            total_count=0, open_count=0, closed_count=0, backlog_count=0,
            processing_rate_percent=0.0, avg_duration_days=0.0,
            median_duration_days=0.0, categories={}, priorities={},
            oldest_open_age_days=0.0, newest_open_age_days=0.0,
            period_start="", period_end=""
        )

    # 基本统计
    total = len(all_records)
    open_statuses = {IssueStatus.NEW.value, IssueStatus.IN_PROGRESS.value}
    closed_statuses = {IssueStatus.RESOLVED.value, IssueStatus.CLOSED.value}

    open_count = sum(1 for r in all_records if r.get("status") in open_statuses)
    closed_count = sum(1 for r in all_records if r.get("status") in closed_statuses)
    backlog_count = open_count  # 积压 = 仍在 OPEN
    processing_rate = (closed_count / total * 100) if total > 0 else 0.0

    # 计算处理时长（仅已关闭的）
    durations_hours = []
    now = datetime.now()

    for r in all_records:
        if r.get("status") in closed_statuses:
            created_str = r.get("created_at")
            # 取最早的非 None 时间戳作为开始
            start_ts = created_str
            if r.get("resolved_at"):
                end_ts = r.get("resolved_at")
            elif r.get("closed_at"):
                end_ts = r.get("closed_at")
            else:
                end_ts = r.get("updated_at")

            if start_ts and end_ts:
                hrs = calculate_duration_hours(start_ts, end_ts)
                if hrs > 0:
                    durations_hours.append(hrs)

    if durations_hours:
        avg_hrs = sum(durations_hours) / len(durations_hours)
        avg_dur_days = avg_hrs / 24
        sorted_hrs = sorted(durations_hours)
        mid_idx = len(sorted_hrs) // 2
        med_hrs = sorted_hrs[mid_idx]
        med_dur_days = med_hrs / 24
    else:
        avg_dur_days = 0.0
        med_dur_days = 0.0

    # Category/Priority 分布
    cat_counts = defaultdict(int)
    prio_counts = defaultdict(int)

    for r in all_records:
        cat = r.get("category", "")
        if cat:
            cat_counts[cat] += 1

        prio = r.get("priority", "")
        if prio:
            prio_counts[prio] += 1

    # OPEN Issue 年龄统计
    open_records = [r for r in all_records if r.get("status") in open_statuses]
    open_ages_days = []

    for r in open_records:
        created_str = r.get("created_at")
        if created_str:
            try:
                created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                age_sec = (now - created_dt).total_seconds()
                open_ages_days.append(age_sec / 86400)
            except:
                pass

    oldest_age = max(open_ages_days) if open_ages_days else 0.0
    newest_age = min(open_ages_days) if open_ages_days else 0.0

    # 时间范围
    ps = period_start.isoformat() if period_start else ""
    pe = period_end.isoformat() if period_end else ""

    return IssueStats(
        total_count=total,
        open_count=open_count,
        closed_count=closed_count,
        backlog_count=backlog_count,
        processing_rate_percent=round(processing_rate, 1),
        avg_duration_days=round(avg_dur_days, 1),
        median_duration_days=round(med_dur_days, 1),
        categories=dict(cat_counts),
        priorities=dict(prio_counts),
        oldest_open_age_days=round(oldest_age, 1),
        newest_open_age_days=round(newest_age, 1),
        period_start=ps,
        period_end=pe,
    )


def format_weekly_report(stats: IssueStats, period_label: str) -> tuple[str, str]:
    """
    将统计结果格式化为飞书卡片消息

    Args:
        stats: IssueStats 结果
        period_label: 时间段标签，如 "3月第2周"

    Returns: (消息内容, 消息标题)
    """
    lines = [
        f"**📊 Issue 周报 ({period_label})**",
        "",
        f"✅ **处理率**: {stats.processing_rate_percent:.1f}% "
        f"({stats.closed_count}/{stats.total_count})",
        f"⏱️ **平均处理时长**: {stats.avg_duration_days:.1f} 天",
        f"📦 **积压数**: {stats.backlog_count}",
        "",
        "**分布统计**:",
    ]

    # Categories
    if stats.categories:
        lines.append("📂 分类:")
        icon_map = {
            "bug": "🐛", "feature": "✨", "enhancement": "🚀",
            "documentation": "📝", "question": "❓", "refactor": "🔧"
        }
        total_cat = sum(stats.categories.values())
        for cat, cnt in sorted(stats.categories.items()):
            pct = (cnt / total_cat * 100) if total_cat > 0 else 0
            icon = icon_map.get(cat, "📋")
            lines.append(f"  {icon} {cat}: {cnt} ({pct:.0f}%)")

    # Priorities
    if stats.priorities:
        lines.append("")
        lines.append("⭐ 优先级:")
        icon_prio = {
            "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"
        }
        total_pri = sum(stats.priorities.values())
        for prio, cnt in sorted(stats.priorities.items()):
            pct = (cnt / total_pri * 100) if total_pri > 0 else 0
            icon = icon_prio.get(prio, "⚪")
            lines.append(f"  {icon} {prio}: {cnt} ({pct:.0f}%)")

    # Backlog age
    if stats.backlog_count > 0:
        lines.extend([
            "",
            f"⏰ **积压预警**: 最久 {stats.oldest_open_age_days:.1f} 天，"
            f"最新 {stats.newest_open_age_days:.1f} 天"
        ])

    lines.extend(["", "感谢大家的努力！💪"])

    content = "\n".join(lines)
    title = f"📊 Issue 周报 ({period_label})"

    return content, title


def get_this_week_label() -> str:
    """获取本周时间段标签（如 "4月第1周"）"""
    from datetime import date
    today = date.today()
    week_num = (today.day - 1) // 7 + 1
    month_names = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]
    month = month_names[today.month - 1]
    return f"{month}月第{week_num}周"


def get_last_week_range() -> tuple[datetime, datetime]:
    """获取上一周的起止时间"""
    from datetime import timedelta, date
    today = date.today()
    # 这周一
    this_monday = today - timedelta(days=today.weekday())
    # 上周一
    last_monday = this_monday - timedelta(days=7)
    # 上周日
    last_sunday = this_monday - timedelta(days=1)
    
    return datetime.combine(last_monday, datetime.min.time()), \
           datetime.combine(last_sunday, datetime.max.time())


def generate_weekly_issue_stats(tracker: Optional[IssueTrackerDB] = None) -> tuple[str, str]:
    """
    生成上周 Issue 统计报表（用于定时任务）

    Args:
        tracker: IssueTrackerDB 实例，默认自动获取

    Returns: (消息内容, 消息标题)
    """
    if tracker is None:
        tracker = get_tracker()

    # 获取上周时间范围
    start_dt, end_dt = get_last_week_range()

    # 统计（暂时不过滤时间段，做全量统计，因为当前是增量存储）
    # TODO: 未来可改为只统计特定周期的
    stats = compute_statistics(tracker, start_dt, end_dt)

    # 标签
    label = get_this_week_label()

    return format_weekly_report(stats, label)


# 别名兼容
def get_issue_tracker():
    """获取 tracker 实例的别名"""
    return get_tracker()


__all__ = [
    "IssueStats",
    "compute_statistics",
    "format_weekly_report",
    "generate_weekly_issue_stats",
]