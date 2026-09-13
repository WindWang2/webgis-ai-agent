"""W6 出版与交付测试（V11，ADR-0166）。

批量导出队列（串行/重试/断点续传/fail-soft）+ 可访问性清单（100% 元素）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.accessibility_manifest import build_accessibility_manifest  # noqa: E402
from app.services.export_batch_queue import (  # noqa: E402
    MAX_BATCH_JOBS,
    ExportBatchQueue,
    ExportJob,
)


# ── W6.5 批量导出队列 ───────────────────────────────────────────────────

class _Flaky:
    """前 n 次失败后成功的桩。"""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"boom #{self.calls}")
        return f"ok-after-{self.calls}"


def test_batch_serial_retry_and_soft_failure() -> None:
    queue = ExportBatchQueue(max_retries=2)
    flaky = _Flaky(fail_times=2)
    calls: list = []
    jobs = [
        ExportJob(id="a", run=lambda: calls.append("a") or "A"),
        ExportJob(id="b", run=flaky),                       # 第 3 次成功
        ExportJob(id="c", run=lambda: (_ for _ in ()).throw(RuntimeError("always"))),
        ExportJob(id="d", run=lambda: calls.append("d") or "D"),
    ]
    out = queue.run_batch(jobs)
    by_id = {r.job_id: r for r in out.results}
    assert by_id["a"].status == "ok" and by_id["a"].attempts == 1
    assert by_id["b"].status == "ok" and by_id["b"].attempts == 3  # 2 次重试后成功
    assert by_id["c"].status == "failed" and by_id["c"].attempts == 3
    assert by_id["c"].error.startswith("RuntimeError")
    # fail-soft：失败不中断批次 —— d 仍被执行
    assert by_id["d"].status == "ok"
    assert calls == ["a", "d"]
    # 串行纪律：批内序 = 输入序（执行顺序同）
    assert [r.job_id for r in out.results] == ["a", "b", "c", "d"]


def test_batch_resume_skips_completed() -> None:
    queue = ExportBatchQueue()
    ran: list = []
    jobs = [
        ExportJob(id="a", run=lambda: ran.append("a") or 1),
        ExportJob(id="b", run=lambda: ran.append("b") or 2),
    ]
    first = queue.run_batch(jobs)
    assert ran == ["a", "b"]
    # 断点续传：completed 集内的 job 跳过（不重复重活）
    second = queue.run_batch(jobs, resume_state=first.resume_state)
    assert ran == ["a", "b"]  # 未再执行
    assert all(r.skipped for r in second.results)
    assert second.resume_state["completed_ids"] == first.resume_state["completed_ids"]


def test_batch_guards() -> None:
    queue = ExportBatchQueue()
    with pytest.raises(ValueError):
        ExportJob(id="", run=lambda: 1)
    with pytest.raises(ValueError):
        queue.run_batch([
            ExportJob(id="x", run=lambda: 1),
            ExportJob(id="x", run=lambda: 1),
        ])
    with pytest.raises(ValueError):
        queue.run_batch([
            ExportJob(id=f"j{i}", run=lambda: 1)
            for i in range(MAX_BATCH_JOBS + 1)
        ])
    assert ExportBatchQueue.max_concurrency == 1  # 并发恒 1（资源纪律）


# ── W6.6 可访问性清单 ───────────────────────────────────────────────────

_SPEC = {
    "layout": {"title": "成都学校分布图"},
    "view": {"projection": "EPSG:3857"},
    "sources": [
        {"name": "学校 POI", "attribution": "© 教育局", "type": "geojson"},
    ],
    "layers": [
        {"id": "schools", "type": "circle", "legend": {"title": "学校分布"},
         "fields": {"color": "pop"}},
        {"id": "boundary", "type": "fill", "fields": {"label": "name"}},
    ],
}


def test_accessibility_manifest_complete() -> None:
    m = build_accessibility_manifest(_SPEC, palette="Viridis", palette_context="cvd_deuteranopia")
    assert m["complete"] is True and m["missing"] == []
    assert m["altText"].startswith("成都学校分布图")
    assert m["layerLabels"] == ["学校分布", "boundary（name）"]
    assert m["dataSources"][0]["name"] == "学校 POI"
    assert m["projection"] == "EPSG:3857"
    # 色盲声明来自 context_matrix 实测（同源事实）
    assert m["colorblindSafety"]["checked"] is True
    assert m["colorblindSafety"]["separable"] is True  # Viridis 在 CVD 下通过


def test_accessibility_manifest_honest_failures() -> None:
    """不达标的色带如实披露（不静默放行）；缺元素如实 missing。"""
    m = build_accessibility_manifest(
        _SPEC, palette="Pastel1", palette_context="cvd_deuteranopia")
    assert m["colorblindSafety"]["separable"] is False
    assert "未通过可分辨校验" in m["colorblindSafety"]["declaration"]

    sparse = build_accessibility_manifest({"layout": {}, "layers": []})
    assert sparse["complete"] is False
    assert set(sparse["missing"]) >= {"layerLabels", "dataSources"}
    assert sparse["altText"]  # 标题兜底仍在（altText 不因其它缺失而空）


def test_accessibility_manifest_deterministic() -> None:
    a = build_accessibility_manifest(_SPEC, palette="Viridis")
    b = build_accessibility_manifest(_SPEC, palette="Viridis")
    assert a == b
