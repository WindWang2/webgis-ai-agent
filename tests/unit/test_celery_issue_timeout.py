"""
Issue Timeout Detection Celery Tasks Tests
T3: 超时检测 Celery 任务
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from app.services.celery_issue_tasks import (
    check_all_issues_timeouts,
)


class TestCheckIssueTimeouts:
    """Test Issue 超时检测任务"""

    @patch("app.services.celery_issue_tasks.get_tracker")
    @patch("app.services.celery_issue_tasks.send_issue_timeout_reminder")
    @patch("app.services.celery_issue_tasks.settings")
    def test_no_timeout_issues(
        self, mock_settings, mock_send_reminder, mock_get_tracker
    ):
        """测试无超时 Issue 时不发送提醒"""
        mock_settings.ISSUE_TIMEOUT_HOURS = 72
        mock_settings.ISSUE_ENABLE_TIMEOUT_REMINDER = True
        
        # Mock tracker 返回空列表
        mock_tracker_instance = Mock()
        mock_tracker_instance.find_timeout_issues.return_value = []
        mock_get_tracker.return_value = mock_tracker_instance

        # Execute
        result = check_all_issues_timeouts()

        # Verify no reminder sent
        mock_send_reminder.assert_not_called()
        assert result["checked_count"] == 0

    @patch("app.services.celery_issue_tasks.get_tracker")
    @patch("app.services.celery_issue_tasks.send_issue_timeout_reminder")
    @patch("app.services.celery_issue_tasks.settings")
    def test_one_timeout_issue_sends_reminder(
        self, mock_settings, mock_send_reminder, mock_get_tracker
    ):
        """测试有一个超时 Issue 时发送提醒"""
        mock_settings.ISSUE_TIMEOUT_HOURS = 72
        mock_settings.ISSUE_ENABLE_TIMEOUT_REMINDER = True

        # Mock tracker 返回一个超时 Issue
        mock_tracker_instance = Mock()
        mock_tracker_instance.find_timeout_issue.return_value = [{
            "issue_number": 123,
            "status": "new",
            "created_at": (datetime.now() - timedelta(hours=80)).isoformat(),
            "category": "bug",
            "assignee": "developer1"
        }]
        mock_get_tracker.return_value = mock_tracker_instance

        # Execute
        result = check_all_issues_timeouts()

        # Verify reminder sent
        mock_send_reminder.assert_called_once()


class TestEscalationLogic:
    """Test 升级提醒机制"""

    @patch("app.services.celery_issue_tasks.settings")
    def test_escalation_level_1(self, mock_settings):
        """第一次超时（72h）- 应 @责任人"""
        from app.services.celery_issue_tasks import determine_escalation_level
        
        level = determine_escalation_level(0)  # 之前没提醒过
        assert level == 1

    @patch("app.services.celery_issue_tasks.settings")
    def test_escalation_level_2(self, mock_settings):
        """第二次超时（120h）- 升级团队负责人"""
        from app.services.celery_issue_tasks import determine_escalation_level
        
        level = determine_escalation_level(1)  # 已提醒1次
        assert level == 2

    @patch("app.services.celery_issue_tasks.settings")
    def test_escalation_level_3(self, mock_settings):
        """第三次超时（168h/1周）- 通知全体"""
        from app.services.celery_issue_tasks import determine_escalation_level
        
        level = determine_escalation_level(2)  # 已提醒2次
        assert level == 3