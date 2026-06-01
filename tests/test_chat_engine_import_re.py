"""Test that chat_engine.py imports 're' module (used on MiniMax path)."""
import ast
import inspect
from app.services import chat_engine as ce_module


def test_chat_engine_imports_re():
    """Lines 441 and 581 call re.sub — 're' must be imported."""
    source = inspect.getsource(ce_module)
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
