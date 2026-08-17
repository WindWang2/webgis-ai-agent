"""#563 regression: manage.py's module-level ``rich`` imports must resolve.

manage.py (the ops CLI entrypoint: create-admin / check / dev) imports
``rich.console/table/panel`` at module scope. `rich` was missing from every
requirements manifest, so a clean ``pip install -r requirements-dev.txt``
crashed with ModuleNotFoundError on EVERY manage.py command. The dependency
is now declared in requirements.txt (pulled in by requirements-dev.txt
via ``-r``); these tests pin the contract so the drift cannot silently
return (CI never executed manage.py before, so nothing caught it).
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_manage_py_help_runs_with_declared_deps():
    """python manage.py --help must exit 0 on a fresh install: the module-level
    rich imports resolve because rich is declared in requirements.txt."""
    result = subprocess.run(
        [sys.executable, "manage.py", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"manage.py --help failed (rc={result.returncode}): "
        f"stdout={result.stdout[-800:]!r} stderr={result.stderr[-800:]!r}"
    )
    assert "usage" in result.stdout.lower()


def test_rich_declared_in_requirements():
    """The dependency manifest must declare rich so a clean install resolves
    manage.py's top-level imports."""
    reqs = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^rich\s*[>=<]", reqs, re.M), (
        "requirements.txt does not declare the rich dependency that "
        "manage.py imports at module level"
    )