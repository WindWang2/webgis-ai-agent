"""#1393: vectorize must use effective (reprojected) raster transform."""
from __future__ import annotations

import ast
from pathlib import Path


def test_vectorize_and_publish_accepts_source_path_param():
    src = Path("app/services/modelops/engine.py").read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_vectorize_and_publish":
            arg_names = [a.arg for a in node.args.args] + [
                a.arg for a in (node.args.kwonlyargs or [])
            ]
            assert "source_path" in arg_names, arg_names
            found = True
    assert found


def test_call_site_passes_source_path():
    """The segmentation path must pass the effective source_path into vectorize."""
    src = Path("app/services/modelops/engine.py").read_text()
    # Narrow window around the vectorize_classes call
    idx = src.find("if request.vectorize_classes:")
    assert idx > 0
    window = src[idx: idx + 500]
    assert "source_path=source_path" in window
    assert "request.source_uri" not in window.split("_vectorize_and_publish")[1][:200]


def test_vectorize_body_opens_source_path_not_only_request_uri():
    src = Path("app/services/modelops/engine.py").read_text()
    start = src.find("def _vectorize_and_publish(")
    end = src.find("\n    def _publish_raster(", start)
    body = src[start:end]
    assert "raster_uri = str(source_path)" in body
    assert "RasterReader.open(raster_uri)" in body
    assert "RasterReader.open(str(request.source_uri))" not in body
