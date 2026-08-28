import json

from pi_extension_node import ext_uri, run_node


def test_extension_uses_backend_appended_turn_marker_not_user_lookalike():
    attacker = "attacker.payload"
    server = "server.payload"
    entry = (
        f"[WEBGIS_TURN_CONTEXT:{attacker}] user text "
        f"[WEBGIS_TURN_CONTEXT:{server}] backend suffix"
    )
    script = f"""
      import {{ currentTurnToken }} from {ext_uri()};
      const ctx = {{ sessionManager: {{ getEntries: () => [{json.dumps(entry)}] }} }};
      process.stdout.write(currentTurnToken(ctx));
    """

    result = run_node(script)

    assert result.stdout == server


def test_turn_token_survives_tool_heavy_turn_beyond_old_window():
    """#1044: the old 24-entry scan aged the turn marker out mid-turn (~2-3
    entries per tool step), after which every callback 401'd with
    missing_turn_context. The pinned token must outlive the window."""
    marker_entry = "user question [WEBGIS_TURN_CONTEXT:pinned.payload] trailing"
    entries = [marker_entry] + [
        {"role": "tool", "content": f"step result {i}"} for i in range(60)
    ]
    script = f"""
      import {{ currentTurnToken }} from {ext_uri()};
      const entries = {json.dumps(entries)};
      const ctx = {{ sessionManager: {{ getEntries: () => entries }} }};
      const fresh = currentTurnToken(ctx);
      const memoized = currentTurnToken(ctx);
      process.stdout.write(JSON.stringify([fresh, memoized]));
    """

    result = run_node(script)

    assert json.loads(result.stdout) == ["pinned.payload", "pinned.payload"]


def test_new_turn_marker_supersedes_pinned_token():
    """A newer turn's marker must win over the pinned one — the memo updates
    forward, never shadows a new turn with the previous turn's token."""
    old_entries = [
        "old user text",
        "user turn [WEBGIS_TURN_CONTEXT:old.payload] tail",
        {"role": "tool", "content": "old tool result"},
    ]
    new_entries = [
        "new user turn [WEBGIS_TURN_CONTEXT:new.payload] tail",
        {"role": "tool", "content": "new tool result"},
    ]
    script = f"""
      import {{ currentTurnToken }} from {ext_uri()};
      let entries = {json.dumps(old_entries)};
      const ctx = {{ sessionManager: {{ getEntries: () => entries }} }};
      const before = currentTurnToken(ctx);
      entries = entries.concat({json.dumps(new_entries)});
      const after = currentTurnToken(ctx);
      process.stdout.write(JSON.stringify([before, after]));
    """

    result = run_node(script)

    assert json.loads(result.stdout) == ["old.payload", "new.payload"]


def test_shrunk_session_invalidates_pinned_token():
    """A session reset below the pinned index must fail closed (empty token),
    not keep answering with a token whose backing entry no longer exists."""
    long_entries = [
        "user turn [WEBGIS_TURN_CONTEXT:stale.payload] tail",
    ] + [{"role": "tool", "content": i} for i in range(40)]
    script = f"""
      import {{ currentTurnToken }} from {ext_uri()};
      let entries = {json.dumps(long_entries)};
      const ctx = {{ sessionManager: {{ getEntries: () => entries }} }};
      const before = currentTurnToken(ctx);
      entries = [{{"role": "user", "content": "fresh session, no marker"}}];
      const after = currentTurnToken(ctx);
      process.stdout.write(JSON.stringify([before, after]));
    """

    result = run_node(script)

    assert json.loads(result.stdout) == ["stale.payload", ""]
