"""Workflow V6 — retry 错误分类矩阵（P4 补强：retry.py 词表面）。"""
from __future__ import annotations

import pytest

from app.services.workflow_runtime import retry as RT


@pytest.mark.parametrize("code", sorted(RT.RETRYABLE_ERROR_CODES))
def test_retryable_codes_are_retryable(code: str) -> None:
    assert RT.error_retryable(code) is True


@pytest.mark.parametrize("code", sorted(RT.NON_RETRYABLE_ERROR_CODES))
def test_non_retryable_codes_are_final(code: str) -> None:
    assert RT.error_retryable(code) is False


def test_unknown_code_is_fail_closed() -> None:
    # 未知错误码保守不重试：盲目重试非幂等副作用更危险。
    assert RT.error_retryable("SOMETHING_NOVEL") is False
    assert RT.error_retryable("") is False
    assert RT.error_retryable(None) is False  # type: ignore[arg-type]


def test_non_retryable_beats_failure_class() -> None:
    # 词表优先：CANCELLED 即使携带可重试 failure_class 也不得重试。
    retryable_class = sorted(RT._retryable_failure_classes())[0]
    assert RT.error_retryable("CANCELLED", retryable_class) is False


def test_failure_class_whitelist_projection() -> None:
    classes = RT._retryable_failure_classes()
    assert classes, "geocompute 可重试 failure_class 集不应为空"
    sample = sorted(classes)[0]
    assert RT.error_retryable("UNLISTED_CODE", sample) is True
    assert RT.error_retryable("UNLISTED_CODE", "definitely_not_a_class") is False


def test_vocabularies_are_disjoint() -> None:
    assert not (RT.RETRYABLE_ERROR_CODES & RT.NON_RETRYABLE_ERROR_CODES)
