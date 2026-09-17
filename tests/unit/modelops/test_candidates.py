"""MaskCandidateSet 契约测试（Platform 11 / WP-C）。

oracle：手造候选/分数，直接断言裁决规则（argmax、平分取小、index 越界
typed 拒绝），不调用被测代码生成期望。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.modelops.candidates import (
    MAX_MASK_CANDIDATES,
    CANDIDATE_SOURCE_HEURISTIC,
    CANDIDATE_SOURCE_MODEL,
    MaskCandidate,
    MaskCandidateSet,
    candidate_set_from_arrays,
)
from app.lib.modelops.errors import PromptArtifactError


def _cand(index, score, source=CANDIDATE_SOURCE_MODEL):
    return MaskCandidate(index=index, score=score, source=source)


def test_best_selection_resolves_argmax_with_tie_to_smaller_index():
    cset = MaskCandidateSet(candidates=(_cand(0, 0.5), _cand(1, 0.9), _cand(2, 0.1)))
    assert cset.resolve_selected() == 1
    tied = MaskCandidateSet(candidates=(_cand(0, 0.7), _cand(1, 0.7)))
    assert tied.resolve_selected() == 0


def test_index_selection_resolves_and_validates_range():
    cset = MaskCandidateSet(
        candidates=(_cand(0, 0.9), _cand(1, 0.2)),
        selection="index", selected_index=1,
    )
    assert cset.resolve_selected() == 1
    with pytest.raises(PromptArtifactError, match="out of range"):
        MaskCandidateSet(
            candidates=(_cand(0, 0.9),), selection="index", selected_index=3
        )
    with pytest.raises(PromptArtifactError, match="requires selected_candidate"):
        MaskCandidateSet(candidates=(_cand(0, 0.9),), selection="index")


def test_contract_validation_matrix():
    with pytest.raises(PromptArtifactError, match="at least one"):
        MaskCandidateSet(candidates=())
    with pytest.raises(PromptArtifactError, match="too many"):
        MaskCandidateSet(
            candidates=tuple(_cand(i, 0.5) for i in range(MAX_MASK_CANDIDATES + 1))
        )
    with pytest.raises(PromptArtifactError, match="duplicate"):
        MaskCandidateSet(candidates=(_cand(0, 0.5), _cand(0, 0.6)))
    with pytest.raises(PromptArtifactError, match="dense"):
        MaskCandidateSet(candidates=(_cand(0, 0.5), _cand(2, 0.6)))
    with pytest.raises(PromptArtifactError, match="unknown candidate selection"):
        MaskCandidateSet(candidates=(_cand(0, 0.5),), selection="random")
    with pytest.raises(PromptArtifactError, match=r"\[0,1\]"):
        MaskCandidate(index=0, score=1.5)
    with pytest.raises(PromptArtifactError, match="finite"):
        MaskCandidate(index=0, score=float("nan"))
    with pytest.raises(PromptArtifactError, match="candidate source"):
        MaskCandidate(index=0, score=0.5, source="oracle")


def test_candidate_set_from_arrays_alignment_and_sources():
    cset = candidate_set_from_arrays(
        np.array([0.2, 0.8, 0.5], dtype=np.float32),
        (CANDIDATE_SOURCE_HEURISTIC,) * 3,
    )
    assert cset.resolve_selected() == 1
    assert cset.as_dict()["candidates"][0]["source"] == CANDIDATE_SOURCE_HEURISTIC
    with pytest.raises(PromptArtifactError, match="length"):
        candidate_set_from_arrays(np.array([0.1, 0.2]), ("model",))
    with pytest.raises(PromptArtifactError, match="empty"):
        candidate_set_from_arrays(np.array([], dtype=np.float32))


def test_as_dict_shape():
    cset = MaskCandidateSet(
        candidates=(_cand(0, 0.4), _cand(1, 0.6)),
        selection="index", selected_index=0,
    )
    payload = cset.as_dict()
    assert payload["selection"] == "index"
    assert payload["selected_index"] == 0
    assert [c["index"] for c in payload["candidates"]] == [0, 1]
    assert all(set(c) == {"index", "score", "source", "label"} for c in payload["candidates"])
