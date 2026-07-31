# Keep legacy-name normalization at the dispatch site

**Status:** accepted

We will **not** relocate `LEGACY_TOOL_NAME_MAP` / `normalize_tool_name()` out of the dispatch hot
path into history-replay only, as architecture-review Candidate #5 proposed. The proposal rests on
a premise the code contradicts, and removing the dispatch-site call would break a regression-tested
contract (ADR-006's "Seam B") and reintroduce a silent unknown-tool failure mode for live traffic.

## What the proposal assumed

Candidate #5 argued that "only history replay actually sends legacy names" — fresh agent traffic
uses canonical `webgis_*` names — so the `normalize_tool_name()` call at
`tool_dispatch_service.py`'s `dispatch()` entry is a permanent gate on the hot path doing one-shot
work that belongs at the history-replay boundary instead. It flagged "⚠ Conflicts with ADR-006."

## What the code shows

`ToolDispatchService.dispatch()` has **two** live callers, both feeding it *fresh external input*
that we do not control:

- `chat_engine.py:527,715` — `tc` originates from the LLM model response
  (`assistant_msg.get("tool_calls")`). The model can emit a legacy name it saw in training data or
  few-shot context; nothing structurally forces canonical names from the model.
- `agent_pi_bridge.py:177` — `tc` is built from `request.toolName`, a Pi-client field.

History replay (`history_service_async.py:55`) is a *third* caller, not the only one. The premise
"only history replay sends legacy names" is unverified and, given the two fresh-input paths,
unlikely.

The dispatch-site normalization is locked by `test_dispatch_normalizes_legacy_tool_names`
(`tests/unit/test_tool_dispatch_service.py:199`, labelled "Seam B"): it asserts that dispatching a
legacy name like `add_layer` reaches the registry as `webgis_layer_upsert`. Removing the
dispatch-site call fails this test and means a future model or Pi client emitting a legacy name
gets an unknown-tool error instead of a successful dispatch.

## Why keep it

Three prior ADRs (0007, 0008, 0009) apply the same standard: a seam with a tested contract and a
real consumer wins over a speculative "this lookup is wasted" optimization. Here the contract is
explicit (regression test + ADR-006 design choice #4 + the consequences note), and the cost being
optimised away is a single `dict.get()` per dispatch — not a measurable hot-path burden. The
proposal's upside (one less call) does not justify deleting a working, tested defence for two live
input paths.

## What changed in this decision

The ADR-006 consequence note previously (commit `2f28b69`) described normalization as a "read-time
translation seam ... rather than gating execution" — **inaccurate**. Normalization gates execution
at `dispatch()` *and* translates on history read. The note (and the code comments in
`tool_dispatch_service.py`) are corrected here to describe the actual two-site design.

## Trigger to revisit

Reopen with **evidence**, not assumption. Specifically, when production telemetry shows that, over
a meaningful window, *neither* the ChatEngine path nor the Pi path ever receives a legacy name
(canonical-name adoption is empirically complete and stable), the dispatch-site call becomes
genuinely redundant and Candidate #5's relocation becomes safe. Until that data exists, the
defence stays.
