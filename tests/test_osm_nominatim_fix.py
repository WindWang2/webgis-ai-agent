"""Test that Nominatim fallback uses correct SSL context function name."""
import pytest
import ast
import inspect
from app.tools import osm as osm_module


def test_nominatim_uses_correct_ssl_function_name():
    """_nominatim_search_poi must call get_ssl_context (not _get_ssl_context)."""
    source = inspect.getsource(osm_module._nominatim_search_poi)
    tree = ast.parse(source)

    ssl_refs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and "ssl_context" in node.id:
            ssl_refs.append(node.id)
        if isinstance(node, ast.Attribute) and "ssl_context" in node.attr:
            ssl_refs.append(node.attr)

    assert "_get_ssl_context" not in ssl_refs, (
        f"Found typo '_get_ssl_context' — should be 'get_ssl_context'. Refs: {ssl_refs}"
    )
