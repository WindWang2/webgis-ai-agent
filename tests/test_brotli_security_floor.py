"""Keep the CVE-2026-111497 decoder fix on both dependency surfaces."""
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from scripts.check_dependency_bounds import (
    parse_lock,
    parse_pyproject_dependencies,
    parse_requirements,
)

ROOT = Path(__file__).resolve().parents[1]


def test_brotli_floor_rejects_vulnerable_decoder_and_lock_is_patched():
    declarations = (
        parse_requirements(ROOT / "requirements.txt"),
        parse_pyproject_dependencies(ROOT / "pyproject.toml"),
    )
    for dependencies in declarations:
        spec = Requirement("brotli" + dependencies["brotli"]).specifier
        assert Version("1.2.0") not in spec
        assert Version("1.2.1") in spec
    assert Version(parse_lock(ROOT / "requirements.lock")["brotli"]) >= Version("1.2.1")


def test_patched_brotli_decodes_http_responses():
    brotli = pytest.importorskip("brotli")
    import httpx

    payload = b'{"type":"FeatureCollection","features":[]}'
    compressed = brotli.compress(payload)
    response = httpx.Response(200, headers={"Content-Encoding": "br"}, content=compressed)
    assert Version(brotli.__version__) >= Version("1.2.1")
    assert response.content == payload
    assert response.json() == {"type": "FeatureCollection", "features": []}
