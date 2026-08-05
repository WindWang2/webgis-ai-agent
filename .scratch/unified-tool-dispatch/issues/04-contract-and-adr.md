# 04 — Contract: remove the old dispatcher module + record ADR-0006

**What to build:** Close the expand–contract migration by deleting the legacy dispatcher module
entirely — once both agent paths (migrated in 02 and 03) no longer reference it, it is dead code.
Then record the architectural decision so future explorers understand why dispatch looks the way it
does: the dual-path divergence problem, the discriminated-result interface choice (and the deeper
alternative it rejected), the dispatch-once + session-keyed cache, and the Pi-first migration order.

**Blocked by:** 03 — Migrate legacy path (the contract can only run once *no* caller references the
old module).

**Status:** done — verified 2026-08-05 (code + tests)

- [x] `app/services/chat/dispatcher.py` deleted entirely — verified no remaining caller (both agent
      paths route through `ToolDispatchService` after 02 + 03)
- [x] If `is_suspicious_result` was the only surviving symbol, it has either moved to the service
      (if the service uses it) or to an appropriate helper module (if it stays a standalone pure
      function) — its tests follow it
- [x] Full suite green with the old module gone
- [x] ADR-0006 written to `docs/adr/0006-unified-tool-dispatch.md`, recording:
      - the dual-path divergence problem and the silent layer-mount regression it caused
      - the chosen interface: discriminated-result dataclass (status + typed fields)
      - the rejected alternative: deeper outcome-object-with-methods (rejected because it would
        couple dispatch to the SSE/task-tracker presentation layer — a locality break)
      - the dispatch-once + session-keyed result cache and the two-adapter shape
      - the Pi-first expand–contract migration order and why (opt-in canary)
- [x] The spec's reference to "ADR-0006 will be recorded alongside the build" is now satisfied
