"""Test that the chat execution engine imports 're' module (used on MiniMax path).

chat_engine.py is now a re-export shim; the re.sub calls live in
app.services.chat.execution_engine, which must import 're'.
"""
import ast
import inspect
from app.services.chat import execution_engine as ee_module


def test_chat_engine_imports_re():
    """re.sub is used on the MiniMax tool-call path - 're' must be imported."""
    source = inspect.getsource(ee_module)
    tree = ast.parse(source)

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)

    assert "re" in imported_names, (
        f"'re' is used via re.sub but not imported. Imports found: {sorted(imported_names)}"
    )
