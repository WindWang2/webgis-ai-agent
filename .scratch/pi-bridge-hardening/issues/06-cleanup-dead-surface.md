# 06 — Cleanup compaction strings, get_messages(), extension_paths contract

**What to build:** No dead API surface, no hard-coded Chinese strings in the RPC layer, honest API contract.

**Blocked by:** None — can start immediately.

**Status:** done — verified 2026-08-05 (code + tests)

- [x] Move compaction strings (`[压缩上下文...]`) to a constants block with i18n comment
- [x] Wire `get_messages()` to a route or remove it
- [x] Document `extension_paths` parameter contract (first-call-only semantics)
