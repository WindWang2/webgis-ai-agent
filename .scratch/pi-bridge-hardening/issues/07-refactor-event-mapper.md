# 07 — Refactor _map_event_to_sse dispatch

**What to build:** Replace the 7-branch if/elif chain in `_map_event_to_sse` with a dispatch table, eliminating Repeated Switch, Primitive Obsession, and Data Clumps smells.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Replace raw string event type comparisons with an Enum or dispatch dict
- [ ] Extract `_base_sse_payload(task_id, step_id, session_id)` to eliminate Data Clumps
- [ ] Replace the 7-branch if/elif chain with a dict of `event_type → handler` mappings
- [ ] Give `step_index: 0` a meaningful name or derive it from actual step metadata
