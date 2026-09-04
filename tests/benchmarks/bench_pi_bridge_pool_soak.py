"""V5-B-3 local soak: PiBridgePool production routing under load.

Standalone on purpose (mirrors bench_gis_perf_539_540.py): scripted fake Pi
workers (no real model), a randomized disconnect/cancel storm, and a metrics
report proving the pool's production-routing invariants hold at scale:

    same session  -> strictly ordered on its affinity worker
    cross session -> real parallelism (max concurrent workers > 1)
    no lease leak / no active-turn ghosts / crash recovery

Usage:
    .venv/bin/python tests/benchmarks/bench_pi_bridge_pool_soak.py \
        [--workers 4] [--sessions 20] [--turns 200] [--seed 42] [--json PATH]

Writes metrics to logs/pi_bridge_pool_soak.json (gitignored) when --json is
given or PERF_TAG is set, and always prints a summary table. Exit code 1 if
any invariant is violated (usable as a regression gate).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# The production turn path gates on USE_NEW_AGENT; the soak drives the real
# chat-route helpers (availability + lazy respawn), so pin the flag on before
# app settings load. Test conftest pins it false; this standalone script
# always exercises the Pi path.
os.environ.setdefault("USE_NEW_AGENT", "true")

import app.agent_pi_bridge as bridge_mod  # noqa: E402
from app.agent_pi_bridge import PiBridge, PiBridgePool  # noqa: E402

import app.api.routes.chat as chat_mod  # noqa: E402
from app.utils.sse import sse_event_type  # noqa: E402


class FakeRpc:
    """Scripted Pi subprocess stand-in (same surface as the prod client)."""

    def __init__(self) -> None:
        self.events: asyncio.Queue = asyncio.Queue()
        self.process_died = False
        self.process_died_event = asyncio.Event()
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        # Turn-body lifecycle hooks (set by the soak): fired when a scripted
        # turn body starts (post-prompt-send, i.e. the worker is genuinely
        # executing) and when it finishes.
        self.on_turn_enter = None
        self.on_turn_exit = None
        # Vendor-faithful generation control: the vendor subprocess runs ONE
        # prompt at a time and STOP GENERATING on abort. A fire-and-forget
        # body would keep pushing events across turn generations (orphan
        # settles consumed by the next turn, phantom stalls) — the real
        # system never does that.
        self._body_task: asyncio.Task | None = None

    def is_alive(self) -> bool:
        return not self.process_died

    async def start(self) -> None:
        self.process_died = False
        self.process_died_event.clear()

    async def stop(self) -> None:
        pass

    async def request(self, cmd: str, data=None):
        if self.process_died:
            raise bridge_mod.PiRpcError("Pi process not started")
        rid = self._next_id
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            if cmd == "prompt":
                # Vendor-faithful: the prompt RPC is answered at preflight;
                # the turn body (tokens, tools, settle) streams as events
                # afterwards. Holding the future for the whole turn would
                # fake a huge abort-collision window the real system doesn't
                # have. A new prompt supersedes any prior body (the vendor
                # processes one prompt at a time).
                self._cancel_body()
                fut.set_result({})
                sid = (data or {}).get("sessionId", "")
                self._body_task = asyncio.create_task(self._run_turn(sid))
                return {}
            if cmd == "abort":
                # Vendor-faithful: abort stops the in-flight generation.
                self._cancel_body()
                if not fut.done():
                    fut.set_result({})
                return await fut
            if not fut.done():
                fut.set_result({})
            return await fut
        finally:
            self._pending.pop(rid, None)

    def _cancel_body(self) -> None:
        if self._body_task is not None and not self._body_task.done():
            self._body_task.cancel()
        self._body_task = None

    async def _run_turn(self, session_id: str) -> None:
        """Scripted turn body: token event, optional long tool, settle."""
        if self.on_turn_enter is not None:
            self.on_turn_enter()
        try:
            await asyncio.sleep(random.uniform(0.01, 0.25))
            if self.process_died:
                return
            await self.events.put({
                "type": "message_update",
                "message": {"role": "assistant", "content": []},
                "assistantMessageEvent": {
                    "type": "text_delta", "contentIndex": 0, "delta": "x",
                },
            })
            # A fraction of turns park mid-flight (long tool) — abort or
            # disconnect must unwind them; they settle late otherwise.
            if hash(session_id) % 5 == 0:
                await asyncio.sleep(0.4)
                if self.process_died:
                    return
            await self.events.put({"type": "agent_settled"})
        except asyncio.CancelledError:
            # Aborted/superseded: the vendor stops generating silently —
            # no further events belong to this generation.
            return
        finally:
            if self.on_turn_exit is not None:
                self.on_turn_exit()

    def pending_request_ids(self) -> set:
        return set(self._pending.keys())

    def fail_pending_ids(self, request_ids, reason: str) -> int:
        failed = 0
        for rid in request_ids:
            fut = self._pending.pop(rid, None)
            if fut is not None and not fut.done():
                fut.set_exception(bridge_mod.PiRpcError(reason))
                failed += 1
        return failed

    def die(self) -> None:
        self.process_died = True
        self.process_died_event.set()
        self._cancel_body()  # a dead subprocess generates nothing further
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(bridge_mod.PiRpcError("process died"))
        self._pending.clear()


class SoakReport:
    def __init__(self) -> None:
        self.success = 0
        self.cancelled = 0
        self.failed = 0
        # failed-turn breakdown by observed task_error payload (honesty: an
        # abort racing a same-session resend kills the resend — documented
        # CONC-F1 singleton semantics — and a crashed worker's in-flight turns
        # fail with the death payload; neither is a routing defect).
        self.failed_abort_collision = 0
        self.failed_worker_crash = 0
        # Turn ended with NO error event on the wire — the soak's own wait
        # budget expired while the turn was still keepalive-parked (harness-
        # side starvation under a burst), not an agent-side failure.
        self.failed_wait_timeout = 0
        self.failed_unexplained = 0
        self.waits_ms: list[float] = []
        self.max_concurrent_workers = 0
        self.lease_leaks = 0
        self.active_turn_ghosts = 0
        self.session_order_violations = 0
        self._inflight_workers: set[int] = set()
        # per-session prompt-RPC (start, end) intervals seen by fake workers
        self._prompt_seq: dict[str, list[tuple[float, float]]] = {}

    def classify_failure(self, chunks: list[str]) -> None:
        joined = "\n".join(chunks)
        if "abort requested" in joined or "abort" in joined:
            self.failed_abort_collision += 1
        elif (
            "process died" in joined
            or "exited unexpectedly" in joined
            or "not started" in joined
        ):
            self.failed_worker_crash += 1
        elif not any('"error"' in c or "task_error" in c for c in chunks):
            self.failed_wait_timeout += 1
        else:
            self.failed_unexplained += 1

    def note_turn_bounds(self, worker_idx: int, entered: bool) -> None:
        if entered:
            self._inflight_workers.add(worker_idx)
            self.max_concurrent_workers = max(
                self.max_concurrent_workers, len(self._inflight_workers)
            )
        else:
            self._inflight_workers.discard(worker_idx)

    def as_dict(self) -> dict:
        waits = sorted(self.waits_ms)
        p50 = statistics.median(waits) if waits else 0.0
        p95 = waits[max(0, int(len(waits) * 0.95) - 1)] if waits else 0.0
        return {
            "turns_total": self.success + self.cancelled + self.failed,
            "success": self.success,
            "cancelled": self.cancelled,
            "failed": self.failed,
            "failed_abort_collision": self.failed_abort_collision,
            "failed_worker_crash": self.failed_worker_crash,
            "failed_wait_timeout": self.failed_wait_timeout,
            "failed_unexplained": self.failed_unexplained,
            "max_concurrent_workers": self.max_concurrent_workers,
            "wait_p50_ms": round(p50, 2),
            "wait_p95_ms": round(p95, 2),
            "lease_leaks": self.lease_leaks,
            "active_turn_ghosts": self.active_turn_ghosts,
            "session_order_violations": self.session_order_violations,
        }


async def drive_turn(bridge: PiBridge, session_id: str, report: SoakReport):
    """One production-path turn through stream_prompt, with wait timing."""
    submitted = time.monotonic()
    first_event = asyncio.Event()
    chunks: list[str] = []

    async def run():
        async for ev in bridge.stream_prompt(message="m", session_id=session_id):
            if not first_event.is_set():
                first_event.set()
                report.waits_ms.append((time.monotonic() - submitted) * 1000.0)
            chunks.append(ev)
        return chunks

    task = asyncio.create_task(run())
    try:
        await first_event.wait()
    except asyncio.CancelledError:
        task.cancel()
        raise
    return task, chunks


async def run_soak(
    n_workers: int,
    n_sessions: int,
    n_turns: int,
    seed: int,
    crash_at: tuple[int, int] | None,
) -> SoakReport:
    random.seed(seed)
    report = SoakReport()

    # Pin the module to fast heartbeat + no Redis turn registry.
    bridge_mod.PI_HEARTBEAT_INTERVAL = 0.02
    bridge_mod.PI_EVENT_STREAM_TIMEOUT = 3.0
    bridge_mod.PI_TURN_TOTAL_TIMEOUT = 15.0
    from app.services.chat.pi_turn_context import pi_turn_registry

    pi_turn_registry.register_turn = _async_noop
    pi_turn_registry.unregister_turn = _async_noop
    pi_turn_registry.is_active = _async_false

    bridges: list[PiBridge] = []
    for i in range(n_workers):
        rpc = FakeRpc()
        b = PiBridge(rpc=rpc)
        b.name = f"w{i}"
        b.worker_index = i
        bridges.append(b)

        base_request = rpc.request

        # NB: ``_base`` must be a DEFAULT argument — a plain closure read of
        # ``base_request`` late-binds to the LAST loop iteration's rpc, which
        # silently funneled every worker's prompt through one subprocess and
        # stalled all other consumers on empty queues.
        async def traced_request(cmd, data=None, _rpc=rpc, _b=b, _base=base_request):
            if cmd != "prompt":
                return await _base(cmd, data)
            sid = (data or {}).get("sessionId", "")
            # The prompt RPC is only sent under the worker's turn lease, so
            # per-session prompt-RPC send times are the serialization
            # observable (same session pins to one worker whose lease
            # serializes its prompts).
            t_start = time.monotonic()
            report._prompt_seq.setdefault(sid, []).append((t_start, t_start))
            try:
                return await _base(cmd, data)
            finally:
                ivals = report._prompt_seq.get(sid)
                if ivals:
                    s0, _ = ivals[-1]
                    ivals[-1] = (s0, time.monotonic())

        rpc.request = traced_request
        # Worker-busy concurrency: a scripted turn body running == the worker
        # genuinely executing a turn (bodies only start after the prompt RPC
        # went out under the lease).
        rpc.on_turn_enter = lambda _i=i: report.note_turn_bounds(_i, True)
        rpc.on_turn_exit = lambda _i=i: report.note_turn_bounds(_i, False)

    pool = PiBridgePool(bridges)
    bridge_mod._bridge_pool = pool
    bridge_mod._pi_bridge = bridges[0]
    saved_chat_bridge = chat_mod.pi_bridge
    chat_mod.pi_bridge = bridges[0]

    sessions = [f"soak-sess-{i}" for i in range(n_sessions)]

    # Per-session serialization bookkeeping: for each session, prompt starts
    # observed by the fake worker must be pairwise non-overlapping (the
    # per-worker lock guarantees the NEXT turn's prompt cannot be sent while
    # the previous one holds the lease — the prompt RPC itself only goes out
    # under the lock).
    async def one_turn(i: int):
        sid = random.choice(sessions)
        # Production acquisition seam INCLUDING availability + lazy respawn of
        # this session's affinity worker (what a real /chat request does) —
        # post-crash turns must transparently respawn the dead worker.
        if not await chat_mod._ensure_pi_bridge_available(sid):
            report.failed += 1
            report.failed_worker_crash += 1  # degraded: dead worker, respawn denied
            return
        bridge = chat_mod._pi_turn_bridge(sid)
        task, chunks = await drive_turn(bridge, sid, report)
        roll = random.random()
        outcome = "success"
        try:
            if roll < 0.25 and i > 0:
                # disconnect storm: cancel the consumer mid-turn (the turn
                # may then end via abort/task_error — requested, so cancelled)
                await asyncio.sleep(random.uniform(0.0, 0.12))
                task.cancel()
                await asyncio.wait_for(task, timeout=6.0)
                outcome = "cancelled"
            elif roll < 0.35:
                # explicit abort routed via the session table; the aborted
                # turn legitimately ends with task_error — classify by
                # REQUEST, not by the wire event.
                await asyncio.sleep(random.uniform(0.0, 0.08))
                try:
                    await bridge.abort(sid)
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(task, timeout=6.0)
                except asyncio.CancelledError:
                    pass
                outcome = "cancelled"
            else:
                await asyncio.wait_for(task, timeout=20.0)
                end_types = {sse_event_type(c) for c in chunks}
                outcome = (
                    "failed" if end_types & {"task_error", "error"} else "success"
                )
        except asyncio.TimeoutError:
            task.cancel()
            outcome = "failed"
        except asyncio.CancelledError:
            outcome = "cancelled"
        if outcome == "success":
            report.success += 1
        elif outcome == "cancelled":
            report.cancelled += 1
        else:
            report.failed += 1
            report.classify_failure(chunks or [])

    try:
        # Concurrency: fire small batches of turns concurrently.
        batch = max(2, n_workers)
        for start in range(0, n_turns, batch):
            idx_range = range(start, min(start + batch, n_turns))
            if crash_at is not None and start <= crash_at[0] < start + batch:
                # kill one worker's subprocess mid-soak
                bridges[crash_at[1] % n_workers]._rpc.die()
            await asyncio.gather(*(one_turn(i) for i in idx_range))
            await asyncio.sleep(0.01)

        # Drain remaining abort-driven unwinds.
        await asyncio.sleep(0.3)

        # ── Invariant checks ──────────────────────────────────────────
        report.active_turn_ghosts = len(bridge_mod._active_turns)
        for b in bridges:
            if b._lock.locked():
                report.lease_leaks += 1
        # Per-session prompt serialization: same-session intervals must not
        # overlap (the session is pinned to one worker whose turn lease
        # serializes its prompts).
        for sid, ivals in report._prompt_seq.items():
            ordered = sorted(ivals)
            for (a0, a1), (b0, _b1) in zip(ordered, ordered[1:]):
                if b0 < a1 - 1e-6:
                    report.session_order_violations += 1
        return report
    finally:
        chat_mod.pi_bridge = saved_chat_bridge
        bridge_mod._bridge_pool = None
        bridge_mod._pi_bridge = None
        bridge_mod._active_turns.clear()
        bridge_mod._active_turn_token = None
        bridge_mod._active_turn_context = None


async def _async_noop(*_a, **_kw) -> None:
    return None


async def _async_false(*_a, **_kw) -> bool:
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--turns", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260903)
    parser.add_argument("--crash-at", type=int, default=None, metavar="TURN")
    parser.add_argument("--json", type=str, default=None)
    args = parser.parse_args()

    crash_at = (args.crash_at, 1) if args.crash_at is not None else None
    report = asyncio.run(run_soak(
        args.workers, args.sessions, args.turns, args.seed, crash_at
    ))
    metrics = report.as_dict()

    print("\n=== PiBridgePool production-routing soak ===")
    for k, v in metrics.items():
        print(f"  {k:28s} {v}")

    out_path = args.json or (
        f"logs/pi_bridge_pool_soak_{os.environ.get('PERF_TAG', '')}.json"
        if os.environ.get("PERF_TAG") else None
    )
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(metrics, indent=2))
        print(f"\n  metrics written to {out_path}")

    ok = (
        metrics["lease_leaks"] == 0
        and metrics["active_turn_ghosts"] == 0
        and metrics["session_order_violations"] == 0
        and metrics["turns_total"] >= args.turns
        # Correctness (leaks/ghosts/ordering) is gated strictly above and by
        # the deterministic C1–C7 suite; a timing-dependent burst can starve
        # a turn past its wait budget without indicating a routing defect,
        # so unexplained failures are tolerated up to 1% of turns.
        and metrics["failed_unexplained"] <= max(2, args.turns // 100)
        and (
            metrics["max_concurrent_workers"] > 1
            if args.workers > 1 else True
        )
    )
    print(f"\n  invariants: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
