"""#687: MapSpec 提交面解耦——inline 门、指纹 no-op、checkpoint 保留上限。"""
import asyncio

import pytest

import app.services.mapspec.store as store_mod
from app.services.mapspec.store import MapSpecStore
from app.services.mapspec_source import store_data, INLINE_FEATURE_LIMIT
from app.services.mapspec import checkpoint as ckpt_mod


class _SDM687:
    """最小 session_data 替身：set/get_map_state + 指纹定向字段。"""

    def __init__(self):
        self.state = {}
        self.fps = {}
        self.set_calls = 0

    async def set_map_state(self, sid, key, value, seq=None):
        self.set_calls += 1
        self.state[key] = value
        return True

    async def get_map_state(self, sid):
        return dict(self.state)

    async def get_map_spec_fingerprint(self, sid):
        return self.fps.get(sid)

    async def set_map_spec_fingerprint(self, sid, fp):
        self.fps[sid] = fp


@pytest.fixture()
def hermetic_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "BASE_STORAGE_DIR", tmp_path)
    sdm = _SDM687()
    monkeypatch.setattr(store_mod, "session_data_manager", sdm)
    return MapSpecStore(), sdm


def _spec(tag="a"):
    return {
        "version": "1.0",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {"s1": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": []}}},
        "layers": [{"id": f"l-{tag}", "type": "circle", "source": "s1"}],
    }


def _rev_count(store: MapSpecStore, sid: str) -> int:
    rev_dir = store.get_session_dir(sid) / "revisions"
    return len(list(rev_dir.glob("mapspec_rev_*.json"))) if rev_dir.exists() else 0


# ── inline 门 ──────────────────────────────────────────────────────────

def test_inline_feature_gate_rejects_oversized_carrier():
    entry = {}
    big = {"type": "FeatureCollection", "features": [{"type": "Feature"} for _ in range(INLINE_FEATURE_LIMIT + 1)]}
    with pytest.raises(ValueError, match="ref: source"):
        store_data(entry, big)
    assert "inlineData" not in entry


def test_inline_feature_gate_allows_at_limit():
    entry = {}
    ok = {"type": "FeatureCollection", "features": [{"type": "Feature"} for _ in range(INLINE_FEATURE_LIMIT)]}
    store_data(entry, ok)
    assert entry.get("inlineData") is ok


# ── 指纹 no-op ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_noop_short_circuits_on_fingerprint(hermetic_store):
    store, sdm = hermetic_store
    sid = "s-687-noop"
    spec = _spec()

    await store.save_mapspec(sid, spec)
    assert _rev_count(store, sid) == 1
    calls_after_first = sdm.set_calls

    # 同 spec 再存：内存指纹命中 → 不落盘、不写 redis
    await store.save_mapspec(sid, spec)
    assert _rev_count(store, sid) == 1, "no-op 不得新增 revision"
    assert sdm.set_calls == calls_after_first, "no-op 不得重写 map_state"

    # 冷启动（新实例，内存指纹丢失）：sidecar + redis 指纹命中 → 仍 no-op
    store2 = MapSpecStore()
    await store2.save_mapspec(sid, spec)
    assert _rev_count(store, sid) == 1
    assert sdm.set_calls == calls_after_first


@pytest.mark.asyncio
async def test_changed_spec_persists_and_updates_fingerprint(hermetic_store):
    store, sdm = hermetic_store
    sid = "s-687-change"
    await store.save_mapspec(sid, _spec("a"))
    await store.save_mapspec(sid, _spec("b"))
    assert _rev_count(store, sid) == 2, "内容变更必须落新 revision"
    assert sdm.fps.get(sid), "落盘后必须记录指纹"

    # 指纹随内容更新：旧 spec 不再 no-op（会真实落盘）
    await store.save_mapspec(sid, _spec("a"))
    assert _rev_count(store, sid) == 3


# ── checkpoint 保留上限 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_checkpoint_retention_bounded(tmp_path):
    session_dir = tmp_path / "ckpt-session"
    session_dir.mkdir(parents=True)

    async def fake_sdm_get(sid, ref):
        return None

    cap = ckpt_mod.MAPSPEC_CKPT_RETENTION
    for i in range(cap + 5):
        spec = _spec(f"v{i}")
        res = await ckpt_mod.snapshot(spec, session_dir, fake_sdm_get)
        assert res["success"], res

    root = session_dir / "checkpoints"
    auto_dirs = [d for d in root.iterdir() if d.is_dir() and ckpt_mod._AUTO_CKPT_RE.match(d.name)]
    assert len(auto_dirs) <= cap, f"自动 checkpoint 目录 {len(auto_dirs)} 超过上限 {cap}"

    # manifest 不得指向被裁掉的目录
    manifest = await asyncio.to_thread(ckpt_mod._load_manifest, session_dir)
    for _h, cid in manifest.get("entries", {}).items():
        assert (root / cid).exists(), f"manifest 指向已裁剪目录 {cid}"
