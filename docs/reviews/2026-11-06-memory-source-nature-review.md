# Independent memory-consumer / Nature resource verification — 2026-11-06

Core requested independent verification of #1951 S49-L3 and #1939 S96-006. No commits.

| Finding | State | Evidence |
|---|---|---|
| #1951 S49-L3 producer namespace | fixed | Numeric profiles now include only each layer's own `(source_id, classified_field)`, not every source with the same field name. A shared collision-safe `field_subject` helper preserves readable `source::field` keys and hashes ambiguous delimiters/identities longer than the255-char subject column. |
| #1951 S49-L3 drift consumer | fixed | Found a genuine gap in the initial change: classification facts still had plain field subjects, so namespaced profile drift invalidated zero classifications. Classification harvesting now uses the same source/field subject and source_id payload; ambiguous historical field-only priors become stale. Plain-field reads return only one unambiguous active scheme; two sources cannot silently pick a winner. Real SQLite test creates two source-specific schemes, changes one profile, verifies exactly that classification becomes stale and the other remains active. |
| #1939 S96-006 owner path checks | fixed / verified | Input/output both remain restricted to scoped owner workspaces/upload ledger roots. Removed the additional hardcoded `./data` resolver, which incorrectly rejected valid custom DATA_DIR/base-storage paths; relative paths use configured DATA_DIR before the same owner gate. Output may only use workspace roots, not read-only owned upload roots. Independent real TIFF test accepts configured owner paths and rejects foreign input/output before numerical processing. |
| Scoped-root sibling symlinks | fixed by core; validation delegated | Independent read found parent-only comparison admitted sibling-root aliases. Core fixed root identity and lexical upload ownership, and added real symlink tests; join core tests120 evidence. This specialist did not edit scoped_raster_paths.py. |

`/tmp/data-memory-nature85.log`:69passed original cartographic memory/governance/distribution/recipe + Nature NDVI/NBR/raster boundaries. `/tmp/data-memory-nature86.log`:**72passed**, including new two-source consumer, delimiter/length identity and custom-owner-path adversarial tests. Ruff passes. No baseline change.

Actual downstream adaptive consumer verification: `/tmp/data-memory-adaptive96.log` **2passed** (three-round same-requirement quality never degrades; untrustworthy verdict never seeds prior memory). Source namespace migration retains unambiguous legacy field reads and fails ambiguous cross-source reuse.
