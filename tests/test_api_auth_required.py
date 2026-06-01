"""Security: Critical API routes must require authentication.

P0: knowledge, task, and report endpoints are completely unauthenticated.
Any anonymous user can CRUD documents, list all tasks, generate/delete reports.
"""
import ast
import pytest


def _get_endpoint_auth_args(source: str, function_name: str) -> list[str]:
    """Parse source and return all Depends(...) strings for a given function."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
            args = node.args
            defaults = args.defaults
            kw_defaults = args.kw_defaults
            all_defaults = defaults + kw_defaults
            result = []
            for d in all_defaults:
                if d is None:
                    continue
                seg = ast.get_source_segment(source, d)
                if seg and "Depends" in seg:
                    result.append(seg)
            return result
    return []


class TestKnowledgeAuth:
    """Knowledge CRUD endpoints must require authentication."""

    @pytest.fixture
    def source(self):
        with open("app/api/routes/knowledge.py") as f:
            return f.read()

    def test_add_document_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "add_document")
        assert any("get_current_user" in a for a in args), (
            "add_document has no auth dependency"
        )

    def test_list_documents_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "list_documents")
        assert any("get_current_user" in a for a in args), (
            "list_documents has no auth dependency"
        )

    def test_semantic_search_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "semantic_search")
        assert any("get_current_user" in a for a in args), (
            "semantic_search has no auth dependency"
        )

    def test_delete_document_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "delete_document")
        assert any("get_current_user" in a for a in args), (
            "delete_document has no auth dependency — any anonymous user can delete docs"
        )

    def test_retrieve_context_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "retrieve_context")
        assert any("get_current_user" in a for a in args), (
            "retrieve_context has no auth dependency"
        )

    def test_imports_auth_dependency(self, source):
        """Module must import get_current_user from auth."""
        assert "get_current_user" in source, (
            "knowledge.py does not import get_current_user"
        )


class TestTaskAuth:
    """Task endpoints must require authentication."""

    @pytest.fixture
    def source(self):
        with open("app/api/routes/task.py") as f:
            return f.read()

    def test_get_task_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "get_task")
        assert any("get_current_user" in a for a in args), (
            "get_task has no auth — any user can inspect any task"
        )

    def test_list_tasks_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "list_tasks")
        assert any("get_current_user" in a for a in args), (
            "list_tasks has no auth — returns all tasks across sessions"
        )

    def test_cancel_task_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "cancel_task")
        assert any("get_current_user" in a for a in args), (
            "cancel_task has no auth — any user can cancel any task"
        )

    def test_imports_auth_dependency(self, source):
        assert "get_current_user" in source, (
            "task.py does not import get_current_user"
        )


class TestReportAuth:
    """Report endpoints must require authentication (except shared view)."""

    @pytest.fixture
    def source(self):
        with open("app/api/routes/report.py") as f:
            return f.read()

    def test_create_report_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "create_report")
        assert any("get_current_user" in a for a in args), (
            "create_report has no auth"
        )

    def test_list_reports_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "list_reports")
        assert any("get_current_user" in a for a in args), (
            "list_reports has no auth — any user can list all reports"
        )

    def test_get_report_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "get_report")
        assert any("get_current_user" in a for a in args), (
            "get_report has no auth"
        )

    def test_download_report_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "download_report")
        assert any("get_current_user" in a for a in args), (
            "download_report has no auth"
        )

    def test_create_share_link_requires_auth(self, source):
        args = _get_endpoint_auth_args(source, "create_share_link")
        assert any("get_current_user" in a for a in args), (
            "create_share_link has no auth — any user can share any report"
        )

    def test_imports_auth_dependency(self, source):
        assert "get_current_user" in source, (
            "report.py does not import get_current_user"
        )
