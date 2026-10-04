"""Review S1: MemorySessionStore.get_map_state deep-copies OFF the loop, so it
must copy a loop-side snapshot — never the live dict the loop keeps mutating —
and layer updates must be copy-on-write."""
import asyncio

from app.services import session_data as sd


def test_get_map_state_copies_snapshot_not_live_dict(monkeypatch):
    store = sd.MemorySessionStore()

    async def run():
        await store.set_map_state("s", "layers", [{"id": "a", "opacity": 1}])
        live = store._map_state["s"]
        real_to_thread = asyncio.to_thread

        async def racing_to_thread(fn, *args, **kw):
            # loop-side writers interleave with the worker-thread deepcopy
            live["_viewport_seq"] = 7
            live["layers"][0]["opacity"] = 0.1
            return await real_to_thread(fn, *args, **kw)

        monkeypatch.setattr(sd.asyncio, "to_thread", racing_to_thread)
        return await store.get_map_state("s")

    snap = asyncio.run(run())
    assert "_viewport_seq" not in snap
    assert snap["layers"][0]["opacity"] == 1


def test_layer_updates_are_copy_on_write():
    store = sd.MemorySessionStore()

    async def run():
        await store.set_map_state("s", "layers", [{"id": "a", "opacity": 1}])
        old_layer = store._map_state["s"]["layers"][0]
        await store.update_layer_in_state("s", "a", {"opacity": 0.5})
        await store.commit_mapspec_state("s", {}, ("upsert", "a", {"visible": False}))
        return old_layer, store._map_state["s"]["layers"][0]

    old_layer, new_layer = asyncio.run(run())
    assert old_layer == {"id": "a", "opacity": 1}
    assert new_layer == {"id": "a", "opacity": 0.5, "visible": False}
