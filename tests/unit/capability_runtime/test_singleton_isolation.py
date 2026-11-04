"""Projection-hook tests must not replace later planners' runtime manifest."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from unittest.mock import MagicMock

import pytest

from tests.unit.capability_runtime.conftest import _reset_runtime_singletons


@pytest.mark.parametrize("warm_cache", [False, True], ids=["cold", "warm"])
@pytest.mark.parametrize("exceptional_exit", [False, True], ids=["normal", "exception"])
def test_projection_manifest_is_restored(warm_cache, exceptional_exit, monkeypatch):
    import app.extensions_platform.host as host_mod
    from app.extensions_platform.refresh import make_projection_refresher
    from app.lib.gis import runtime_manifest as rm
    from app.services.gis_harness.planner import capability_tool_map

    original = rm.compile_runtime_manifest() if warm_cache else None
    monkeypatch.setattr(rm, "_cached_manifest", original)
    monkeypatch.setattr(host_mod, "get_extension_host", lambda: None)

    # Exercise the owning fixture's actual setup/teardown, including its failure
    # path. A surrounding pytest fixture cannot expose its own post-yield state.
    isolated_runtime = contextmanager(_reset_runtime_singletons.__wrapped__)
    outcome = (
        pytest.raises(RuntimeError, match="injected test failure")
        if exceptional_exit else nullcontext()
    )
    with outcome:
        with pytest.MonkeyPatch.context() as fixture_patches:
            with isolated_runtime(fixture_patches):
                # Same producer as the certification wiring test: the mock
                # permits schema refresh but exposes no registered tools.
                make_projection_refresher(MagicMock())("acme.pack", "deactivate")
                assert rm._cached_manifest is not original
                assert capability_tool_map() == {}
                if exceptional_exit:
                    raise RuntimeError("injected test failure")

    # Preserve both a warm snapshot's identity and an uninitialized cache. Do
    # not hide the leak by compiling or clearing unrelated runtime singletons.
    assert rm._cached_manifest is original

    from app.services.gis_harness.planner import CAPABILITY_TOOLS

    for capability in ("poi_query", "admin_aggregation", "kde_density"):
        assert capability in CAPABILITY_TOOLS
