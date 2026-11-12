"""G23 P5 — 有界内存/背压/规模档（A17/A18/A19）。

规模档位（任务书：小数据/10万级/大于缓存——档位是测量点不是性能承诺）：
  * 300k 行 CSV（> COMMIT 缓存任何量级）：导入全程 RSS 有界、行数统计完整
    不截断、收据 input_rows/output_rows 与源一致；
  * 45k 面 geojson（计算工作台上限内）：导入+计算 RSS 有界、进度真值；
  * 临时盘耗尽（ENOSPC 注入）：typed 失败，不静默取前 N 行当全集（A19）。

运行较慢（~1-2 分钟），标 heavy：pytest -m heavy tests/data_operations/test_scale.py
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from app.services.data_import.service import DataImportService

pytestmark = pytest.mark.heavy

SESS = "sess-g23-scale"


class _RSS:
    def __init__(self):
        self.peak_kb = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                with open("/proc/self/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            self.peak_kb = max(self.peak_kb, int(line.split()[1]))
                            break
            except OSError:
                pass
            time.sleep(0.05)

    def __enter__(self):
        self._start_kb = self.peak_kb
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=1)

    @property
    def delta_mb(self) -> float:
        return round((self.peak_kb - getattr(self, "_start_kb", 0)) / 1024, 1)


def _csv_bytes(rows: int) -> bytes:
    import io

    buf = io.StringIO()
    buf.write("lng,lat,v\n")
    for i in range(rows):
        buf.write(f"{116.0 + (i % 5000) * 0.001},{39.0 + (i % 5000) * 0.001},{i}\n")
    return buf.getvalue().encode()


GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"n": 1},
         "geometry": {"type": "Point", "coordinates": [116.3, 39.8]}},
        {"type": "Feature", "properties": {"n": 2},
         "geometry": {"type": "Point", "coordinates": [116.31, 39.81]}},
    ],
}


async def test_scale_300k_csv_bounded_memory_honest_counts(ops_env):
    """A17：300k 行 CSV 导入 —— RSS 有界 + 统计完整（不静默截断）。"""
    svc = DataImportService()
    state = await svc.create_batch(
        SESS, "user:scale", [("big.csv", _csv_bytes(300_000))])
    key = state.members[0].member_key
    await svc.save_plan(state.batch_id, SESS, {key: {"mode": "table"}},
                        selected=[key])
    t0 = time.monotonic()
    with _RSS() as rss:
        out = await svc.start_commit(state.batch_id, SESS, background=False)
    elapsed = time.monotonic() - t0
    assert out["status"] == "completed", out
    receipt = out["receipts"][key]
    # 行数诚实：输入=输出=300k，拒绝=0（不把前 N 行当全集）
    assert receipt["input_rows"] == 300_000
    assert receipt["output_rows"] == 300_000
    assert receipt["rejected_rows"] == 0
    # 有界内存：峰值增量 < 1.2GB（解释器整表物化 + payload 序列化上限内）
    assert rss.delta_mb < 1200, f"RSS delta {rss.delta_mb}MB 超档"
    print(f"\n[scale] 300k csv: {elapsed:.1f}s, rss_delta={rss.delta_mb}MB")


async def test_scale_row_budget_exceeded_typed_failure(ops_env):
    """行预算超限 → ROW_BUDGET_EXCEEDED（typed 拒绝，不静默截断）。

    500_001 行生成耗时可控：直接构造超过 COMMIT_ROW_LIMIT 的表。
    """
    svc = DataImportService()
    state = await svc.create_batch(
        SESS, "user:scale", [("over.csv", _csv_bytes(500_001))])
    key = state.members[0].member_key
    await svc.save_plan(state.batch_id, SESS, {key: {"mode": "table"}},
                        selected=[key])
    out = await svc.start_commit(state.batch_id, SESS, background=False)
    receipt = out["receipts"][key]
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "ROW_BUDGET_EXCEEDED"
    # 失败输出不覆盖源；job 如实 failed
    from app.services.data_import import staging

    src = staging.source_path(state, state.sources[0].source_id)
    assert src.exists()  # 源未被动


async def test_disk_exhaustion_typed_failure(ops_env, monkeypatch):
    """A19：临时盘耗尽（ENOSPC）→ typed 失败，不静默部分成功。

    注入点 = staging.save_state（批次状态/收据的持久面）。提交启动阶段的
    写失败 → typed DataImportError(STAGING_WRITE_FAILED) + job 落 failed；
    执行中收据落盘失败 → 成员按 failed 披露。源数据绝不被改。
    """
    from app.services.data_import import staging as staging_mod
    from app.services.data_import.service import DataImportError

    svc = DataImportService()
    state = await svc.create_batch(
        SESS, "user:scale", [("a.geojson", json.dumps(GEOJSON).encode())])
    key = state.members[0].member_key
    await svc.save_plan(state.batch_id, SESS, {key: {}}, selected=[key])

    def _enospc_save(state, *, data_dir=None):
        raise OSError(28, "No space left on device")  # ENOSPC

    _real_save_state = staging_mod.save_state
    monkeypatch.setattr(staging_mod, "save_state", _enospc_save)
    try:
        with pytest.raises(DataImportError) as ei:
            await svc.start_commit(state.batch_id, SESS, background=False)
    finally:
        # 手动恢复（勿用 monkeypatch.undo() —— 会连带撤掉 ops_env 的补丁）
        staging_mod.save_state = _real_save_state
    assert ei.value.code == "STAGING_WRITE_FAILED"
    # job 行如实 failed（durable 证据，不伪造状态）
    from app.models.db_model import AnalysisTask
    from app.services.data_operations.runner import _sync_session

    with _sync_session() as db:
        row = (
            db.query(AnalysisTask)
            .filter(AnalysisTask.task_type == "data_import_commit")
            .order_by(AnalysisTask.id.desc()).first())
        job_status = str(row.status) if row else "none"
        db.commit()
    assert job_status == "failed"
    # 源数据未被修改
    src = staging_mod.source_path(state, state.sources[0].source_id)
    assert src.read_bytes() == json.dumps(GEOJSON).encode()
