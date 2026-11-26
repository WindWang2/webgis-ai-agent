"""Human-readable GIS situation inspector rendering regressions."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import scripts.situation_inspect as inspector


def _stub_inspect(monkeypatch, statuses):
    facts = [
        SimpleNamespace(
            status=status,
            source=f"source-{index}",
            value=index if status == "known" else None,
            revision=1,
            observed_at=None,
            ref=None,
        )
        for index, status in enumerate(statuses)
    ]
    situation = SimpleNamespace(
        identity=SimpleNamespace(
            revision=SimpleNamespace(
                mutation_revision=1,
                observation_sequence=1,
                interaction_sequence=1,
            ),
            model_dump=lambda mode: {"schema_version": 1},
        ),
        evidence=SimpleNamespace(
            omitted=[],
            sources_ok=1,
            sources_unavailable=[],
        ),
        iter_facts=lambda: iter([
            ("test", f"fact-{index}", fact)
            for index, fact in enumerate(facts)
        ]),
    )
    monkeypatch.setattr(inspector, "compile_situation", AsyncMock(return_value=situation))
    monkeypatch.setattr(inspector, "load_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(
        inspector,
        "render_situation_for_context",
        lambda situation: SimpleNamespace(
            byte_len=10,
            byte_cap=100,
            truncated=False,
            text="projection",
        ),
    )
    monkeypatch.setattr(inspector, "check_consistency", lambda situation: [])


@pytest.mark.asyncio
async def test_human_summary_does_not_count_unavailable_as_known(monkeypatch, capsys):
    _stub_inspect(monkeypatch, ["known", "stale", "unknown", "unavailable"])

    assert await inspector._inspect("session-1", as_json=False) == 0

    assert "facts: 4 total, known=1, stale=1, unknown=1" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_human_rendering_tolerates_future_fact_status(monkeypatch, capsys):
    _stub_inspect(monkeypatch, ["deprecated"])

    assert await inspector._inspect("session-1", as_json=False) == 0

    assert "[deprecated] test.fact-0 <- source-0" in capsys.readouterr().out
