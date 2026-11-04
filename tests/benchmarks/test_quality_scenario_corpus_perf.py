"""ADR-0245: preserve the corpus 45s budgets in the required no-coverage lane.

Correctness still replays every case in tests/quality under coverage. This
additional gate uses the same real replay, case-count guards and failures.
"""
from __future__ import annotations

import pytest

from tests.quality.test_quality_scenario_corpus import replay_quality_slice

pytestmark = pytest.mark.perf


@pytest.mark.parametrize("prefix", ["basemap_", "science_", "data_"])
@pytest.mark.asyncio
async def test_quality_corpus_slice_budget(prefix: str) -> None:
    elapsed, failed = await replay_quality_slice(prefix)
    assert failed == [], f"{len(failed)} failures: {failed[:5]}"
    assert elapsed < 45, f"slice {prefix} took {elapsed:.1f}s (budget 45s)"
