"""Test: RAG embedding must not block the async event loop."""
import ast
import inspect
from app.services import rag_service as rag_module


def test_embed_encode_runs_in_executor():
    """embed_model.encode must be called via run_in_executor, not directly."""
    source = inspect.getsource(rag_module.add_document)
    tree = ast.parse(source)

    # Walk the AST looking for embed_model.encode calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            # Direct call: embed_model.encode(...)
            if isinstance(func, ast.Attribute) and func.attr == "encode":
                # Check if this call is inside a run_in_executor
                # Walk up the parent chain — but AST doesn't store parents
                # Instead check if encode appears in source with run_in_executor nearby
                line_start = node.lineno
                source_lines = source.split('\n')
                # Look in nearby lines for run_in_executor
                nearby = '\n'.join(source_lines[max(0, line_start-5):line_start+3])
                assert "run_in_executor" in nearby, (
                    f"embed_model.encode() at line {line_start} is called directly, "
                    "not via run_in_executor — will block the async event loop"
                )
                return

    # If no encode call found, that's fine (maybe already refactored)
