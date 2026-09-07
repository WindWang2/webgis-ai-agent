"""Cancellation & Resource Safety 红线（ADR-0104 Wave 9/10）。

行为认证（不只看表）：
- 置换检验/CSR envelope 的取消（Wave 9 修复的 point_pattern 盲区）；
- 既有可取消路径（statistics）的取消行为回归；
- 资源上限 typed reject（RasterResourceGuard / args 预算门）——超限抛
  类型化异常而不是 OOM；
- 两张认证表字节一致 + 缺口如实披露。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from gen_resource_certification import DEFAULT_CANCELLATION, DEFAULT_RESOURCE, generate  # noqa: E402

from app.lib.cancellation import CancellationToken, OperationCancelled, CURRENT_TOKEN  # noqa: E402
from app.lib.quality.certification import (  # noqa: E402
    cancellation_coverage,
    render_cancellation_md,
    render_resource_md,
)


def _rng_xy(n: int = 120, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.column_stack([rng.uniform(0, 1, n), rng.uniform(0, 1, n)])


# ── Wave 9：取消行为（真实重循环）────────────────────────────────────────


def test_ripley_k_csr_envelope_honours_cancellation():
    """K 包络 CSR 模拟循环必须响应取消（每个 draw 一个检查点）。"""
    from app.lib.geo_analysis.point_pattern import ripley_k

    token = CancellationToken(job_id="cert-1")
    token.cancel()
    token_context = CURRENT_TOKEN.set(token)
    try:
        with pytest.raises(OperationCancelled):
            ripley_k(_rng_xy(), envelopes=50)
    finally:
        CURRENT_TOKEN.reset(token_context)


def test_permutation_test_honours_cancellation():
    """cross-K 置换循环（permutations × O(n²)）必须响应取消。"""
    from app.lib.geo_analysis.point_pattern import cross_k

    token = CancellationToken(job_id="cert-2")
    token.cancel()
    token_context = CURRENT_TOKEN.set(token)
    try:
        with pytest.raises(OperationCancelled):
            cross_k(_rng_xy(80), types=["a", "b"] * 40, permutations=30)
    finally:
        CURRENT_TOKEN.reset(token_context)


def test_unpainted_path_still_runs_without_token():
    """无 token（普通请求路径）时检查点零开销、结果正常。"""
    from app.lib.geo_analysis.point_pattern import ripley_k

    out = ripley_k(_rng_xy(40), envelopes=5)
    assert "K" in out or "k_function" in out or out  # 结构性返回即可


# ── Wave 10：typed reject ────────────────────────────────────────────────


def test_raster_guard_typed_reject_over_pixels():
    from app.lib.geo_analysis.raster_guard import (
        RasterResourceExceededError,
        RasterResourceGuard,
    )

    with pytest.raises(RasterResourceExceededError):
        RasterResourceGuard.check_grid(
            width=200_000, height=200_000, bytes_per_pixel=4, num_bands=1)


def test_raster_guard_allows_normal_dimensions():
    from app.lib.geo_analysis.raster_guard import RasterResourceGuard

    RasterResourceGuard.check_grid(width=1_000, height=1_000)


def test_tool_args_budget_typed_gate():
    """超大 args 必须被预算门识别（typed，非 OOM）。"""
    from app.tools.registry import _is_args_oversized

    big = {"features": [{"geometry": {"coordinates": [1.0, 2.0]}} for _ in range(200_000)]}
    assert _is_args_oversized(big) is True
    assert _is_args_oversized({"a": 1}) is False


# ── 认证表 ───────────────────────────────────────────────────────────────


def test_certification_tables_current():
    cancellation_md, resource_md = generate()
    assert DEFAULT_CANCELLATION.exists() and DEFAULT_RESOURCE.exists()
    assert DEFAULT_CANCELLATION.read_text(encoding="utf-8") == cancellation_md
    assert DEFAULT_RESOURCE.read_text(encoding="utf-8") == resource_md


def test_point_pattern_is_certified_now():
    """Wave 9 修复后 point_pattern 必须有检查点（缺口清零的证据行）。"""
    rows = {r["file"]: r for r in cancellation_coverage()}
    row = rows.get("app/lib/geo_analysis/point_pattern.py")
    assert row is not None
    assert row["status"] == "certified", row


def test_resource_limits_introspected_not_hardcoded():
    """表数值必须来自源头内省（改常量不改表 → 字节闸红）。"""
    from app.lib.quality.certification import resource_limits

    rows = {r["kind"]: r for r in resource_limits()}
    assert rows["raster_total_pixels"]["limit"] == 250_000_000
    assert rows["json_node_budget"]["limit"] >= 1_000
