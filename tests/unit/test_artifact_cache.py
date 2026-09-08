"""Unit tests for content-addressed artifact cache (app/lib/artifact_cache.py)."""
import os
import tempfile
import pytest

from app.lib.artifact_cache import (
    make_artifact_key,
    get_artifact,
    publish_artifact,
    clear_artifact_cache,
    _source_identity,
)


@pytest.fixture(autouse=True)
def clean_cache():
    clear_artifact_cache()
    yield
    clear_artifact_cache()


def test_source_identity():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"test data 123")
        fname = f.name

    try:
        ident1 = _source_identity(fname)
        assert fname in ident1
        assert "13" in ident1  # size 13 bytes
    finally:
        os.unlink(fname)


def test_source_identity_uses_mtime_ns_precision(tmp_path):
    """round-1 review MINOR：identity 用 st_mtime_ns —— 同一秒内的
    同尺寸改写也产生新 identity（int(st.st_mtime) 会截断亚秒精度）。"""
    import os
    import time

    p = tmp_path / "src.tif"
    p.write_bytes(b"v1")
    ident1 = _source_identity(str(p))
    # 同尺寸、同秒内（sub-second）改写
    time.sleep(0.01)
    p.write_bytes(b"v2")
    st = p.stat()
    ident2 = _source_identity(str(p))
    assert ident1 != ident2
    # identity 里的 mtime 是纳秒精度整数（无小数点截断痕迹）
    os.utime(p, ns=(st.st_mtime_ns, st.st_mtime_ns))
    assert _source_identity(str(p)) == ident2  # 纳秒级稳定


def test_make_artifact_key_deterministic():
    key1 = make_artifact_key("file.tif", "resample", {"res": 10.0})
    key2 = make_artifact_key("file.tif", "resample", {"res": 10.0})
    key3 = make_artifact_key("file.tif", "resample", {"res": 20.0})

    assert key1 == key2
    assert key1 != key3
    assert len(key1) == 16


def test_publish_and_get_artifact():
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as src_f:
        src_f.write(b"SOURCE_RASTER_DATA")
        src_path = src_f.name

    key = make_artifact_key(src_path, "test_op", {"param": 1})
    assert get_artifact(key) is None

    def _compute():
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as out_f:
            out_f.write(b"PROCESSED_RASTER_OUTPUT")
            return out_f.name

    published_path = publish_artifact(key, src_path, _compute)
    assert os.path.exists(published_path)
    assert published_path != src_path

    # Second get should hit cache
    hit_path = get_artifact(key)
    assert hit_path == published_path

    # Cleanup temp source file
    os.unlink(src_path)


# ── round-1 review PERF MAJOR-3: advisory byte counter ────────────────────
# Full directory scans must be O(1) per publish burst (lazily initialized
# running counter), not O(publishes); the cap is still enforced by the one
# scan+evict round once the counter crosses it.


def test_chunk_publish_full_scan_count_bounded_and_cap_enforced(monkeypatch):
    from app.lib import artifact_cache as ac

    ac.clear_chunk_cache()
    monkeypatch.setattr(ac, "_chunk_cache_cap", lambda: 10 * 1024)  # 10 KiB

    scans = {"init": 0, "evict": 0}
    real_init, real_evict = ac._scan_dir_total, ac._scan_and_evict

    def counting_init(dir_path, body_path_for):
        scans["init"] += 1
        return real_init(dir_path, body_path_for)

    def counting_evict(*a, **kw):
        scans["evict"] += 1
        return real_evict(*a, **kw)

    monkeypatch.setattr(ac, "_scan_dir_total", counting_init)
    monkeypatch.setattr(ac, "_scan_and_evict", counting_evict)

    payload = b"x" * 256
    try:
        for i in range(20):
            assert ac.publish_chunk(f"{i:016x}", payload, source_path="")
        # 20 publishes → at most ONE full scan (lazy counter init); the
        # per-publish full-scan eviction precondition is gone.
        assert scans["init"] <= 1
        assert scans["evict"] == 0, "20*256B ≤ 10 KiB cap — no eviction scan"

        # Counter crosses the (new) cap → exactly one scan+evict round, and
        # the cap is enforced by it.
        monkeypatch.setattr(ac, "_chunk_cache_cap", lambda: 2 * 1024)
        assert ac.publish_chunk(f"{99:016x}", payload, source_path="")
        assert scans["evict"] == 1
        assert scans["init"] <= 1, "counter known → no re-init scan"
        total = real_init(ac.CHUNK_DIR, ac._chunk_path)
        assert total <= 2 * 1024, "eviction still enforces the cap"
    finally:
        ac.clear_chunk_cache()
