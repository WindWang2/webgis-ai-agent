# UI Low issue verification — 2026-11-05

Scope: root-owned rows in #1866; every row below has a concrete implementation. Focused tests are tracked in the task validation logs.

| Finding | Disposition | Evidence / resulting behavior |
|---|---|---|
| S7b2-019 | implemented | Artifact center discloses that sorting/type filters apply to loaded artifacts; full loaded set supplies type choices. |
| S7c-017 | implemented | Duplicate of S7g-023 table empty-value sorting; repaired in attribute-table-panel. |
| S7f-005 | implemented | Footprints keep polygon holes/components and open lines separate, discard nonfinite coordinates, and use one scale for both axes. |
| S7f-006 | implemented | Artifact type choices derive from the unfiltered loaded set, preserving a way out of an empty filter. |
| S7f-007 | implemented | Editing lease tracker resets when the connection client identity changes; selected layers reacquire leases. |
| S7f-008 | implemented | Pagination remains cursor mode at its final cursor; explicit has_more=false disables the next offset page. |
| S7f-009 | implemented | Counters use the same visible fact set as the list (data_profile excluded). |
| S7f-010 | implemented | Project/refresh generations reject old memory responses and reset in-flight mutation state on switching projects. |
| S7f-011 | implemented | Model detail/inspection/history accept and transmit the selected model version. |
| S7f-012 | implemented | Spatial catalog uses offset pagination, append/dedup, request generations, and a Load more control. |
| S7f-013 | implemented | Elapsed time updates every second while active jobs are visible; timer is cleaned on inactive/closed panel. |
| S7f-014 | implemented | Repeated warning codes use indexed keys in result detail. |
| S7f-015 | implemented | Clipboard feedback timers are canceled and late clipboard completion is rejected on project change/unmount. |
| S7g-002 | implemented | Aggregate field-required state disables Run/Explain and exposes an accessible explanation. |
| S7g-003 | implemented | Auto-load uses a real loading state rather than a nonfunctional button, and renders blocked-node reasons. |
| S7g-004 | implemented | Tool step keys include index; seconds duration takes precedence over elapsed_ms. |
| S7g-007 | implemented | Explain honors the same filter guard as Run. Limit is an editable string, clamped on blur. |
| S7g-008 | implemented | Geometry objects remain typed instead of guessing from ordinary text prefixes; null/null sort compares equal. |
| S7g-010 | implemented | ModelOps uses a distinct Cpu navigation icon. |
| S7g-014 | implemented | Telemetry labels display coordinates as WGS84, status text is localized, and waveform frame updates touch the SVG path without rerendering the HUD. |
| S7g-017 | implemented | GeoAI normalizes API errors, clears candidates on source/model changes, gates concurrent mutations, and refines using the selected run model/source. |
| S7g-018 | implemented | Basemap provider resolution requires exact id/name/keyword. Project-visible templates require a nonempty project id. |
| S7g-020 | implemented | Timeline rejects old run/request responses and keeps already loaded events with an inline pagination error. |
| S7g-022 | implemented | Removed fake front-end vector address/collection Save. Connection is explicitly server managed; actual connectivity probe and document text are localized. |
| S7g-023 | implemented | Table empty values sort last in both directions; duplicate feature ids have distinct row keys. Skills use declared category or Other. Removed an unconditional Online badge. |
| S7h-008 | implemented | Spotlight is transparent inside its target rectangle; the outer shadow provides the mask. |
| S7h-010 | implemented | Mission actions refresh list/detail/timeline projections instead of resetting the list key. |
| S7h-012 | implemented | Citation titles/ids cannot add definition lines; excerpts are quoted and source boundaries/handling instructions are explicit. This is context hygiene, not a complete prompt-injection security boundary. |
| S7h-013 | implemented | Case-insensitive Unicode regex matches retain original string offsets for highlight spans. |
| S7h-015 | implemented | Tokenizer treats shell # as a comment only at a boundary, distinguishes subtraction/unary minus, and handles numeric radices separately. |
| S7h-016 | implemented | System-message queues support explicit severity; the bridge no longer guesses from localized text. Legacy strings remain informational for compatibility. |
| S7h-017 | implemented | Basemap cards use only the renderer selectedBaseLayer as selection truth. |
| S7h-020 | implemented | System settings tabs implement roving tabindex, arrow/Home/End activation and associated tab panels; surrounding Settings dialog owns modal focus. |
