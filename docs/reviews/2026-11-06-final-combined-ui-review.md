# Final combined UI review

Reviewed the combined production Next build with the local authenticated API
fixture on 2026-11-06. The browser ran the real MapLibre page at its default
1280×720 viewport and at 390×844. Screenshots were captured before evaluating
the layout. This supplements the earlier project, authentication and UI audits;
it is not a production deployment or a claim to have exercised every screen.

## Findings fixed and verified

| Finding | Final behavior | Evidence |
| --- | --- | --- |
| P2: the settings drawer's 400px floor and content minimum width clip controls on a 390px viewport | Drawer width is capped at the viewport; the content flex item can shrink, mobile padding leaves room for the close button, and logout remains readable | Real login succeeds. The complete account card, wrapped identity, logout and close controls are visible in the after screenshot. Settings regression: 6 files, 27 passing tests; focused ESLint and production build pass. |
| P2: an empty session is presented indefinitely as loading, including after login | Existing idle state displays “尚无地图情境” / “No map context yet”; real pending requests retain their loading state | Three red reproductions covered anonymous, logged-in and detached sessions. Final status-bar file: 14 passing tests. Real browser shows the neutral state after page load. No hook/backend behavior was changed. |
| P2: status labels occur twice in the accessibility tree | The redundant screen-reader-only copy is removed; visible chips, titles, `role=status` and polite live updates remain | Error and idle labels occur once; running, verifying and retryable details remain present. Final browser AX shows one idle phrase. This is not a full screen-reader or WCAG certification. |
| Earlier Escape fix requires verification against the combined browser build | First Escape clears a nonempty search and retains the gallery; second Escape closes it and restores focus | Native input searched for “人口” and showed its matching template. After first Escape the input was empty and the dialog remained. After second Escape the dialog was absent and focus was on the template-library button. |

The real basemap renders with its scale and attribution visible. The narrow
gallery keeps its search, close control, scrollable categories and pagination
available. Desktop focus restoration is visible in the final screenshot.

Refreshing the page requires signing in again under the existing memory-only
credential policy documented in `frontend/lib/auth/tokenStore.ts`; the observed
return to the login form is consistent with that policy. The local fixture's
temporary signing key was replaced and the fixture restarted during this review.
No user credentials or original-workspace configuration were changed.

## Screenshot evidence

- [Initial combined desktop](assets/2026-11-06-ui/17-combined-desktop.png).
- [390px account before correction](assets/2026-11-06-ui/18-combined-narrow-account.png).
- [390px account after correction](assets/2026-11-06-ui/19-combined-narrow-account-fixed.png).
- [Gallery retained after first Escape](assets/2026-11-06-ui/20-combined-gallery-first-escape.png).
- [Final desktop idle state and restored focus](assets/2026-11-06-ui/21-combined-desktop-idle-final.png).

The full combined frontend run before the last narrow-layout/status corrections
passed 532 files, 4,976 tests and one expected failure, with coverage thresholds
enabled. The final UI corrections were separately tested and rebuilt; final
whole-suite results are recorded with the landing evidence. The browser viewport
override was reset after testing.

No precise-location permission was granted, paid-model analysis was not invoked,
and no custom 3D asset, terrain GPU rendering or PDF pixel-level browser journey
was accepted from these screenshots. Those limits are separate from the backend
scientific, export and scene lifecycle regressions documented in the PR reviews.
