"""B-1（review-fix-rereview-20261108）：MemorySessionStore.overwrite 锁纪律。

store() 的既定纪律（同文件注释："Disk and descriptor work must not hold
the process-wide cache lock"）把 O(features) 的 descriptor 计算移到锁外，
计算完再以「条目仍是本对象且 revision 未变」守卫回写。overwrite() 是
plan_mode 每波后重持久化 step_results 的热路径，此前却在**全 store 进程级
锁**的临界区内 await to_thread 跑 descriptor 计算 —— 任意会话的
store/get/get_shared/delete_ref 全部排队停顿。
"""
import asyncio

import pytest

from app.services.session_data import MemorySessionStore


@pytest.mark.asyncio
async def test_overwrite_descriptor_recompute_runs_outside_store_lock(monkeypatch):
    store = MemorySessionStore()
    ref = await store.store("s-b1", {"kind": "plan", "steps": [{"v": 1}]})

    # 在主 loop 上捕获锁对象（B-3：_lock 是 per-loop property，spy 在
    # to_thread 线程里不能再走 property 解析）。
    lock_obj = store._lock

    from app.schemas import ref_descriptor

    observed: list[bool] = []
    orig = ref_descriptor.compute_descriptor

    def spy(ref_id, data):
        observed.append(lock_obj.locked())
        return orig(ref_id, data)

    monkeypatch.setattr(ref_descriptor, "compute_descriptor", spy)

    ok = await store.overwrite("s-b1", ref, {"kind": "plan", "steps": [{"v": 2}]})
    assert ok is True

    # descriptor 计算不得在全 store 锁内进行（store() 同款锁外纪律）。
    assert observed == [False], (
        f"overwrite 在持锁状态下计算 descriptor（observed={observed}）—— "
        "全 store 串行停顿（B-1）"
    )
    # 新 descriptor 已写入且反映新 payload（revision 语义不变）。
    desc = store._descriptors.get("s-b1", {}).get(ref)
    assert desc is not None


@pytest.mark.asyncio
async def test_overwrite_concurrent_same_ref_keeps_latest_descriptor(monkeypatch):
    """锁外计算窗口内的并发覆写：迟到的旧 descriptor 不得覆盖新 revision
    的描述符（store() 同款身份+revision 守卫）。

    master 现状（descriptor 在锁内计算）下本用例以 TimeoutError 红：
    第二次覆写拿不到被第一次覆写持有的全 store 锁。
    """
    import threading

    store = MemorySessionStore()
    ref = await store.store("s-b1", {"kind": "plan", "steps": [{"v": 1}]})
    lock_obj = store._lock

    from app.schemas import ref_descriptor

    orig = ref_descriptor.compute_descriptor
    gate = threading.Event()
    first_compute_started = threading.Event()

    def slow_first(ref_id, data):
        if not first_compute_started.is_set():
            first_compute_started.set()
            # 让第一个（旧 payload 的）descriptor 计算滞留到第二次覆写提交之后。
            gate.wait(timeout=10)
        return orig(ref_id, data)

    monkeypatch.setattr(ref_descriptor, "compute_descriptor", slow_first)

    async def overwrite(payload):
        return await store.overwrite("s-b1", ref, payload)

    t1 = asyncio.create_task(overwrite({"kind": "plan", "steps": [{"v": 2}]}))
    # 等第一次覆写进入 descriptor 计算（线程侧事件），带界轮询。
    for _ in range(400):
        if first_compute_started.is_set():
            break
        await asyncio.sleep(0.005)
    assert first_compute_started.is_set()

    t2 = asyncio.create_task(overwrite({"kind": "plan", "steps": [{"v": 3}]}))
    try:
        # 修复后：descriptor 计算在锁外，t2 可立即拿到锁完成覆写与自己的
        # descriptor（first_compute_started 已置位，不再滞留）。
        assert await asyncio.wait_for(t2, timeout=3) is True
    finally:
        gate.set()
        await asyncio.wait_for(t1, timeout=5)
    assert await t1 is True

    # 最终描述符必须反映 v=3（最新 revision），而非滞留的 v=2 计算。
    desc = store._descriptors.get("s-b1", {}).get(ref)
    assert desc is not None
    assert lock_obj.locked() is False
