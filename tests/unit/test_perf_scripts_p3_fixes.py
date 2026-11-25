"""P3 回归：scripts/perf 的编码、临时目录与 tool_call_ok 语义。

对应 review-swarm 任务 P3-scripts_perf-00 的三条发现；每个测试在未修复
代码上必须失败。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

from scripts.perf import measurements
from scripts.perf.pi_vs_chatengine import run_task

REPO = Path(__file__).resolve().parents[2]


# ── calibrate_from_usage.py：read_text/write_text 显式 UTF-8 ─────────────


def test_calibrate_usage_script_is_utf8_safe_under_ascii_locale(
    tmp_path: Path,
) -> None:
    """ASCII locale 下含中文的 快照/建议文件 必须能读能写。

    未修复代码：Path.read_text()/write_text() 走 locale 缺省编码（ASCII），
    读快照即 UnicodeDecodeError（脚本 return 2），或写建议时 UnicodeEncodeError。
    """
    snapshot = {
        "keys": {
            "tool_dispatch:热点分析": {
                "wall_time_s": {"samples": [[1.0, 5.0]] * 8},
            }
        }
    }
    snap_path = tmp_path / "snap.json"
    snap_path.write_text(
        json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    out_path = tmp_path / "suggest.json"
    env = dict(os.environ)
    env.update(
        LC_ALL="C",
        LANG="C",
        PYTHONUTF8="0",
        PYTHONCOERCECLOCALE="0",
        # 只影响 stdio；文件缺省编码仍为 ASCII，从而暴露脚本里的隐含编码。
        PYTHONIOENCODING="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "scripts/perf/calibrate_from_usage.py",
         "--snapshot", str(snap_path), "--report", str(out_path)],
        capture_output=True, text=True, cwd=str(REPO), env=env,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert report["suggestions"][0]["tool_key"] == "tool_dispatch:热点分析"


# ── measurements.py：tmp_root 缺省时的 zarr 临时目录必须清理 ─────────────


def test_cube_window_measurement_removes_its_temp_store(
    monkeypatch, tmp_path: Path
) -> None:
    """tmp_root 缺省时不得遗留 perf-cube-* 目录（zarr 用 fake，无需真实依赖）。"""

    class _FakeArray:
        def __init__(self) -> None:
            self.attrs: dict = {}

    class _FakeGroup:
        def __init__(self) -> None:
            self.attrs: dict = {}

        def create_array(self, name, data=None, chunks=None):
            return _FakeArray()

    fake_zarr = types.ModuleType("zarr")
    setattr(fake_zarr, "open_group", lambda *a, **k: _FakeGroup())
    monkeypatch.setitem(sys.modules, "zarr", fake_zarr)

    fake_cube = types.ModuleType("app.services.lakehouse.cube_store")
    setattr(fake_cube, "read_cube_window",
            lambda *a, **k: {"bands": ["ndvi"]})
    monkeypatch.setitem(
        sys.modules, "app.services.lakehouse.cube_store", fake_cube)

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    measurements.measure_cube_window_p95(iterations=1)
    assert list(tmp_path.iterdir()) == []


# ── pi_vs_chatengine.py：tool_call_ok 不得被 task_complete 洗白 ───────────


class _FakeStreamResponse:
    def __init__(self, lines) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamContext:
    def __init__(self, lines) -> None:
        self._lines = lines

    async def __aenter__(self):
        return _FakeStreamResponse(self._lines)

    async def __aexit__(self, *exc_info):
        return False


class _FakeStreamingClient:
    def __init__(self, lines) -> None:
        self._lines = lines

    def stream(self, *args, **kwargs):
        return _FakeStreamContext(self._lines)


def test_tool_call_ok_false_when_expected_tool_missing_and_completed() -> None:
    """task_complete 不能把「期望 tool_call 却未出现」算成命中。"""
    task = {"id": "expects-tool", "message": "m", "expect_tool": True}
    lines = [
        'data: {"event": "token", "content": "hi"}',
        'data: {"event": "task_complete"}',
    ]
    result = asyncio.run(
        run_task(_FakeStreamingClient(lines), "http://x", task))
    assert result["completed"] is True
    assert result["tool_call"] is False
    assert result["tool_call_ok"] is False
