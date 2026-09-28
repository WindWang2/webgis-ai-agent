"""Layer-boundary guard: app/core must not import app/services (#1542).

#1542: ``app/core/auth.py::verify_session_owner`` reached into
``AsyncHistoryService`` via a function-body import — core → services layer
inversion inside the mypy-ratchet zone (``files=app/core``). The guard moved
to ``app/services/auth_history_bridge.py``; this test pins the boundary so
the inversion cannot silently return.

AST-based (mirrors tests/test_api_auth_required.py): prose/string mentions of
``app.services...`` paths (e.g. the call-site notes in
app/core/network_dependency.py) are fine — only real import statements count.
"""
import ast
from pathlib import Path

CORE_DIR = Path("app/core")


def _imported_modules(tree: ast.AST) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                mods.append(node.module)
            elif node.module and node.level > 0:
                # relative import inside app/core — resolve prefix contextually
                mods.append(f"<relative>{'.' * node.level}{node.module}")
    return mods


def test_core_never_imports_services():
    offenders: list[str] = []
    scanned = 0
    for path in sorted(CORE_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        scanned += 1
        mods = _imported_modules(ast.parse(path.read_text(encoding="utf-8")))
        offenders.extend(
            f"{path.as_posix()}: {m}" for m in mods if m.startswith("app.services")
        )
    assert scanned > 0, "scanner found no app/core files — glob broke"
    assert not offenders, (
        "app/core must not import app/services (layer inversion, #1542); "
        f"move the consumer to the services side: {offenders}"
    )


def test_session_ownership_guard_lives_in_services_bridge():
    """SEC-08 pair importable from the bridge, fully gone from app.core.auth."""
    from app.core import auth as core_auth
    from app.services import auth_history_bridge as bridge

    assert callable(bridge.verify_session_owner)
    assert callable(bridge.require_owned_session)
    # No back-compat alias in core: a lazy re-export there would still be
    # core → services, which is exactly what #1542 forbids.
    assert not hasattr(core_auth, "verify_session_owner")
    assert not hasattr(core_auth, "require_owned_session")
