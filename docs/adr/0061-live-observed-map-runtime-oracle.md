# Live Observed Map is the production runtime oracle

“The map is done” could have meant live MapLibre observation, headless canvas (`mapLoaded` + non-empty), a map-action ACK, or live camera matching last SetView.

**Decision:** Production runtime pass is the live Observed Map for this MapSpec generation: style loaded, no reconcile error, expected layer identities present (visibility-off still counts as present). Gesture camera is never pass/fail. Headless Playwright (`webgis_runtime_validate`) is record-only; it cannot pass the map alone and cannot change a live verdict. ACKs stay on interaction metrics; cartographic PASS must not set `InteractionStateConvergenceRate` to 100. Missing live observation is `not_evaluated`, not pass.
