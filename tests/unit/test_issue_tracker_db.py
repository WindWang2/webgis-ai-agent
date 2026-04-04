"""
Issue Tracking Database Module Tests
T1: 数据库模块 - IssueTracking 持久化
"""
import pytest
import os
import tempfile
from datetime import datetime, timedelta
from app.services.issue_tracker_db import (
    IssueTrackerDB,
    IssueStatus,
)


@pytest.fixture
def temp_db():
    """创建临时数据库用于测试"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def tracker(temp_db):
    """创建 IssueTracker 实例"""
    return IssueTrackerDB(db_path=temp_db)


class TestIssueStatus:
    """Test Issue Status Enum"""

    def test_status_values(self):
        assert IssueStatus.NEW.value == "new"
        assert IssueStatus.IN_PROGRESS.value == "in_progress"
        assert IssueStatus.RESOLVED.value == "resolved"
        assert IssueStatus.CLOSED.value == "closed"
        assert IssueStatus.REOPENED.value == "reopened"


class TestIssueTrackerDB:
    """Test Issue Tracker Database Operations"""

    def test_save_new_issue(self, tracker):
        """测试保存新 Issue"""
        now = datetime.now()
        tracker.save(
            issue_number=1,
            status=IssueStatus.NEW,
            created_at=now,
            category="bug",
            priority="high",
            assignee="developer1"
        )

        # 查询验证
        result = tracker.get_by_number(1)
        assert result is not None
        assert result["issue_number"] == 1
        assert result["status"] == IssueStatus.NEW.value
        assert result["category"] == "bug"
        assert result["priority"] == "high"

    def test_update_status(self, tracker):
        """测试更新 Issue 状态"""
        now = datetime.now()
        tracker.save(
            issue_number=2,
            status=IssueStatus.NEW,
            created_at=now,
            category="feature",
            priority="medium"
        )

        # 更新为进行中
        tracker.update_status(
            issue_number=2,
            new_status=IssueStatus.IN_PROGRESS,
            timestamp=datetime.now()
        )

        result = tracker.get_by_number(2)
        assert result["status"] == IssueStatus.IN_PROGRESS.value

    def test_get_all_open_issues(self, tracker):
        """测试获取所有 OPEN 状态 Issue（NEW + IN_PROGRESS）"""
        now = datetime.now()

        # 创建一些 Issue，有不同状态
        tracker.save(issue_number=1, status=IssueStatus.NEW, created_at=now, category="bug")
        tracker.save(issue_number=2, status=IssueStatus.IN_PROGRESS, created_at=now, category="feature")
        tracker.save(issue_number=3, status=IssueStatus.RESOLVED, created_at=now, category="enhancement")

        open_issues = tracker.get_all_open()
        assert len(open_issue) == 2

    def test_update_reminder_count(self, tracker):
        """测试更新提醒次数"""
        now = datetime.now()
        tracker.save(
            issue_number=10,
            status=IssueStatus.NEW,
            created_at=now,
            category="bug"
        )

        tracker.increment_reminder(10)

        result = tracker.get_by_number(10)
        assert result["reminder_count"] == 1

        tracker.increment_reminder(10)
        result = tracker.get_by_number(10)
        assert result["reminder_count"] == 2

    def test_delete_issue(self, tracker):
        """测试删除 Issue"""
        now = datetime.now()
        tracker.save(issue_number=99, status=IssueStatus.NEW, created_at=now)

        tracker.delete(99)
        result = tracker.get_by_number(99)
        assert result is None

    def test_find_timeout_issues(self, tracker):
        """测试查找超时 Issue"""
        now = datetime.now()
        old_time = now - timedelta(hours=80)  # 已超时

        # 创建一个超时的 NEW Issue
        tracker.save(
            issue_number=100,
            status=IssueStatus.NEW,
            created_at=old_time,
            category="bug",
            reminder_count=0
        )

        # 刚创建的 Issue（未超时）
        tracker.save(
            issue_number=101,
            status=IssueStatus.NEW,
            created_at=now,
            category="feature"
        )

        # 已提醒过的超期 Issue
        tracker.save(
            issue_number=102,
            status=IssueStatus.IN_PROGRESS,
            created_at=old_time,
            category="enhancement",
            reminder_count=1
        )

        # 查找超时未提醒
        timeout_issues = tracker.find_timeout_issues(timeout_hours=72, max_reminders=0)
        assert len(timeout_issue) >= 1
        assert 100 in [i["issue_number"] for i in timeout_issue]