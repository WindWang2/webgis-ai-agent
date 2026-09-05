"""Security: mutating API routes must require authentication.

#618-28: the old check used ``dep in source_segment`` substring matching, so a
Depends named ``get_current_user_optional`` satisfied ``get_current_user``.
Auth deps are now matched as full AST identifiers (Name / Attribute.attr).

Coverage scans every ``app/api/routes/*.py`` public POST/PUT/DELETE/PATCH that
mutates data. Intentionally public mutating endpoints live in
``PUBLIC_MUTATING_ALLOWLIST`` with a reason.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROUTES_DIR = Path("app/api/routes")

# Auth-enforcing Depends(...) identifiers. A route is authenticated if it
# declares any of these as a Depends default / Annotated Depends, OR if it
# pairs get_current_user_optional with an in-body ownership check.
# - get_current_user: JWT bearer (401 on missing/invalid). Exact name — does
#   NOT match get_current_user_optional.
# - get_current_user_with_version: same, plus token_version / logout.
# - require_owned_session: get_current_user_optional + owner_token + session
#   ownership; anonymous callers cannot touch a session they do not own.
# - require_admin: get_current_user_with_version + role=admin.
# - verify_bridge_secret: Pi HTTP-callback HMAC (pi_tools.py).
AUTH_ENFORCING_DEPS = frozenset(
    {
        "get_current_user",
        "get_current_user_with_version",
        "require_owned_session",
        "require_admin",
        "verify_bridge_secret",
    }
)

# Optional auth is NOT enforcing on its own. Combined with one of these
# in-body checks it is (anonymous session owner / project ACL).
OPTIONAL_AUTH_DEP = "get_current_user_optional"
BODY_OWNERSHIP_CHECKS = frozenset(
    {
        "verify_session_owner",
        "require_owned_session",
        "_guard_body_session",
        "get_project_with_auth",
        "authorize_session_write",
        "_durable_or_404",  # jobs.py: owner-scoped fetch, 404 if not owned
    }
)

MUTATING_HTTP = frozenset({"post", "put", "delete", "patch"})

# Intentionally public mutating endpoints. Keyed (filename, function_name).
PUBLIC_MUTATING_ALLOWLIST: dict[tuple[str, str], str] = {
    ("auth.py", "register"): "public registration issues the first JWT",
    ("auth.py", "login"): "public login issues JWT",
    ("auth.py", "refresh"): "public refresh-token rotation; no access JWT yet",
    # geocompute plan validation is pure-CPU over the caller-submitted DAG:
    # no catalog/data access, response carries only derived fingerprints —
    # deliberately optional-auth (contract: test_validate_stays_optional_auth).
    ("geocompute.py", "validate_execution_plan"): (
        "stateless pure-CPU plan validation; no data/catalog access"
    ),
}


def _depends_identifiers(node: ast.AST) -> list[str]:
    """Identifier names passed to Depends(...) anywhere under ``node``.

    Matches ``Depends(name)`` and ``Depends(mod.name)`` as the full identifier
    (Name.id / Attribute.attr). Does not substring-match: ``get_current_user``
    is not found inside ``get_current_user_optional``.
    """
    names: list[str] = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        func = n.func
        is_depends = (isinstance(func, ast.Name) and func.id == "Depends") or (
            isinstance(func, ast.Attribute) and func.attr == "Depends"
        )
        if not is_depends or not n.args:
            continue
        arg = n.args[0]
        if isinstance(arg, ast.Name):
            names.append(arg.id)
        elif isinstance(arg, ast.Attribute):
            names.append(arg.attr)
    return names


def _fn_depends(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    names: list[str] = []
    for d in list(fn.args.defaults) + list(fn.args.kw_defaults):
        if d is not None:
            names.extend(_depends_identifiers(d))
    for arg in list(fn.args.posonlyargs) + list(fn.args.args) + list(fn.args.kwonlyargs):
        if arg.annotation is not None:
            names.extend(_depends_identifiers(arg.annotation))
    return names


def _fn_body_names(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
    return names


def _has_auth_fn(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    deps = _fn_depends(fn)
    if any(d in AUTH_ENFORCING_DEPS for d in deps):
        return True
    if OPTIONAL_AUTH_DEP in deps and (_fn_body_names(fn) & BODY_OWNERSHIP_CHECKS):
        return True
    return False


def _get_function(source: str, function_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return node
    return None


def _has_auth(source: str, function_name: str) -> bool:
    """True if the endpoint declares an auth-enforcing Depends(...) default."""
    fn = _get_function(source, function_name)
    if fn is None:
        return False
    return _has_auth_fn(fn)


def _http_methods(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    methods: set[str] = set()
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr in {
            "get",
            "post",
            "put",
            "delete",
            "patch",
        }:
            methods.add(target.attr)
    return methods


def _mutating_routes(path: Path) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _http_methods(node) & MUTATING_HTTP:
                found.append(node)
    return found


class TestAuthDepIdentifierMatch:
    """#618-28: substring matching is a false-positive for *_optional."""

    def test_optional_dep_does_not_satisfy_get_current_user(self):
        src = (
            "from fastapi import Depends\n"
            "async def add_document(user=Depends(get_current_user_optional)):\n"
            "    return None\n"
        )
        assert not _has_auth(src, "add_document")

    def test_exact_get_current_user_counts(self):
        src = (
            "from fastapi import Depends\n"
            "async def add_document(user=Depends(get_current_user)):\n"
            "    return None\n"
        )
        assert _has_auth(src, "add_document")

    def test_with_version_counts(self):
        src = (
            "from fastapi import Depends\n"
            "async def add_document(user=Depends(get_current_user_with_version)):\n"
            "    return None\n"
        )
        assert _has_auth(src, "add_document")

    def test_optional_plus_verify_session_owner_counts(self):
        src = (
            "from fastapi import Depends\n"
            "async def cancel_job(user=Depends(get_current_user_optional)):\n"
            "    await verify_session_owner(db, session_id, user_id=user['user_id'])\n"
        )
        assert _has_auth(src, "cancel_job")


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
        """Module must import an auth-enforcing dependency (exact identifier)."""
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.name)
        assert imported & AUTH_ENFORCING_DEPS, (
            "knowledge.py does not import an auth-enforcing dependency"
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
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.name)
        assert imported & AUTH_ENFORCING_DEPS, (
            "task.py does not import an auth-enforcing dependency"
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
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.name)
        assert imported & AUTH_ENFORCING_DEPS, (
            "report.py does not import an auth-enforcing dependency"
        )


class TestAllMutatingRoutesAuth:
    """Every mutating route in app/api/routes/*.py is auth-enforcing or allowlisted."""

    def test_mutating_routes_use_auth_enforcing_depends(self):
        missing: list[str] = []
        allowlisted_seen: set[tuple[str, str]] = set()
        scanned = 0
        for path in sorted(ROUTES_DIR.glob("*.py")):
            if path.name == "__init__.py":
                continue
            for fn in _mutating_routes(path):
                scanned += 1
                key = (path.name, fn.name)
                if key in PUBLIC_MUTATING_ALLOWLIST:
                    allowlisted_seen.add(key)
                    continue
                if not _has_auth_fn(fn):
                    missing.append(f"{path.name}:{fn.name}")
        assert scanned > 0, "scanner found no mutating routes — AST walk broke"
        unused = set(PUBLIC_MUTATING_ALLOWLIST) - allowlisted_seen
        assert not unused, f"allowlist entries do not match a mutating route: {sorted(unused)}"
        assert not missing, (
            "mutating POST/PUT/DELETE/PATCH without an auth-enforcing Depends "
            f"(get_current_user / require_owned_session / require_admin / "
            f"verify_bridge_secret, or get_current_user_optional + ownership "
            f"check). Allowlist with a reason if intentionally public: {missing}"
        )

    def test_allowlist_reasons_are_nonempty(self):
        for key, reason in PUBLIC_MUTATING_ALLOWLIST.items():
            assert reason.strip(), f"{key} allowlist reason is empty"
