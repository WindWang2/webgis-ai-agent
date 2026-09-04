# ADR-0100: Pi Runtime V6 — unified cancellation, wave fairness, subagent cancel, truthful skill refresh

status: Accepted
date: 2026-09-04
relates-to: ADR-0052 (durable cancellation), ADR-0077 (turn ownership),
ADR-0093 (V5 concurrency)

## Context

V5 gave the bridge pool provable lease/ordering invariants, but cancellation
was still *convergent by discipline*: the tracker token and the bridge turn
token are independent signals joined by three hand-written abort call sites
(task cancel / job cancel / session delete) — each needing to remember the
#1066 lesson; a fourth path would inevitably forget. The wave semaphore was
process-wide FIFO with no per-session fairness (one tool-heavy session
starves everyone), `subagent.py` had zero cancellation wiring (cancel
detected only via the literal string "任务已取消"), and the skill surface
freeze at spawn had no truthful refresh story.

## Decision

1. **One cancellation seam** — `app/services/chat/session_cancellation.py`:
   - `abort_active_pi_turn(session_id)` — the only bridge-abort primitive.
     Resolves the owning worker via the session-keyed `_active_turns` table
     (no entry → no active turn → no abort; never a blind singleton abort),
     applies the CONC-F7 budget, never raises.
   - `cancel_agent_task_and_turn(db, task_id)` — the agent-task cascade
     (tracker terminalize + durable cancel requests + registry ignition +
     bridge abort) shared verbatim by `DELETE /tasks/{id}` and the agent
     branch of `DELETE /jobs/{id}`. Session deletion aborts through the same
     primitive.
2. **Wave fairness** — `_SessionWaveGate` in `ToolDispatchService`: per-session
   cap (`TOOL_WAVE_SESSION_CONCURRENCY`, default 2) *before* the global wave
   semaphore. Acquire order (session gate → global slots) makes gate waiters
   hold zero global slots: a session at its cap queues; other sessions pass.
   No deadlock cycle; the held-sessions table self-cleans at zero.
3. **Subagent cancellation** — the dispatcher links a child
   `CancellationToken` to the ambient parent token (contextvar), races
   `sub_engine.chat()` against `token.wait()`, and returns an honest
   `error="cancelled"` result. No more string-matching cancellation detection.
4. **Truthful skill refresh** — tier-3 tool `refresh_skill_surface`: reloads
   the registry layer (new skills immediately callable via `webgis_execute`)
   and truthfully reports the native schema layer as frozen-at-spawn
   (respawn is an operator decision; the tool never respawns — respawns kill
   active turns). No Pi fork; no pretend hot-reload.

The extension's `_signal` → fetch `AbortController` propagation (Pi-side
abort cancels the in-flight HTTP tool call, with honest cancelled-vs-timeout
text) already exists on master and is kept as-is.

## Invariants preserved

- INV-P1..P4 lease discipline untouched (abort stays lock-free/snapshot-based).
- `agent_settled` remains the sole turn terminator; the seam never touches
  the event stream.
- Cancellation of a task never rolls back already-issued cancel requests;
  abort failure logs and reports `{"aborted": false}` instead of raising.
- Pool size 1 behavior unchanged (the gate degenerates to per-session cap
  over the same single semaphore).

## Rejected alternatives

- **Cross-pod abort RPC** (a second transport to other pods' bridges):
  violates sticky-session ownership (ADR-0077) and adds a new failure domain;
  the Redis turn registry stays validation-only.
- **Priority queue in the wave semaphore**: fairness across sessions is the
  requirement; per-session caps achieve it with a Condition + counters and
  zero semaphore surgery.
- **Auto-respawn on skill refresh**: would terminate active turns; truth
  (two-layer report) over convenience.
