# Frontend issue verification and repair — 2026-11-05

Baseline: `e0e97b462`; branch: `codex/all-issues-deep-review`. This is a live ledger. **Unresolved rows are not completed and must not be closed.**

## High issues

| Issue | Status | Evidence / result |
|---|---|---|
| #1807 | fixed | Memory-only access/refresh credentials; purge legacy tokens; reload signs out until HttpOnly cookie support exists. |
| #1808 | fixed | Exact Mercator viewport bounds, latitude interpolation and export graticule math. |
| #1809 | fixed | Authored filters retained on main, representation and label layers. |
| #1810 | fixed | Closed expression subset filters both SVG compiler twins; unsupported expressions skip layer with diagnosis. |
| #1811 | fixed | Geometry guard uses expression syntax (MapLibre vector tile geometry normalizes Multi* to primitive families). |
| #1812 | fixed | Mutation clients send objects to the shared JSON transport, avoiding double encoding. |
| #1813 | fixed | Inset south latitude clamps symmetrically to inset bounds. |
| #1837 | fixed | Data agent verified cube_store window transform translation/stride scaling and band-subset metadata;56 dedicated,227 raster/profile,210 schema tests passed. |
| #1838 | fixed | Every GeoAI request uses current/restorable real session id and owner token; no fabricated session. |
| #1839 | fixed | Story binds artifact cursor after session ownership validation and clears it on cleanup. |

## Aggregate findings

`fixed` below means implementation completed; aggregate closure also requires final validation of every row.

| Issue | Finding | Status | Evidence / remaining work |
|---|---|---|---|
| #1826 | S7-002 | fixed | Credentials moved to first auth-frame-v1 application frame; backend retains old handshake compatibility. |
| #1826 | S7-003 | fixed | Auth refresh bounded at 10 seconds. |
| #1826 | S7-010 | fixed | Export notification titles use bounded sanitized80-character helper. |
| #1826 | S7a-001 | fixed | Image cache busters omitted for data/blob URLs. |
| #1826 | S7a-002 | fixed | renderer categories normalize mixed keys and deduplicate; empty maps use fallback. |
| #1826 | S7a-003 | fixed | Removal recognizes exact/explicit __ family or same-source legacy children, retaining unrelated prefix siblings. |
| #1826 | S7a-015 | fixed | Frame where combines authored filters for main/representation/label children; missing target skips frame. |
| #1826 | S7a-017 | fixed | Publication notification uses bounded sanitized title and generic failure reason. |
| #1826 | S7a-019 | fixed | ref cache invalidates content_revision; old revision fetch cannot refill; regression added. |
| #1826 | S7a-021 | fixed | pending clear/rollback requires expectedMutationId in session-cursor; style drafts debounced per layer. |
| #1826 | S7a-025 | fixed | worker strips only inlineData/imageRef and preserves source metadata (verified spread). |
| #1826 | S7a-026 | fixed | per-worker error callback set settles all pending requests; WeakSet gives per-result failure evidence consumed by runtime. |
| #1826 | S7a2-002 | fixed | stacked export scales to maximum column sum. |
| #1826 | S7a2-003 | fixed | atlas PDF passes loaded publication font and requested color mode. |
| #1826 | S7a2-004 | fixed | legacy PDF compass uses negative bearing matching live compass. |
| #1826 | S7a2-005 | fixed | Continuous legend uses drawChromeColorbar gradient; legacy thematic copy skipped when same layer. |
| #1826 | S7a3-003 | fixed | TS/Python bounded label text expressions support get/to-string/coalesce/concat/format; unsupported skips with diagnosis. |
| #1826 | S7a3-004 | fixed | TS/Python synthesize authored top-level label into symbol layer with paint/layout. |
| #1826 | S7a3-005 | fixed | Zoom-camera interpolation excludes data-field nodata guard. |
| #1826 | S7a3-006 | fixed | Validator routes pinned MapLibre CDN requests to installed local distribution and records requestfailed. |
| #1826 | S7a4-002 | fixed | early hover/disabled branches remove old labels; plain label registration cleanup retains newly mounted labels; runtime.label regression passes. |
| #1826 | S7a4-003 | fixed | MapLibre ErrorEvent.error.message unwrapped. |
| #1826 | S7a4-004 | fixed | runtime evidence uses internal base-style readiness like runtime. |
| #1826 | S7a4-005 | fixed | runtime-evidence converts native expected paint/layout and symbol-law rules before comparing. |
| #1826 | S7a4-006 | fixed | Label band reset restores every prior boundary/base field before applying fresh bands. |
| #1826 | S7b3-002 | fixed | Data-plane estimator recursively counts geometry coordinate numbers and properties strings. |
| #1826 | S7b3-003 | fixed | Categorical thematic styles get same flat-color protection. |
| #1827 | S7c-002 | fixed | Mercator latitude fractions shared by graticule/anchor/export. |
| #1827 | S7c-003 | fixed | shared computeNiceScale already applied; no fixed list limitation remains. |
| #1827 | S7c-004 | fixed | sketch point circle layer included in render, visibility, opacity and hit queries. |
| #1827 | S7c-005 | fixed | Keyboard map actions ignore modifier keys/select/listbox. |
| #1827 | S7c-006 | fixed | Floating panel zIndex capped at 49. |
| #1827 | S7c-007 | fixed | Chart linked brush intersects geometryBBox with selection bounds; no representative-point approximation. |
| #1828 | S7b-002 | fixed | Default project/dataset/workflow lists follow has_more with explicit200-page/no-progress limits; explicit paging preserved. |
| #1828 | S7b-003 | fixed | Await optional analysis projection inside try/catch. |
| #1828 | S7b-004 | fixed | Reconciliation drops responses from previous session. |
| #1828 | S7b-005 | fixed | XHR abort rejects and removes external signal listener on terminal events. |
| #1828 | S7b-006 | fixed | workflow execute deadline plus15s; geocompute sync315s request budget. |
| #1828 | S7b2-001 | fixed | GC execution has a separate abort controller. |
| #1828 | S7b2-002 | fixed | GC execute requires reviewed plan_fingerprint; backend409 clears plan and asks repreview. |
| #1828 | S7b2-003 | fixed | settle issue sequence assigned before await and checked afterward. |
| #1828 | S7b2-004 | fixed | Cluster terminal/not-found states persist across visibility changes. |
| #1828 | S7b2-005 | fixed | clarification completion/catch/timeout enforce originating session. |
| #1828 | S7b2-006 | fixed | Selection/SSE/table share resolveFeatureId and canonical key order incl FID. |
| #1828 | S7b2-007 | fixed | Project quality data resets on project change and rejects stale responses. |
| #1828 | S7b2-008 | fixed | project selection clears detailLoading/error; absent run clears loading. |
| #1848 | S7b4-001 | fixed | Exported provider registry honors air_gapped; empty local registry yields blank style and disabled switcher. |
| #1848 | S7b4-002 | fixed | Cross-session index sessionStorage key scoped by user; auth change purges old user index. |
| #1848 | S7b4-003 | fixed | Undo reorder uses current objects by id, preserving current styles and newly added layers. |
| #1848 | S7b4-004 | fixed | Style/reorder/filter and cartographic runtime repairs reject locked resolved targets atomically. |
| #1848 | S7b4-005 | fixed | Scheduler shared waiter cancellation settles only own caller; regressions cover concurrent waiter abort. |
| #1849 | S7d-001 | fixed | Citation layers bridge to store plus runtime ids; API evidence tests pass. |
| #1849 | S7d-002 | fixed | card displays only server-reported speed, supports cycling/transit and unknown modes. |
| #1849 | S7d-003 | fixed | descriptor/query/explain responses guarded by selected dataset id. |
| #1849 | S7d-004 | fixed | opacity slider disabled for locked rows; mutation entry guards lock state. |
| #1849 | S7d-005 | fixed | A3/A4 both lower 300dpi to supported 150. |
| #1850 | S7e-002 | fixed | Stable callback ref; cancelled resolver never consumes hint. |
| #1850 | S7e-003 | fixed | publish/revoke disabled without projectId; early exit closes confirm. |
| #1850 | S7e-004 | fixed | Missing owner invalidates request generation and aborts old request. |
| #1850 | S7e-005 | fixed | catalog lineage target auto-runs inspect. |
| #1850 | S7e-006 | fixed | independent lineage/scrub results retained on partial failure. |
| #1850 | S7e-007 | fixed | Wallboard panels stay mounted while hidden, retaining sampled charts across rotation. |
| #1850 | S7e-008 | fixed | Run cancel asks explicit confirmation, disables pending action and surfaces failure. |
| #1850 | S7e-009 | fixed | Later retry attempt restores running state and clears prior end. |
| #1851 | S7f-001 | fixed | Dataset switch resets audit input; repair requires same dataset identity. |
| #1851 | S7f-002 | fixed | Partial sample disclosed; full-dataset repair disabled for partial input. |
| #1851 | S7f-003 | fixed | Lineage edges use actual parent/child ids at each depth. |
| #1851 | S7f-004 | fixed | Dataset search remains visible while filter is nonempty. |
| #1852 | S7g-001 | fixed | Inspector keyed by selected dataset id. |
| #1852 | S7g-005 | fixed | Console run/explain response generations tied to current target. |
| #1852 | S7g-006 | fixed | Materialization uses saved successful result spec. |
| #1852 | S7g-009 | fixed | mobile bottom sheet renders lakehouse/ops/cockpit/market/modelops. |
| #1852 | S7g-012 | fixed | spec styles merge into180ms draft submission; stale failures cannot rollback later changes; rollback derives committed canonical paint. |
| #1852 | S7g-016 | fixed | GeoAI responsive image/overlay share size and pointer coordinates normalize to preview. |
| #1852 | S7g-019 | fixed | AgentRunPanel subscribes identity changes and refreshes on done; clears graph on session change; AnalysisGraphPanel clears graph on session change. |
| #1852 | S7g-021 | fixed | action busyRef prevents same-tick duplicates; buttons disabled until completion. |
| #1852 | S7h-001 | fixed | lasso finish stops pointer bubbling and near-duplicate doubleclick points skipped. |
| #1852 | S7h-002 | fixed | widget reply dismisses card. |
| #1852 | S7h-003 | fixed | Cockpit execution/session/mission projections show explicit loading/error status. |
| #1852 | S7h-004 | fixed | ArrayBuffer decodes fatal UTF8 then fatal GB18030. |
| #1852 | S7h-005 | fixed | large content preview limited32KiB readonly; byte cap before submission. |
| #1866 | S7-004 | fixed | Geolocation no longer runs on mount; TopBar explicit request button calls requestLocation. |
| #1866 | S7-006 | fixed | abort listeners removed on successful sleep and abort. |
| #1866 | S7-007 | fixed | canvas action report has10s timeout and dev diagnostics. |
| #1866 | S7-008 | fixed | markdown URLs parsed with URL and protocol whitelist. |
| #1866 | S7-009 | fixed | autofill respects type presence even when explicit component disabled. |
| #1866 | S7-011 | fixed | PNG upload uses asynchronous canvas.toBlob; data URL generated only inside SVG fallback. |
| #1866 | S7-012 | fixed | All browser blob download consumers share triggerBlobDownload with delayed1s URL revoke. |
| #1866 | S7-013 | fixed | Export chrome, notifications and removal journals use locale catalog keys; export messages carry explicit levels. |
| #1866 | S7-014 | fixed | negative legend values use absolute magnitude for formatting. |
| #1866 | S7a-004 | fixed | graticule label precision derives from interval. |
| #1866 | S7a-005 | fixed | Heatmap opacity uses ??; empty/single palettes fallback or duplicate stops. |
| #1866 | S7a-006 | fixed | Async viewport update checks source identity and catches worker rejection. |
| #1866 | S7a-007 | fixed | Orphan helper default uses explicit __ separator rather than truncating arbitrary suffix. |
| #1866 | S7a-008 | fixed | Process fill-color honors authored color with separate fill-opacity. |
| #1866 | S7a-009 | fixed | empty raster grid declines export; palette and nodata mask lengths checked. |
| #1866 | S7a-010 | fixed | MVT ref path and session query encoded independently. |
| #1866 | S7a-011 | fixed | Vector PDF conversion logs bounded dev warning; caller retains raster fallback evidence. |
| #1866 | S7a-012 | fixed | WeakSet reserves in-flight upload before capture; upload deadline30s and finally releases. |
| #1866 | S7a-013 | fixed | Raster tile grid caps512 requests by lowering zoom; wraps tile x while preserving world placement. |
| #1866 | S7a-014 | fixed | Pure lib number formatter removes client-component dependency; divergent midpoint honors center. |
| #1866 | S7a-016 | fixed | polygon longitude deltas normalized across antimeridian. |
| #1866 | S7a-018 | fixed | Vector SVG honors chrome enablement; truncates overflowing legend entries with diagnosis; bivariate omission disclosed. |
| #1866 | S7a-020 | fixed | In-flight task only removes its own map entry; superseded session does not emit. |
| #1866 | S7a-022 | fixed | MapSpec mutation session path segments encoded. |
| #1866 | S7a-023 | not_a_bug | Current compiler/paint bridge explicitly define canonical opacity as hillshade rendering strength; live-spec uses same native hillshade-exaggeration key, never nonexistent hillshade-opacity. |
| #1866 | S7a-024 | not_a_bug | Mutation helpers carry different payload/CAS flows; existing pending IDs reject superseded completions. Consolidation is an optional abstraction, no observable defect. |
| #1866 | S7a-027 | fixed | Inline token comparison short-circuits differing content_revision before recursive equality. |
| #1866 | S7a2-006 | fixed | Watermark font includes scaled px unit. |
| #1866 | S7a2-007 | fixed | Export metadata correctly labels display CRS EPSG3857. |
| #1866 | S7a2-008 | fixed | Bivariate n rounded then clamped before indexing. |
| #1866 | S7a2-009 | fixed | Pie totals include positive slices only; empty pie discloses no data. |
| #1866 | S7a2-010 | fixed | Chart extrema use reduce, avoiding argument limits. |
| #1866 | S7a2-011 | fixed | Inset bbox unwraps east+360; world copies align shared Mercator projection. |
| #1866 | S7a3-007 | fixed | Cluster source config merges layer-authored values; cluster children require source.cluster true. |
| #1866 | S7a3-008 | fixed | label truncation uses Unicode code points. |
| #1866 | S7a3-009 | fixed | SVG extent considers visible layer sources only; invalid geographic coordinates skipped and diagnosed once per layer in TS/Python. |
| #1866 | S7a3-010 | fixed | Approximation diagnostics emitted once per layer; configured labelBudget reported. |
| #1866 | S7a3-011 | fixed | TS/Python input coercion/coalesce, case and array match labels supported. |
| #1866 | S7a3-012 | fixed | Validator malformed URI returns400; tracing stopped in finally; navigation/load/idle share deadline. |
| #1866 | S7a3-013 | fixed | step default permits one stop; stop ordering compares numbers. |
| #1866 | S7a4-007 | fixed | Paint/layout rejected incremental fallback marks z sync required. |
| #1866 | S7a4-008 | fixed | Label-only and full add paths both exclude raster/heatmap/background/hillshade. |
| #1866 | S7a4-009 | fixed | Unresolved ref source reports pending; inline feature count preferred. |
| #1866 | S7a4-010 | fixed | Categorical match deduplicates and normalizes mixed key types. |
| #1866 | S7a4-011 | fixed | Expression sanitizer chooses numeric fallback for numeric paint keys. |
| #1866 | S7a4-012 | fixed | Unwired label grid and tests moved to explicitly experimental namespace. |
| #1866 | S7a4-013 | fixed | Nested object keys canonicalized before label-only equality. |
| #1866 | S7b-007 | fixed | Story export sends optional mapspec in JSON body. |
| #1866 | S7b-008 | fixed | Lakehouse writes invalidate lakehouse cache; map product mutations and snapshot clone invalidate lists. |
| #1866 | S7b-009 | fixed | ACK401 attempts refresh and bounded transient redelivery when successful. |
| #1866 | S7b-010 | fixed | Content-Disposition supports UTF8 filename* and safe malformed percent fallback. |
| #1866 | S7b-011 | fixed | SSE handles CR,LF,CRLF including split trailingCR and final line. |
| #1866 | S7b-012 | fixed | Restore priority bounds use shared exact Mercator center/zoom projection with viewport dimensions. |
| #1866 | S7b-013 | fixed | Committed remote patch updates explicit __ children. |
| #1866 | S7b-014 | not_a_bug | API type-only component imports are erased by TypeScript and create no runtime dependency. Page adapters intentionally normalize legacy array/envelope shapes; actual truncation repaired separately. |
| #1866 | S7b-015 | fixed | API project/run/session/task/fact path identifiers encoded. |
| #1866 | S7b-016 | fixed | FastGET hit touches LRU insertion order. |
| #1866 | S7b2-009 | fixed | Dialog focus uses capture listener and open-dialog stack, only top modal handles Escape. |
| #1866 | S7b2-010 | fixed | Owner token changes invalidate stuck-run mutation epoch and state. |
| #1866 | S7b2-011 | fixed | Only404 negatively cached; shared request not aborted by consumer cleanup. |
| #1866 | S7b2-012 | fixed | Lakehouse history in user-scoped sessionStorage, validates records, auth change clears prior user. |
| #1866 | S7b2-013 | fixed | Virtual start clamps to current item count; nonpositive row heights guarded. |
| #1866 | S7b2-014 | fixed | Additive no-id selection retains previous ids. |
| #1866 | S7b2-015 | fixed | Workspace/map bridge session path segments encoded. |
| #1866 | S7b2-016 | fixed | Session asset refresh checks originating aborted signal before dispatch. |
| #1866 | S7b2-017 | fixed | Cancelled job IDs absent in server list removed. |
| #1866 | S7b2-018 | fixed | Empty project aborts request, increments generation, resets loading. |
| #1866 | S7b2-019 | fixed | Artifact center discloses that sorting/type filters apply to loaded artifacts; full loaded set supplies type choices. |
| #1866 | S7b2-020 | fixed | DMS rounds total seconds before splitting; degree format rounds. |
| #1866 | S7b2-021 | fixed | North arrow no longer fabricates dipole approximation; only explicit finite declination rendered. |
| #1866 | S7b2-022 | fixed | Chart register/unregister use same64-char normalized id. |
| #1866 | S7b2-023 | fixed | Unused duplicate search drawer store deleted; canonical hook remains. |
| #1866 | S7b3-004 | fixed | Isolate rechecks current layer visibility before each toggle. |
| #1866 | S7b3-005 | fixed | Layer data matches exact/explicit __ identity only. |
| #1866 | S7b3-006 | fixed | raster heatmap accepts url and preserves opacity0. |
| #1866 | S7b3-007 | fixed | Polygon annotation label averages unclosed ring vertices. |
| #1866 | S7b3-008 | fixed | Quote-aware SQL comment scanner preserves literals and checks remaining statements. |
| #1866 | S7b3-009 | fixed | Chart cache epoch prevents late refill after reset. |
| #1866 | S7b3-010 | fixed | Viewport flush synchronously publishes pending context; reset invalidates before flushing. |
| #1866 | S7b3-011 | fixed | Layer ops and style intents share new style-paint projection incl width. |
| #1866 | S7b3-012 | fixed | Gesture arbitration30s failsafe clears missing gesture end. |
| #1866 | S7b3-013 | fixed | Session anchors store user id; restore requires matching identity; auth changes clear anchor. |
| #1866 | S7c-008 | fixed | Toolbar pitch derives debounced actual map view state. |
| #1866 | S7c-009 | fixed | Measured polygon label averages unclosed vertices. |
| #1866 | S7c-010 | fixed | Cancelled asynchronous focus clears fit marker so effect can retry. |
| #1866 | S7c-011 | fixed | North-up reset only on3D-to2D transition, preserving2D bearing across readiness/scene updates. |
| #1866 | S7c-012 | fixed | Map commands settle timeout after300s and release action guard on finish. |
| #1866 | S7c-013 | fixed | POI info keyed by click coordinates. |
| #1866 | S7c-014 | fixed | Shared prioritized Escape dispatcher handles only top eligible map tool and defers to modal dialogs. |
| #1866 | S7c-015 | fixed | Comparison and primary map ownerToken priority matches. |
| #1866 | S7c-016 | fixed | Map readout attribution derives current provider, removing fixed OSM claim. |
| #1866 | S7c-017 | fixed | Duplicate of S7g-023 table empty-value sorting; repaired in attribute-table-panel. |
| #1866 | S7c-018 | fixed | Map recovery/export watermark use translations; raw renderer error no longer shown. |
| #1866 | S7c-019 | fixed | Repair approve disabled absent integer revision; failed reject leaves error card. |
| #1866 | S7d-006 | fixed | Unknown status falls back pending metadata. |
| #1866 | S7d-007 | fixed | Historical think uses message isThinking flag. |
| #1866 | S7d-008 | fixed | LISA/isochrone cards no longer guess first layer. |
| #1866 | S7d-009 | fixed | Citation remark plugin rewrites text AST only, skipping code and existing links. |
| #1866 | S7d-010 | fixed | Chart label uses current theme; nonnegative widths; missing heat matrix neutral gray and no-data text. |
| #1866 | S7d-011 | fixed | Clarification submission requires each question answered or waived. |
| #1866 | S7d-012 | fixed | Move-out and cross-group drops enter doc undo history. |
| #1866 | S7d-013 | fixed | Virtual layer secondary actions use overlaid menu, preserving fixed row height. |
| #1866 | S7d-014 | fixed | Materialization updates _refId on existing layer. |
| #1866 | S7d-015 | fixed | Version reload preserves valid selected comparison; lifecycle failures displayed error notice. |
| #1866 | S7d-016 | fixed | Missing project does not mount child asset/memory/version panels; create disabled pending. |
| #1866 | S7d-017 | fixed | Export history action says remove from list, matching local list-only behavior. |
| #1866 | S7f-005 | fixed | Footprints keep polygon holes/components and open lines separate, discard nonfinite coordinates, and use one scale for both axes. |
| #1866 | S7f-006 | fixed | Artifact type choices derive from the unfiltered loaded set, preserving a way out of an empty filter. |
| #1866 | S7f-007 | fixed | Editing lease tracker resets when the connection client identity changes; selected layers reacquire leases. |
| #1866 | S7f-008 | fixed | Pagination remains cursor mode at its final cursor; explicit has_more=false disables the next offset page. |
| #1866 | S7f-009 | fixed | Counters use the same visible fact set as the list (data_profile excluded). |
| #1866 | S7f-010 | fixed | Project/refresh generations reject old memory responses and reset in-flight mutation state on switching projects. |
| #1866 | S7f-011 | fixed | Model detail/inspection/history accept and transmit the selected model version. |
| #1866 | S7f-012 | fixed | Spatial catalog uses offset pagination, append/dedup, request generations, and a Load more control. |
| #1866 | S7f-013 | fixed | Elapsed time updates every second while active jobs are visible; timer is cleaned on inactive/closed panel. |
| #1866 | S7f-014 | fixed | Repeated warning codes use indexed keys in result detail. |
| #1866 | S7f-015 | fixed | Clipboard feedback timers are canceled and late clipboard completion is rejected on project change/unmount. |
| #1866 | S7g-002 | fixed | Aggregate field-required state disables Run/Explain and exposes an accessible explanation. |
| #1866 | S7g-003 | fixed | Auto-load uses a real loading state rather than a nonfunctional button, and renders blocked-node reasons. |
| #1866 | S7g-004 | fixed | Tool step keys include index; seconds duration takes precedence over elapsed_ms. |
| #1866 | S7g-007 | fixed | Explain honors the same filter guard as Run. Limit is an editable string, clamped on blur. |
| #1866 | S7g-008 | fixed | Geometry objects remain typed instead of guessing from ordinary text prefixes; null/null sort compares equal. |
| #1866 | S7g-010 | fixed | ModelOps uses a distinct Cpu navigation icon. |
| #1866 | S7g-011 | fixed | TopBar animation writes DOM ref rather than frame-level React setState. |
| #1866 | S7g-013 | fixed | Opacity commit only changed non-null draft. |
| #1866 | S7g-014 | fixed | Telemetry labels display coordinates as WGS84, status text is localized, and waveform frame updates touch the SVG path without rerendering the HUD. |
| #1866 | S7g-017 | fixed | GeoAI normalizes API errors, clears candidates on source/model changes, gates concurrent mutations, and refines using the selected run model/source. |
| #1866 | S7g-018 | fixed | Basemap provider resolution requires exact id/name/keyword. Project-visible templates require a nonempty project id. |
| #1866 | S7g-020 | fixed | Timeline rejects old run/request responses and keeps already loaded events with an inline pagination error. |
| #1866 | S7g-022 | fixed | Removed fake front-end vector address/collection Save. Connection is explicitly server managed; actual connectivity probe and document text are localized. |
| #1866 | S7g-023 | fixed | Table empty values sort last in both directions; duplicate feature ids have distinct row keys. Skills use declared category or Other. Removed an unconditional Online badge. |
| #1866 | S7h-006 | fixed | Upload success clears title. |
| #1866 | S7h-007 | fixed | Tour Enter excludes native buttons. |
| #1866 | S7h-008 | fixed | Spotlight is transparent inside its target rectangle; the outer shadow provides the mask. |
| #1866 | S7h-009 | fixed | Hint unseen memoized; count from current known hints. |
| #1866 | S7h-010 | fixed | Mission actions refresh list/detail/timeline projections instead of resetting the list key. |
| #1866 | S7h-011 | fixed | Login errors use describeApiError. |
| #1866 | S7h-012 | fixed | Citation titles/ids cannot add definition lines; excerpts are quoted and source boundaries/handling instructions are explicit. This is context hygiene, not a complete prompt-injection security boundary. |
| #1866 | S7h-013 | fixed | Case-insensitive Unicode regex matches retain original string offsets for highlight spans. |
| #1866 | S7h-014 | fixed | Code blocks retain leading indentation. |
| #1866 | S7h-015 | fixed | Tokenizer treats shell # as a comment only at a boundary, distinguishes subtraction/unary minus, and handles numeric radices separately. |
| #1866 | S7h-016 | fixed | System-message queues support explicit severity; the bridge no longer guesses from localized text. Legacy strings remain informational for compatibility. |
| #1866 | S7h-017 | fixed | Basemap cards use only the renderer selectedBaseLayer as selection truth. |
| #1866 | S7h-018 | fixed | Pointercancel resets sketch gesture. |
| #1866 | S7h-019 | fixed | Aborted explorer no active progress. |
| #1866 | S7h-020 | fixed | System settings tabs implement roving tabindex, arrow/Home/End activation and associated tab panels; surrounding Settings dialog owns modal focus. |
| #1866 | S7i-002 | fixed | Chapter artifact rejection clears chart. |
| #1866 | S7i-003 | fixed | Missing active chapter stops playback. |
| #1866 | S7i-004 | fixed | Unseen chapters sort after persisted ordered chapters. |
| #1866 | S7i-005 | fixed | Chapter override version and object validated. |
| #1866 | S7i-006 | fixed | Route error translated, root fallback bilingual and avoids raw exception. |
| #1866 | S7i-007 | fixed | Per-request CSP nonce protects theme and Next scripts with strict-dynamic; no script unsafe-inline/eval in production; build and browser injection test pass. |
| #1866 | S7i-008 | fixed | HSTS opt-in ENABLE_HSTS only; HTTPS edge deployment controls it. |
| #1866 | S7i-009 | fixed | Worker route caches installed module content and sends revalidated1h client cache. |
| #1866 | S7i-010 | fixed | Page messages use canonical ChatStore ChatMessage type; restore effect declares stable deps. |

## Validation

- Focused Vitest: 7 files / 92 tests pass (auth, API encoding, SVG filters, adapter, GeoAI, Mercator math).
- Python SVG regression invocation initially timed out in config default-host DNS validation. Retrying with public-IP endpoint configuration.
- Final full frontend suite, eslint and both TypeScript configurations pending.
- Existing E2E auth helpers that read localStorage need migration to in-page memory token access/UI login.
- No screenshot-based UI audit performed by this worker; parent owns final UI review.

## Product behavior change

Credentials no longer survive reload or new tabs. Persisted account metadata never authenticates a user. This removes the persistent credential exposure in #1807; a future backend HttpOnly refresh cookie can restore silent reload sign-in.

## Continuation validation (2026-11-05)

- Installed independent worktree frontend dependencies with pnpm frozen lockfile; original workspace untouched.
- Fresh initial full suite: 518 files, 4840 tests, 4 failures: outdated expectations/mock fixtures, label cleanup bug, and one load-sensitive perf budget. Real failures fixed; current focused batches 126 + 19 + 137 + 56 tests passed. Final suite rerun still pending as remaining findings are being handled.
- Typecheck both configurations passed before last Low batch; rerunning after changes. ESLint had only viewport external-store dependency warning; explicit dependency read fixed.
- #1694 ARC-06: translator/renderer/user-mutation/copilot logs use devOnly. API-03: terminal callback releases send gate, per-send generation prevents old finally unlocking next send; regression passes.

- Later focused Low batches: 56,89,44 tests pass. Latest style/worker batch and typecheck in progress. No Low or Medium aggregate closed.

## Integration validation (ongoing)

- Production build passed with nonce Proxy and all routes dynamic. Per-request script nonces checked on22/22 SSR scripts. A real Chromium HTML-parser probe blocked a non-nonced script, allowed the same script with the current nonce, and rendered43 interactive buttons.
- Current full frontend rerun: `/tmp/frontend-final-all2.log`; typecheck39 clean. Initial rerun had only localized ExportMask expectations, now corrected and3 focused tests pass.
- Catalog/ABI/conformance/category/diagnostic/SVG Python batch94 pass; new SVG label/coordinate/expression3 pass; sampledJenks endpoint2 regressions pass.
- CSP deliberately uses dynamic rendering and keeps inline style support for React style attributes. Duplicate external Google-font CSS import removed; next/font serves bundled fonts.

Final source checks: typecheck39 passed; final production rebuild passed. Full frontend run after repairs reached515/519 files with6 expected-UI-text fixture failures across4 files; those4 files were corrected during the run and all focused checks passed (mask3, frame13, disclosure/ESLint26). Further compiler diagnostic forwarding regression passed6 tests. Parent owns the last all-suite integration pass.

Final frontend integration:519 files run;518 passed,4851 tests passed,1 expected fail,1 stale gallery success fixture selected credential-gated Carto. Fixture migrated to usable OSM and16tests passed; compiler/carto-ir/gallery35files459passed+1expectedfail afterward. Final tsc/lint pass; production build pass and3108 restarted on session66478. Python compiler integration pass after replacing style-abi logger path alias with relative import.

## Canonical #1921 S7-004 — fixed (distinct from #1866 geolocation row)

Backend inset contract accepts west>east and rejects invalid latitude/nonfinite values (`tests/unit/test_review_core_medium_regressions.py::test_inset_accepts_wrapped_dateline_bbox`). `geo-anchor.ts::bboxToBounds` unwraps `[170,-20,-170,-10]` to east=190; `unwrapLongitude` and `alignWrappedBounds` are consumed by both live `components/map/map-components/inset-map.tsx` and PNG `lib/map-kit/export-chrome.ts`. The main viewport and -175 longitude align into the same continuous world copy.

Fresh verification also caught and fixed a remaining SVG lane: `app/lib/cartography/svg_marginalia.py::render_inset_locator` now unwraps context/main extents and aligns world copies; `mapspec_to_svg.py` respects the authored inset.options.bbox instead of substituting an automatic extent. `tests/unit/test_review_inset_dateline.py` checks exact rectangle coordinates and compiler preservation of the authored extent. `/tmp/inset-layout-final-all.log`: **50 passed**, including publication chrome/vector PDF, backend inset contract and shared layout golden. `/tmp/api-low-ui-focused.log`: **36 passed**, including the exact raw wrapped-bbox case and frontend IR/health gating.

Shared layout content follow-up for core #1951 S13-L7: optional content is preserved/deep-copied in TS LayoutIR, with a shared Python-produced `layout_ir/content.json` fixture consumed by both test suites.
