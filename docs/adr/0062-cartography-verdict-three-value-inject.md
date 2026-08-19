# Next Pi turn gets a three-value Cartography Verdict; pass is not silence

Today `[CARTOGRAPHY_VERDICT]` injects only on unresolved fail. Pass injects nothing, so silence is ambiguous (pass vs no activity vs superseded). Same-turn tool `content` is not the harness verdict.

**Decision:** Auto-inject a tiny current-generation token `pass` | `fail` | `not_evaluated`. `passed_with_warnings` maps to `pass`. Skip no-activity and superseded. Failed / `not_evaluated` checks ride only on non-pass. Omit `overall_passed` from inject and from the status-tool summary (that flag is a metric-gate lie, not cartography). Status tool returns the full stored review; inject stays the token. Mutation `content` stays lifecycle, not harness. User-chrome and observed camera stay out of this block.
