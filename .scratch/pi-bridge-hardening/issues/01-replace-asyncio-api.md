# 01 — Replace deprecated asyncio API + fix subprocess bufsize

**What to build:** Pi bridge runs on Python 3.12+ without deprecation warnings or subprocess startup crashes.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()` in `_read_responses` and `_send_request`
- [ ] Fix `bufsize=0` with `text=True` in `subprocess.Popen` (change to `bufsize=1` or remove the parameter)
