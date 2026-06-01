"""Security: Swagger/ReDoc must be disabled in production."""
import ast
import inspect
from app import main as main_module


def test_docs_url_gated_by_is_production():
    """docs_url in FastAPI() constructor must use is_production() guard."""
    source = inspect.getsource(main_module)
    tree = ast.parse(source)

    # Find the FastAPI() call and check docs_url argument
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "FastAPI":
                for kw in node.keywords:
                    if kw.arg == "docs_url":
                        # Must be a conditional expression: "..." if not ... else None
                        assert isinstance(kw.value, ast.IfExp), (
                            f"docs_url should use IfExp (conditional), got {type(kw.value).__name__}"
                        )
                        # The else branch should be None
                        assert isinstance(kw.value.orelse, ast.Constant) and kw.value.orelse.value is None, (
                            "docs_url else branch should be None"
                        )
                        return

    pytest.fail("docs_url not found in FastAPI() constructor")


def test_redoc_url_gated_by_is_production():
    """redoc_url in FastAPI() constructor must use is_production() guard."""
    source = inspect.getsource(main_module)
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "FastAPI":
                for kw in node.keywords:
                    if kw.arg == "redoc_url":
                        assert isinstance(kw.value, ast.IfExp), (
                            f"redoc_url should use IfExp (conditional), got {type(kw.value).__name__}"
                        )
                        assert isinstance(kw.value.orelse, ast.Constant) and kw.value.orelse.value is None, (
                            "redoc_url else branch should be None"
                        )
                        return

    pytest.fail("redoc_url not found in FastAPI() constructor")


import pytest
