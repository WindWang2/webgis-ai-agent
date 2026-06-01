"""Test: delete_upload must commit DB deletion BEFORE removing files."""
import pytest
import ast
import inspect
from app.api.routes import upload as upload_module


def test_delete_upload_file_cleanup_after_db_context():
    """In delete_upload source, shutil.rmtree must appear AFTER the async with block."""
    source = inspect.getsource(upload_module.delete_upload)
    tree = ast.parse(source)

    # Find the async with block and the rmtree call
    async_with_end = None
    rmtree_line = None

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncWith):
            async_with_end = node.end_lineno
        if isinstance(node, ast.Attribute) and node.attr == "rmtree":
            rmtree_line = node.end_lineno

    assert rmtree_line is not None, "shutil.rmtree not found in delete_upload"
    assert async_with_end is not None, "async with db session not found"

    # rmtree must be outside the async with block (line number > block end)
    assert rmtree_line > async_with_end, (
        f"shutil.rmtree at line {rmtree_line} is INSIDE the DB context "
        f"(ends at line {async_with_end}). Files would be deleted before DB commit."
    )
