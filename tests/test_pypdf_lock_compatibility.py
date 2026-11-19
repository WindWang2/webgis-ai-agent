"""The PDF lock must satisfy both installation surfaces without lowering the floor."""
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def _pypdf_requirement(filename: str) -> Requirement:
    line = next(
        line.split("#", 1)[0].strip()
        for line in (ROOT / filename).read_text().splitlines()
        if line.startswith("pypdf")
    )
    return Requirement(line)


@pytest.mark.parametrize("filename", ["requirements.txt", "requirements-dev.txt"])
def test_pdf_lock_satisfies_declared_security_and_runtime_floor(filename):
    requirement = _pypdf_requirement(filename)
    locked = next(iter(_pypdf_requirement("requirements.lock").specifier)).version
    assert Version(locked) in requirement.specifier
    assert Version("7.0.1") in requirement.specifier
    assert Version("6.21.4") not in requirement.specifier
