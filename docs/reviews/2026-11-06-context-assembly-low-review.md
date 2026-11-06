# Context assembly Low verification — 2026-11-06

Shared branch `codex/all-issues-deep-review`, baseline master `e0e97b462`. Delegated from frontend/core; no commits or external writes.

| Issue | Finding | Status | Implementation and evidence |
|---|---|---|---|
| #1862 | S3c-016 | fixed / verified | `ScopedText` carries immutable source-scope mapping from actual authorized Project rows, retrieved memory record orgs and working context `scope_ref`. The existing ProjectService authorization, org/scope-filtered memory retrieval and working-context scope guards remain in place. Providers cannot relabel plain text with request scope: missing source evidence is a recorded provider error. Plan/Harness use their actual source session IDs; shared session facts bind to the loaded source envelope. ContextItem checks every supplied org/project dimension and fails missing project/org ownership instead of wildcard acceptance. Foreign-plan tool surface is disabled before side effects. |
| #1862 | S3c-017 | fixed / verified | Preferred floors can be trimmed then explicitly omitted when global hard cap still cannot fit them. One final outcome per `(provider_id,item_id)` preserves original token cost and final decision. Untouchable user/control overflow fails assembly explicitly instead of forwarding an over-budget prompt. |
| #1862 | S3c-018 | fixed / verified | An explicit governor `reject` raises typed `ContextAssemblyRejected` before prompt settlement. `bind_turn_prompt` propagates this deliberate denial and cannot bypass it via legacy fallback. Governor absence/reconciliation failure retain existing observational handling; explicit rejection is enforced. |

Validation checkpoint: `/tmp/data-context73.log` 78 passed, one new test omitted required max_output_tokens parameter (corrected). `/tmp/data-context74.log` runs full context assembly + Chat/Pi integration + GIS context/memory security/retrieval wiring. New behavioral tests cover foreign source scope, unknown scope, existing project authorization refusal before data read, real Project metadata, foreign source plan, floor budget overflow/unique outcomes, and governor rejection with no settle/legacy call.

Files outside the assembly package changed only at existing source-render return seams: `chat/context_assembler.py`, `gis_memory/queries.py`, `gis_context/hotpath.py`; `chat/pi_turn_context.py` enforces explicit-denial propagation. Neutral wrapper lives in `app/lib/runtime/scoped_text.py`.

## Final verification

- `/tmp/data-context74.log`: **298 passed** — full typed assembly, Chat/Pi integration, GIS working context and GIS memory security/retrieval/wiring, plus raster capture boundary. Existing authorization regression suites pass unchanged. One pre-existing test-suite aiohttp session cleanup warning follows successful completion.
- `/tmp/data-context75.log`: **36 passed** — new source-identity tests (including actual authorized Project row metadata and foreign envelope/tool-surface rejection), V6 context assembly and raster Low/capture regressions.
- Ruff and owned-path `git diff --check`: pass. `/tmp/data-context-debt76.log` debt ratchet: pass; baseline unchanged.

Authority boundaries retained:

| Provider family | Existing source authorization | New evidence used by item gate |
|---|---|---|
| Project cartographic memory | `ProjectService.get_project_with_auth` before active facts/environment changes | Actual authorized `Project.id/org_id` |
| Project knowledge | Feature flag; authenticated ProjectService read; org+project-filtered active entries | Actual authorized `Project.id/org_id` |
| GIS memory | Existing org/session/project/user target retrieval and sensitive-record filtering | All returned records must carry one actual nonempty org; source org travels with projection |
| Mission GIS card | Existing binding-org check, stored working-context owner and `scope_ref().renderable_in` | Actual working context scope_ref after existing guard |
| Plan/V6/tool surface/Harness | Existing session authorization upstream; canonical session-bound envelope/context read | Actual envelope/context session_id, not a request relabel |
| Verdict, representation, evicted refs | Existing authorized session namespace; shared reads | Source session namespace from loaded canonical envelope; absent source evidence is denied |

Caller-injected environment/legacy blocks retain the documented upstream route authorization boundary. This change does not create a standalone authentication API or bypass any source guard. Governor explicit denial is enforced before settlement and propagated through Pi binding; unavailable observational services retain the previous fallback behavior.

Separate core-requested integration: `raster_store.save_png` calls task-local `note_created_ref` only after atomic publication. Nested capture regression proves sibling capture does not populate the parent collector.
