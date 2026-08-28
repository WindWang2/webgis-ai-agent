# Spec: Pi as the agent host

> Status: Implemented on `feat/pi-as-agent-host` (hardening pass applied: envelope
> session lock, native-dump fail-fast, key-sensitive status guard, bare-name
> reject at the HTTP boundary). Live-LLM E2E of the Chengdu chain is the merge gate.
> Parent map: GitHub issue “Pi as the agent host”.
> Glossary: Session, SessionPlan, GIS Harness, MapProductPlan, CartographyRecipe, Tool dispatch, Cartography Verdict, Observed Map, Pi Native Surface.
> ADRs this spec adds: 0076 (SessionPlan is Pi-path plan truth), 0077 (wrap vendored Pi).
> Does not reopen ADR-0055, 0006, 0071. (ADR-0071 “shared cartography session
> runtime” currently lives only on the unmerged next-generation-harness line,
> commit `7955cba`; the shared evaluation seam it records — both hosts
> evaluating cartography through the same session-scoped harness state — is
> already how master works.)

## Problem Statement

The chat badge says the assistant is powered by Pi, and the product is supposed to be a geographic agent. Asking for Chengdu elementary-school distribution still fails like a coding agent: Pi calls a map-verdict pull with analysis arguments (`city`, `topic`, `scope`), then falls through to bash. Planning, tool schemas, and plan HUD events still live on the ChatEngine fallback. The map is empty of insight even when the runtime is Pi.

## Solution

Keep Pi as the agent host by **wrapping** the vendored coding harness, not forking it. Give Pi real GIS tools with live registry schemas for intent, product, verdict, China POI, admin boundary, and tool discovery. Keep the long tail behind one execute proxy. Store a **SessionPlan** on the Session (not on Pi’s own session): MapProductPlan as the GIS chapter, capability progress as the other chapter. Emit SessionPlan SSE under new event names. ChatEngine may lag on plan/prompt/schemas; it must not lag on map world or cartographic quality. Cartography Verdict inject/pull stays as it is.

## User Stories

1. As a map user, I want to ask 「成都市小学分布情况」 on a Pi-powered chat and get schools on the map, so that the assistant matches the “geographic agent” promise.
2. As a map user, I want that request to load a city boundary and elementary-school points, so that the map shows distribution rather than an empty canvas.
3. As a map user, I want a heatmap or product layout after the points land, so that I see a finished map not a raw dump.
4. As a map user, I never want a tool error that says cartography status does not accept `city` / `topic` / `scope`, so that analysis is not mistaken for a verdict pull.
5. As a map user, I never want the assistant to run shell commands for a GIS question, so that a coding harness leftover cannot steal the turn.
6. As a map user, I want a follow-up 「换个颜色」 to keep Chengdu schools, so that restyling does not start a new city analysis.
7. As a map user, I want 「分析北京学校」 to replace the Chengdu plan, so that two goals do not smear into one envelope.
8. As a map user, I want the next message to still know we are on Chengdu schools and which analysis steps remain, so that I do not re-explain the task every turn.
9. As a map user, I want cartographic pass/fail to work the same as today (inject and status pull), so that a host change does not reopen map-quality policy.
10. As a map user, I want refreshing the page to keep layers and refs, so that killing the Pi process is not killing the GIS world.
11. As a map user, if Pi dies mid-turn I want my last typed question still in history, so that retry is possible.
12. As a map user, if Pi dies mid-turn I accept losing in-flight thinking, so that failover can start a ChatEngine turn without pretending the Pi loop is resumable.
13. As a map user on ChatEngine fallback, I still want the same map and the same cartographic verdict, so that emergency mode is not a blank map.
14. As a map user on ChatEngine fallback, I accept the old step-plan HUD, so that fallback is not blocked on SessionPlan UI.
15. As GeoAgent, I want `webgis_map_intent` as a first-class tool with a `query` field, so that distribution requests write the GIS chapter instead of guessing a proxy name.
16. As GeoAgent, I want intent success to replace the current SessionPlan GIS chapter with that MapProductPlan, so that there is no shadow plan in the tool result only.
17. As GeoAgent, I want China admin boundary and POI tools with their real parameters, so that 「成都市」+「小学」cannot be stuffed into a zero-arg verdict tool.
18. As GeoAgent, I want `webgis_cartography_status` to accept only an empty argument object, so that extra keys fail closed.
19. As GeoAgent, I want `list_available_tools` with a live domain list, so that I can discover long-tail names without 150 schemas in context.
20. As GeoAgent, I want heatmap and display-finalize on the execute proxy, so that the native set stays an entrance kit not an analysis kitchen sink.
21. As GeoAgent, I must not wrap a native tool name inside execute, so that there is one call shape per tool.
22. As GeoAgent, I must not invent a second plan in markdown or bash, so that SessionPlan stays the only plan truth on this path.
23. As GeoAgent, I want `webgis_map_product` to update the same envelope and the MapSpec, so that product assembly is a step not a new plan.
24. As GeoAgent, I want a bounded next-turn note of recipe and open capabilities, so that I do not reload the full product plan into context.
25. As the host, I want a SessionPlan slot to exist before tools run, so that intent has somewhere to write.
26. As the host, I want SessionPlan keyed by session id in SessionStore, so that `--no-session` Pi restarts do not drop the plan.
27. As the host, I want SSE `session_plan_updated` when the GIS chapter is written or replaced, so that observers can see plan identity change.
28. As the host, I want SSE `session_plan_progress` when a capability completes or is voided, so that progress is capability-shaped.
29. As the host, I want SSE `session_plan_superseded` when the user goal changes, so that stale envelopes are obvious.
30. As the host, I must not emit CanonicalPlan `plan_ready` / `plan_step_done` / `plan_finalized` on the Pi path, so that the fallback HUD is not fed a lie.
31. As the GIS Harness, I want to keep computing intent and recipes without executing tools, so that domain intelligence stays pure.
32. As Tool dispatch, I want every GIS tool still going through the unified dispatch chain, so that refs and map mounts keep working on both runtimes.
33. As an operator, I want ChatEngine to remain a fallback flag, so that a dead Pi process is not a dead product.
34. As an operator, I want ChatEngine to keep its own prompt, catalog, and CanonicalPlan, so that fallback does not wait on a SessionPlan port.
35. As a developer wrapping Pi, I want native tool schemas generated from the live registry, so that a handwritten second catalog cannot drift.
36. As a developer, I want vendored Pi treated as a black box, so that GIS work does not become a Pi fork.
37. As a developer, I accept that tools and persona are fixed at process start, so that this spec does not require post-spawn RPC to retarget tools.
38. As a QA engineer, I want Chengdu-schools and the old status hallucination as scripted tool sequences without a live model, so that the host contract is testable.
39. As a QA engineer, I want existing cartography-status tests to keep passing with empty args, so that verdict pull is not broken by native wrapping.
40. As a future product owner, I want SessionPlan panel, breakpoint restore, deferred tool loading, and upstream Pi contribution left for later, so that this spec can ship without boiling the ocean.

## Implementation Decisions

Three layers, already locked:

1. **Host** — vendored Pi process, RPC, extension, turn (token, SSE, persona hook), and guaranteeing a SessionPlan slot. No source edits inside vendored Pi.
2. **GIS Harness** — intent, recipe, product planner. Fills the GIS chapter when intent runs. Does not execute tools and does not hold runtime.
3. **Neither** — Session identity and SessionStore, unified tool dispatch, MapSpec lifecycle, Observed Map, Cartography Verdict.

**SessionPlan.** Current host-plan envelope on a Session. GIS chapter is an embedded MapProductPlan (recipe id lives there). Progress chapter is capability / data-requirement completion, not tool-name steps. Host opens the slot. Native intent replaces the GIS chapter on success. Same user goal + intent again: replace chapter and void related progress. New user goal: supersede the envelope. Product assembly writes MapSpec and updates the same envelope. Model must not write a parallel plan. ChatEngine does not have to read or write SessionPlan.

**Native tools** (schemas from the live registry only): map intent, map product, component update, cartography status, local POI query, local admin boundary, list available tools. **Execute proxy** for heatmap data, finalize display, and every other GIS tool. Status arguments are empty; unknown keys reject. Do not execute-wrap a native name.

Prototype (`resolveCall`) encoded the surface as three kinds — native, execute, reject — with this decision core (not the HTML shell):

```text
native names: webgis_map_intent, webgis_map_product, webgis_component_update,
  webgis_cartography_status, query_local_poi, get_local_admin_boundary,
  list_available_tools
status extra keys → reject
execute(toolName) if toolName is native → reject
unknown bare name → reject (discover via list_available_tools, then execute)
```

**Wire.** New SSE: `session_plan_updated`, `session_plan_progress`, `session_plan_superseded`. Do not reuse CanonicalPlan event names. Next user turn gets a bounded SessionPlan projection (current recipe, open capabilities, replaced/superseded flag). That block is not Cartography Verdict. Existing plan HUD stays on CanonicalPlan events. A dedicated SessionPlan panel is later.

**Wrap.** Spawn with builtin tools disabled. Persona via the extension start hook. Static tool set at process start. No fork. No Pi-core plan type. No requirement for RPC that changes tools or prompt after spawn.

**Fallback.** Must share Session identity, unified dispatch, MapSpec lifecycle, Observed Map, Cartography Verdict. May lag: SessionPlan, native schemas, GeoAgent prompt, SessionPlan SSE. Failover keeps map, refs, checkpoints, persisted user messages. May drop in-flight thinking, uncommitted SessionPlan progress, Pi-only SSE.

**Persona.** Pi is GeoAgent for geographic questions, not a coding assistant. Bash/read/write/edit stay off the default tool set.

## Testing Decisions

Good tests observe **external behavior**: after a scripted tool call, what SessionPlan and SSE look like, and whether invalid native arguments fail closed. They do not assert extension file layout, prompt string wording, or Pi subprocess internals.

**Primary seam (one chain, no live model):** host-side tool dispatch into the unified dispatcher, then SessionPlan in SessionStore and SessionPlan SSE payloads. Drive 「成都市小学分布情况」 as an ordered list of tool calls (intent → boundary → POI → execute heatmap → product → empty status). Assert: GIS chapter is the intent MapProductPlan; progress tracks capabilities; `session_plan_updated` / `session_plan_progress` fire; status with analysis keys never succeeds; execute wrapping intent is rejected if that guard is implemented at dispatch.

**Existing seams to keep using:** registry/dispatch tests for cartography status empty args and unknown-parameter rejection; unified dispatch tests that refs still mount; health/runtime badge tests that Pi is the live host.

**Do not** require a live LLM or a full Pi RPC round-trip to prove SessionPlan or native schema guards. Optional later: extension-level tests that the seven names are registered.

Prior art: cartography status evidence tests, GIS harness intent-tool tests (Chengdu schools query), Pi dispatch integration tests, SSE stream tests for `plan_*` (those names must **not** appear on the Pi path for SessionPlan).

Exception to "no extension internals": `tests/test_tool_meta_contract.py` greps the extension source for the persona hook (`before_agent_start` / GeoAgent) and the `WEBGIS_NATIVE_TOOLS_PATH` wiring. It pins host↔extension text contracts the spec calls out (persona, distribution-before-status routing); it does not assert file layout or Pi subprocess internals.

SessionPlan wire is **stream-only by contract**: `session_plan_updated` / `session_plan_progress` / `session_plan_superseded` are emitted on the SSE stream after each tool execution; the non-stream `prompt()` path does not carry them (no consumer exists today — a non-stream consumer reads the envelope from SessionStore).

## Out of Scope

- Deleting ChatEngine
- Keying the GIS world to a Pi session (ADR-0055 stands)
- Registering about 150 GIS tools as Pi-native
- Forking or editing vendored Pi core
- Reopening cartography-verdict policy
- SessionPlan frontend panel
- Multi-turn breakpoint restore / subagent GIS
- Runtime `setActiveTools` from SessionPlan (deferred schemas / prompt cache)
- Upstream contribution of the GIS extension
- Implementing from the wayfinder map without this spec

## Further Notes

Parent wayfinder map: GitHub “Pi as the agent host”. Research notes: vendored Pi as non-coding host; Pi vs ChatEngine host seams. Throwaway surface walk lives on git branch `prototype/pi-native-surface`. Next implementation should add ADR-0076 and ADR-0077 as accepted records beside this spec. `/to-spec` does not implement.
