"""Security: Critical API routes must require authentication.

P0: knowledge, task, and report endpoints are completely unauthenticated.
Any anonymous user can CRUD documents, list all tasks, generate/delete reports.
"""
import ast
import pytest


# Auth-enforcing dependencies that the AST check accepts. A route is
# "authenticated" if it declares any of these as a Depends(...) default.
# - get_current_user: standard JWT bearer auth (raises 401 on missing/invalid).
# - require_owned_session: composes get_current_user_optional + owner_token +
#   session-ownership verification; rejects any caller who neither authenticates
#   nor holds the session's owner_token. Treated as auth-enforcing because it
#   cannot succeed for an anonymous caller on a session they do not own.
AUTH_DEPS = ("get_current_user", "require_owned_session")


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


def _has_auth(source: str, function_name: str) -> bool:
    """True if the endpoint declares any auth-enforcing Depends(...) default."""
    args = _get_endpoint_auth_args(source, function_name)
    return any(dep in a for a in args for dep in AUTH_DEPS)


class TestKnowledgeAuth:
    """Knowledge CRUD endpoints must require authentication."""

    @pytest.fixture
    def source(self):
        with open("app/api/routes/knowledge.py") as f:
            return f.read()

    def test_add_document_requires_auth(self, source):
        assert _has_auth(source, "add_document"), "add_document has no auth dependency"

    def test_list_documents_requires_auth(self, source):
        assert _has_auth(source, "list_documents"), "list_documents has no auth dependency"

    def test_semantic_search_requires_auth(self, source):
        assert _has_auth(source, "semantic_search"), "semantic_search has no auth dependency"

    def test_delete_document_requires_auth(self, source):
        assert _has_auth(source, "delete_document"), (
            "delete_document has no auth dependency - any anonymous user can delete docs"
        )

    def test_retrieve_context_requires_auth(self, source):
        assert _has_auth(source, "retrieve_context"), "retrieve_context has no auth dependency"

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
        assert _has_auth(source, "get_task"), (
            "get_task has no auth - any user can inspect any task"
        )

    def test_list_tasks_requires_auth(self, source):
        # list_tasks is guarded by require_owned_session, which composes
        # get_current_user_optional + session-ownership verification: an
        # anonymous caller cannot list another session's tasks (audit S33).
        assert _has_auth(source, "list_tasks"), (
            "list_tasks has no auth - returns all tasks across sessions"
        )

    def test_cancel_task_requires_auth(self, source):
        assert _has_auth(source, "cancel_task"), (
            "cancel_task has no auth - any user can cancel any task"
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
        assert _has_auth(source, "create_report"), "create_report has no auth"

    def test_list_reports_requires_auth(self, source):
        assert _has_auth(source, "list_reports"), (
            "list_reports has no auth - any user can list all reports"
        )

    def test_get_report_requires_auth(self, source):
        assert _has_auth(source, "get_report"), "get_report has no auth"

    def test_download_report_requires_auth(self, source):
        assert _has_auth(source, "download_report"), "download_report has no auth"

    def test_create_share_link_requires_auth(self, source):
        assert _has_auth(source, "create_share_link"), (
            "create_share_link has no auth - any user can share any report"
        )

    def test_imports_auth_dependency(self, source):
        assert "get_current_user" in source, (
            "report.py does not import get_current_user"
        )
