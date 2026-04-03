"""
T005 报告生成功能冒烟测试 (简化版)
测试核心报告功能的可用性和稳定性
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """创建测试客户端"""
    from app.main import app
    return TestClient(app)


class TestReportBasicFunctionality:
    """基本功能冒烟测试"""

    def test_api_root_accessible(self, client):
        """API 根路径可访问"""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "name" in data

    def test_reports_endpoint_exists(self, client):
        """报告端点存在，处理正常"""
        response = client.post(
            "/api/v1/reports/generate",
            json={"task_id": 999999}
        )
        assert response.status_code == 200
        data = response.json()
        assert "success" in data or "code" in data

    def test_invalid_format_rejected(self, client):
        """无效格式被正确拒绝"""
        response = client.post(
            "/api/v1/reports/generate",
            json={"task_id": 1, "format": "exe"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False


class TestReportStructureValidation:
    """报告结构验证测试"""

    def test_markdown_template_renders(self):
        """Markdown模板能正常渲染"""
        from app.services.report_service import ReportService
        svc = ReportService()

        test_data = {
            "report_title": "Test Report",
            "task_info": {
                "id": 1,
                "type": "buffer",
                "status": "completed",
                "created_at": "2024-01-01 10:00:00",
                "completed_at": "2024-01-01 10:05:00",
                "duration": 300.0
            },
            "parameters": {"distance": 100},
            "summary": {"area": 1000},
            "statistics": {"area": 1000},
            "generated_at": "2024-01-01 12:00:00"
        }

        result = svc._render_markdown_template(test_data)
        assert "Test Report" in result

    def test_html_default_template(self):
        """HTML默认模板能正常渲染"""
        from app.services.report_service import ReportService
        svc = ReportService()

        test_data = {
            "report_title": "Test HTML Report",
            "task_info": {
                "id": 1,
                "type": "buffer",
                "status": "completed",
                "created_at": "2024-01-01",
                "completed_at": "2024-01-01",
                "duration": 300.0
            },
            "parameters": {},
            "summary": {},
            "statistics": {},
            "generated_at": "2024-01-01"
        }

        result = svc._render_html_template(test_data)
        assert len(result) > 100


class TestReportErrorHandling:
    """错误处理冒烟测试"""

    def test_nonexistent_task_handled(self, client):
        """不存在的任务ID被优雅处理"""
        response = client.post(
            "/api/v1/reports/generate",
            json={"task_id": 999999}
        )
        data = response.json()
        assert data["success"] is False

    def test_missing_parameter_handled(self, client):
        """缺少必需参数被正确处理"""
        response = client.post(
            "/api/v1/reports/generate",
            json={}
        )
        assert response.status_code in [200, 422]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
