# `MapSpecSource` as a pure-function module, not a value type

**Status:** accepted

The shape knowledge "a MapSpec source is `{type: "geojson"}` carrying exactly one of
`inlineData` (dict) / `url` (str) / `dataPath` (ref)" is re-derived across the store and its
checkpoint companion. We will concentrate it into a small **pure-function module**,
`app/services/mapspec_source.py`, operating on bare source dicts — **not** a Pydantic/dataclass
value type. The module exposes **three** primitives, each with a real caller, and changes
**no behavior**: `store_data(entry, data)` (the shared `dict → inlineData` / `str → url`
classifier), `profile_data(entry)` (the `inlineData → url → dataPath` fallback read), and
`ref(entry)` (the `url | dataPath`-as-cursor lookup).

We deliberately did **not** add a `has_data(entry)` presence predicate. Its one natural call
site — `layer_upsert`'s idempotency guard — checks only `inlineData`/`url`, whereas a presence
predicate would also count `dataPath`. Wiring the guard through `has_data` would *change*
behavior (an entry holding only `dataPath` would newly skip the write). Per the "house only what
is actually duplicated" stance, shipping an unused-and-non-equivalent primitive is Speculative
Generality; the guard keeps its two-key check inline until a real second caller appears.

## Why a module, not a type

A typed `MapSpecSource` value object is tempting and was the architecture review's first framing
(Candidate #3, "Worth exploring"). We rejected it. A MapSpec is fundamentally a JSON document —
the file on disk, the Redis cache, and the TS compiler all consume the raw dict. Wrapping it in a
Pydantic model introduces JSON↔type conversion at every read/write boundary for very little
invariant protection: the only "invariant" here is *one of three keys present*, which a two-line
classifier expresses directly. This is exactly the trap the just-reverted `MapSpecView` fell into
(ADR-0007's sibling cleanup, commit `96f4a02`): a typed model that the JSON-doc reality never
calls through, leaving it dead beside the dict mutations it was meant to replace. The right
analogy is the function `view_has_center(mapspec)` that survived that revert — a pure predicate
over a bare dict, no type, doing one thing well. `MapSpecSource` follows that shape, deliberately.

## Why house, not normalize

`url` is overloaded today: it carries real HTTP/local URLs, opaque `ref:xxx` cursors, and bare
path strings. A deeper refactor would promote `ref` to a first-class field and stop stuffing
`ref:` strings into `url`. We are **not** doing that here. It crosses the Python↔TS boundary (the
compiler reads `source.url || source.dataPath`, `compiler.ts:222`) and breaks on-disk checkpoint
compatibility, so it is its own future ADR with a migration. This ADR houses the existing shape
behind a narrow interface; a later change can promote `ref` by editing one function body, not
four sites.

## What the call sites keep

The two write sites are not, on inspection, doing "the same thing duplicated":
`source_profile` applies a **strict** url-shape guard (`http`/`/` prefix) and overwrites
unconditionally; `layer_upsert` applies an **idempotency** guard (skip if data already present)
and accepts any string. Those policies legitimately differ and stay at their call sites. The
module owns only the genuinely-shared classification — the part that was actually written twice —
plus the read helpers. This keeps the change a pure refactor with zero behavior change, which is
the only honest way to land a "concentrate duplicated shape knowledge" candidate.

## Considered options

- **Pure-function module (chosen).** Kills the duplicated classifier and the re-derived read
  chains; leaves policy and the JSON-doc reality untouched; mirrors the surviving `view_has_center`.
- **Typed value object.** Rejected — re-creates the dead-`MapSpecView` failure mode (typed model
  the JSON reality never round-trips through) for an invariant too shallow to need it.
- **Don't do it.** Rejected — the duplication is real (same 2-line classifier written twice in one
  file; two divergent or-chain readers), so doing nothing leaves a known friction that the next
  edit will re-derive a fifth time.

## Trigger to revisit

Two conditions reopen this:

1. **A fourth caller appears** — at four sites the duplication argument only strengthens; at one
   it would dissolve (then inline it).
2. **The `url`/`ref` overload is normalized** — when a future ADR promotes `ref` to first-class,
   this module is where that change lands (one `ref()` body edit), and at that point a typed value
   object may finally earn its keep.
