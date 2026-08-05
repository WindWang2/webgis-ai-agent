# 04 — Extract USE_NEW_AGENT guard helper

**What to build:** Adding new Pi-backed routes requires one conditional check, not three copies.

**Blocked by:** None — can start immediately.

**Status:** done — verified 2026-08-05 (code + tests)

- [x] Create `_use_pi_bridge()` helper in `chat.py` returning `USE_NEW_AGENT and pi_bridge is not None`
- [x] Replace three copies of the guard in `chat_completions`, `chat_stream`, `clear_session`
