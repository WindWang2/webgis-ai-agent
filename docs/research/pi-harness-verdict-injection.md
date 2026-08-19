# How the next Pi turn receives cartographic verdicts

**Ticket:** [#652](https://github.com/WindWang2/webgis-ai-agent/issues/652) (map [#644](https://github.com/WindWang2/webgis-ai-agent/issues/644))
**Question:** What does the next Pi turn actually see today: `[CARTOGRAPHY_VERDICT]`, `webgis_cartography_status`, same-turn payload, or nothing on pass?
**Method:** Primary sources only (current Python/TS call sites and the tests that lock them). No ADR restatement.

---

## Answer

The next Pi turn **does not** receive the session harness verdict through same-turn tool `content`, and it **does not** auto-call `webgis_cartography_status`. Chat injects a bounded `[CARTOGRAPHY_VERDICT]` block into the **next user message** only when `should_inject_verdict` is true: an unresolved failure (or a fingerprint-matched `not_evaluated` that is not a “no activity” skip), stored in `_cartographic_review`, matching the current MapSpec fingerprint. **Pass, `passed_with_warnings`, and `superseded` inject nothing.** Same-turn mutation results may still carry a **different** object — MapSpec lifecycle `cartographic_review` (`stage: desired_state`) — inside `llm_payload`; that is not the harness verdict. The agent can **pull** the stored harness verdict in any turn via `webgis_cartography_status`, including after the same-turn evaluate has persisted.

---

## Sequence: when evaluation runs vs when the Agent sees it

Two clocks. Evaluation writes session state. The Agent sees a verdict only on a later prompt injection or an explicit status-tool pull.

```text
prior mutation / observation / ACK
  → evaluate_cartographic_session
  → persist map_state["_cartographic_review"]

next POST /chat/completions or /chat/stream  (Pi path)
  → _record_frontend_cartographic_observation   # does NOT evaluate
  → _build_cartography_turn_context             # read-only; may yield [CARTOGRAPHY_VERDICT]
  → PiBridge.prompt / stream_prompt
  → attach_turn_context(user, verdict?, turn marker)

same turn, webgis_execute callback
  → ToolDispatchService.dispatch  → llm_payload already final
  → if mapspec_fingerprint: evaluate_cartographic_session (persist review)
  → PiToolResponse.content = that same llm_payload   # no harness block appended
```

### Turn start is read-only

`_build_cartography_turn_context` documents that a new user message must not re-evaluate or repair; verdicts come from earlier event sources, and the agent may query `webgis_cartography_status` inside the turn (`app/api/routes/chat.py:247-252`).

The helper loads `_cartographic_review` plus the current MapSpec fingerprint, then either renders or returns `""` (`app/api/routes/chat.py:264-274`):

```python
review = state.get("_cartographic_review")
current_fingerprint = (
    cartographic_fingerprint(mapspec) if isinstance(mapspec, dict) else None
)
if not should_inject_verdict(review, current_fingerprint):
    return ""
return render_verdict_for_llm(review)
```

Missing `session_id`, a thrown exception, or a failed guard all yield `""` (`app/api/routes/chat.py:254-279`). Tests: empty/None session and a stale fingerprint return `""` (`tests/test_cartography_turn_injection.py:150-169`); a current-generation `failed_repairable` returns a block starting `[CARTOGRAPHY_VERDICT]` (`tests/test_cartography_turn_injection.py:134-147`).

Pre-turn frontend `map_state` is stored as `_cartographic_context_observation` and is explicitly **not** a runtime-convergence claim (`app/api/routes/chat.py:203-211`). Canonical runtime observation is a separate endpoint.

Pi completions and stream both build the block **before** sending the prompt (`app/api/routes/chat.py:634-639`, `app/api/routes/chat.py:770-798`). Legacy ChatEngine is not on this path.

### Attachment shape the model sees

`attach_turn_context` concatenates, in order: user text, optional cartography block, `[WEBGIS_TURN_CONTEXT:<token>]`, do-not-quote line (`app/services/chat/pi_turn_context.py:90-104`). Empty `cartography_block` omits the verdict paragraph entirely. Bridge `prompt` / `stream_prompt` pass `cartography_context` through (`app/agent_pi_bridge.py:1552-1554`, `app/agent_pi_bridge.py:1710-1711`). Tests lock order: user message → `[CARTOGRAPHY_VERDICT]` → turn marker (`tests/test_cartography_turn_injection.py:56-68`, `81-93`); without context, `CARTOGRAPHY_VERDICT` is absent (`tests/test_cartography_turn_injection.py:71-77`).

The extension’s `currentTurnToken` takes the **last** `WEBGIS_TURN_CONTEXT` match in the newest session entry (`app/extensions/webgis-tools/index.ts:14-25`), which is why the marker must stay last.

### Evaluation is after GIS success, before HTTP return, not in `content`

`dispatch_tool` calls `ToolDispatchService.dispatch` first (`app/agent_pi_bridge.py:346`). `llm_payload` is built there (`app/services/tool_dispatch_service.py:500-557`) and never rewritten after evaluate.

If the raw result has `mapspec_fingerprint` (`has_cartographic_generation`), the bridge persists harness context, records the event, then:

```python
# Desired-state evidence is available immediately.  Runtime PASS is
# deliberately impossible until a matching live observation and ACK
# arrive; those event endpoints invoke the same session evaluator.
if has_cartographic_generation:
    try:
        await evaluate_cartographic_session(session_id)
```

(`app/agent_pi_bridge.py:444-455`)

The HTTP response is still the pre-eval payload (`app/agent_pi_bridge.py:457-461`):

```python
return PiToolResponse(
    toolCallId=request.toolCallId,
    content=[{"type": "text", "text": result.llm_payload}],
    details=result.raw_result,
    isError=(result.status == "error"),
)
```

Pi’s `AgentToolResult` sends `content` to the model; `details` is “for logs or UI rendering” (`vendor/pi/packages/agent/src/types.ts:354-359`). The extension forwards `content` unchanged (`app/extensions/webgis-tools/index.ts:89-93`).

Evaluate persists `_cartographic_review` only after a successful `set_map_state` (`app/agent_pi_bridge.py:1063-1078`). Early exits (`no_session_harness`, deleted session) return a not-evaluated dict **without** writing that key (`app/agent_pi_bridge.py:873-895`, `960-974`). Persistence failure returns `evidence_persistence_unavailable` without replacing the stored review (`app/agent_pi_bridge.py:1066-1077`).

The same evaluator also runs from the runtime observation route and from map-action ACK (`app/api/routes/chat.py:1145-1150`, `app/api/routes/chat.py:1252-1256`). Those updates are invisible to an already-running turn unless the agent pulls `webgis_cartography_status`; they become the next turn’s injection input.

`webgis_cartography_status` is documented read-only: it does not re-evaluate (`app/tools/cartography_tools.py:591-594`). The extension only **suggests** the agent call it after display-producing changes (`app/extensions/webgis-tools/index.ts:41-44`); nothing auto-invokes it.

---

## What is injected on fail / pass / not_evaluated / superseded

Policy lives in `should_inject_verdict` (`app/lib/cartography/verdict_summary.py:15-18`, `32-55`). Comments: inject only “未收敛”; skip passed (no noise), superseded (old generation), and sessions with no cartography activity.

| Stored `cartography.status` / reason | Next-turn prompt injection | Pull via `webgis_cartography_status` |
|---|---|---|
| `failed_repairable` (and fingerprint matches) | **`[CARTOGRAPHY_VERDICT]`** | Yes, always renders the block |
| `failed_unrepairable`, `repair_exhausted` | **Inject** (not in skip set; same fingerprint rule) | Yes |
| `passed`, `passed_with_warnings` | **Nothing** (`""`) | Yes — tool path skips the inject filter |
| `superseded` | **Nothing** | Yes if still stored |
| `not_evaluated` + `termination_reason` `no_session_harness` or `no_mapspec_mutation` | **Nothing** | Yes if stored; if the key is missing, see next section |
| `not_evaluated` + any other reason, fingerprint present and matching | **Inject** | Yes |
| Missing/malformed review, missing fingerprint, current fingerprint unknown, fingerprint mismatch | **Nothing** | Missing key → “no verdict yet”; malformed dict still hits the renderer |

Guards, in order (`app/lib/cartography/verdict_summary.py:43-55`):

1. `review` must be a dict with a dict `cartography`.
2. Skip `status ∈ {passed, passed_with_warnings, superseded}`.
3. Skip `termination_reason ∈ {no_session_harness, no_mapspec_mutation}`.
4. Both `cartography.mapspec_fingerprint` and `current_fingerprint` must be truthy and equal. “无法验证就不当证据用.”

There is no generic status `"failed"`. Failures that inject in tests are `failed_repairable` (`tests/unit/test_verdict_summary.py:40-41`, `tests/test_cartography_turn_injection.py:116-147`). Pass skip: `tests/unit/test_verdict_summary.py:43-47`. Superseded skip: `tests/unit/test_verdict_summary.py:49-51`. No-activity skip: `tests/unit/test_verdict_summary.py:53-59`. Fingerprint mismatch / missing current fingerprint: `tests/unit/test_verdict_summary.py:61-69`.

When injection happens, `render_verdict_for_llm` emits (`app/lib/cartography/verdict_summary.py:84-124`):

```text
[CARTOGRAPHY_VERDICT]
{json: status, termination_reason, desired_status, runtime_status,
 mapspec_fingerprint, overall_passed, failed_checks?, repair_attempts?}
Server-verified cartography harness verdict for the CURRENT map state. ...
query `webgis_cartography_status` for full details.
```

Bounds: at most 3 `fail`/`not_evaluated` checks (`app/lib/cartography/verdict_summary.py:58-81`), messages ≤ 200 chars, last 2 repair attempts, JSON hard-clipped at 1500 chars (`app/lib/cartography/verdict_summary.py:21-23`, `114-117`). Passing checks are omitted (`tests/unit/test_verdict_summary.py:80-93`). `not_evaluated` **checks** (e.g. freshness) are projected (`tests/unit/test_verdict_summary.py:95-102`).

Prompt path is supposed to call `should_inject_verdict` first; the **tool** path “直接渲染，把完整判定交还给 agent” (`app/lib/cartography/verdict_summary.py:87-89`). That is why a passed verdict is silent on the next prompt but visible if the agent queries the status tool.

Repair advance can rewrite status **before** persist (`app/agent_pi_bridge.py:1143-1193`): `passed`/`passed_with_warnings` short-circuit; `failed_repairable` may become `not_evaluated` (`runtime_repair_ack_pending`), `superseded` (`user_or_newer_intent`), or `repair_exhausted`. The next turn sees that **post-repair** stored status, not the pre-repair one.

---

## What `webgis_cartography_status` returns when there is no verdict

Args are empty; `session_id` comes from dispatch (`app/tools/cartography_tools.py:179-180`, `598-600`).

No `session_id`: `{"success": False, "message": "Missing session_id"}` (`app/tools/cartography_tools.py:599-600`; `tests/unit/test_cartography_tools_evidence.py:587-591`).

`_cartographic_review` absent or not a dict (`app/tools/cartography_tools.py:602-610`):

```python
return {
    "success": True,
    "summary": "No cartography harness verdict yet (no MapSpec mutation evaluated).",
    "cartography": {"status": "not_evaluated", "termination_reason": "no_session_harness"},
    "overall_passed": False,
}
```

There is **no** `[CARTOGRAPHY_VERDICT]` marker. Test: `tests/unit/test_cartography_tools_evidence.py:575-584`.

When a review dict **is** stored, the tool always `render_verdict_for_llm`s it and also returns the full `cartography` / `gate` / `overall_passed` (`app/tools/cartography_tools.py:611-619`). `slim_tool_result` keeps the `summary` key for the model (`app/services/llm_result_formatter.py:233-251`), so `llm_payload` contains `[CARTOGRAPHY_VERDICT]` (`tests/unit/test_cartography_tools_evidence.py:594-617`). That pull is available **in the same turn** after evaluate has persisted, and on later turns even when prompt injection skipped a pass.

---

## Whether same-turn tool results include the harness verdict

**No.** Same-turn `PiToolResponse.content` is `ToolDispatchResult.llm_payload` from **before** `evaluate_cartographic_session` (`app/agent_pi_bridge.py:346` then `449` then `457-459`). Evaluate does not append `[CARTOGRAPHY_VERDICT]` and does not mutate `llm_payload`.

What the mutation tool **can** put in `content` is MapSpec **lifecycle** `cartographic_review`: `stage: desired_state`, nested `review.checks`, `repair_count` (`app/services/tool_dispatch_service.py:675-681`, `871-889`). Slimming for the LLM projects that nested `review` (`app/services/llm_result_formatter.py:114-145`, `246-248`; `tests/unit/test_llm_result_formatter.py:61-99`). The harness stored object is `{session_id, cartography, gate, overall_passed}` with `cartography.stage == "actual_runtime"` (`app/lib/harness/evidence.py:201-230`; `app/agent_pi_bridge.py:1027-1032`). `_slim_cartographic_review` reads `value["review"]["checks"]`, not `cartography.checks`, so it cannot project the harness verdict even if the dicts were swapped.

Non-generation tools (`mapspec_fingerprint` missing) skip evaluate entirely (`app/agent_pi_bridge.py:373-377`, `447-449`). Authoring-unavailable display results attach `desired_state` / `not_evaluated` / `mapspec_authoring_unavailable` and **omit** `mapspec_fingerprint` (`app/services/tool_dispatch_service.py:717`, `871-889`; `tests/unit/test_tool_dispatch_service.py:276-284`) — no harness generation, no session evaluate.

`details=result.raw_result` may still hold lifecycle review for UI/logs; Pi does not treat `details` as model text (`vendor/pi/packages/agent/src/types.ts:356-359`).

Same-turn **pull**: after evaluate persists, `webgis_cartography_status` reads `_cartographic_review` and **does** put `[CARTOGRAPHY_VERDICT]` in that tool’s `llm_payload`. That is opt-in, not attached to the mutation result.

---

## Direct answers to the ticket’s four options

| Option | Today |
|---|---|
| `[CARTOGRAPHY_VERDICT]` | Yes, **next-turn user-message injection**, only if `should_inject_verdict`. Failures with matching fingerprint: yes. Pass / `passed_with_warnings` / `superseded`: no. |
| `webgis_cartography_status` | Not auto-run. Static prompt hint only. Pull returns a plain “no verdict yet” string when `_cartographic_review` is missing; otherwise always renders the block (including pass). |
| Same-turn payload | Mutation `content` is slimmed GIS/lifecycle JSON. It is **not** the harness verdict. Evaluate has usually already run by the time Pi receives the HTTP response, but the text the model gets is still the pre-eval `llm_payload`. |
| Nothing on pass | **Correct for injection.** Next user message has no verdict paragraph. Transcript may still contain earlier lifecycle `cartographic_review` JSON from mutation tools. A later status-tool call would still show pass. |
