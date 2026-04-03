"""
PR 检查流程模块单元测试
"""
import pytest
from unittest.mock import patch, MagicMock
from app.services.pr_check_flow import (
    PRCheckSummary,
    generate_pr_comment,
    post_pr_comment,
)


class TestPRCheckSummary:
    """测试检查结果数据类"""

    def test_default_creation(self):
        """默认创建应为通过状态"""
        result = PRCheckSummary(pr_number=1, passed=True)

        assert result.pr_number == 1
        assert result.passed is True
        assert result.linter_passed is True
        assert result.coverage_passed is True
        assert result.security_passed is True

    def test_failed_creation(self):
        """失败时应标记各检查项"""
        result = PRCheckSummary(
            pr_number=1,
            passed=False,
            linter_passed=False,
            coverage_percent=50.0
        )

        assert result.passed is False
        assert result.linter_passed is False
        assert result.coverage_percent == 50.0


class TestGeneratePRComment:
    """测试 PR 评论生成"""

    def test_generate_pass_message(self):
        """通过时显示正确标记"""
        result = PRCheckSummary(pr_number=1, passed=True)
        
        comment = generate_pr_comment(result)

        assert "✅" in comment
        assert "PR 自动检查结果" in comment
        assert "通过" in comment

    def test_generate_fail_message(self):
        """失败时显示错误标记和问题"""
        result = PRCheckSummary(pr_number=1, passed=False)
        result.linter_passed = False
        result.coverage_percent = 60.0
        result.coverage_passed = False
        result.security_passed = True
        result.security_issue_count = 0
        
        comment = generate_pr_comment(result)

        assert "❌" in comment
        assert "未通过" in comment
        assert "请修复" in comment

    def test_generate_includes_threshold(self):
        """评论应包含阈值信息"""
        result = PRCheckSummary(pr_number=1, passed=True)
        result.coverage_percent = 85.0
        result.coverage_passed = True
        
        comment = generate_pr_comment(result)

        assert "85." in comment


class TestPostPRComment:
    """测试 PR 评论发布"""

    @patch("httpx.Client")
    def test_post_success(self, mock_client_class):
        """成功发布"""
        mock_response = MagicMock()
        mock_response.status_code = 201

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_class.return_value = mock_client

        with patch("app.services.pr_check_flow.settings") as mock_settings:
            mock_settings.GITHUB_TOKEN = "fake_token"
            mock_settings.GITHUB_REPO_OWNER = "owner"
            mock_settings.GITHUB_REPO_NAME = "repo"

            result = post_pr_comment(123, "Test comment")

            assert result is True

    @patch("httpx.Client")
    def test_post_no_config(self):
        """配置不全时应返回失败"""
        with patch("app.services.pr_check_flow.settings") as mock_settings:
            mock_settings.GITHUB_TOKEN = ""

            result = post_pr_comment(123, "Test comment")

            assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])