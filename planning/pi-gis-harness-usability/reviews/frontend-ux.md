# Frontend UX review — map interaction and agent status loop

Reviewer: read-only adversarial pass on `zcode/pi-gis-harness-usability-20261106` at `a678d0005` (`origin/master`).
Scope: user-visible defects in Pi/SSE → chat store → agent status → MapSpec runtime → MapLibre → observation POST → ACK, plus selection → next turn, cancel/late events, export, style locks, and empty-map copy.
Out of scope: redesign, and re-litigation of merged #1974 / #1975 unless production wiring is broken. Those paths were not found broken here.

Line numbers are from files opened in this pass.

---

## Event chain

```
Pi turn (sse_event_id_scope)
  app/api/routes/chat.py:1374, :1456
  app/utils/sse.py:125-140          id: stamped inside the scope; omitted outside it
        │
        ▼
parseSSEStream → streamChat
  frontend/lib/api/sse-stream-parser.ts:27-32
  frontend/lib/api/chat.ts:171-218   body is message + session_id + map_state
                                     (+ optional project_id / canvas_actions)
                                     No mapspec_revision, fingerprint, selection set,
                                     or display-confirmation id.
        │
        ▼
useMapBridge.send
  frontend/lib/hooks/useMapBridge.ts:302-659
  :364  abort / streamEpoch gate (per event, after yield)
  :400-417  id dedup (only when event.id is present)
  :428-449  aiStatus: done | error | idle(task_cancelled) | thinking | acting
  :459-559  step_result → dispatchAction (or deferred store-mounted ack)
  :565-587  onEvent, then reportTerminal(..., 'succeeded', { store_mounted: true })
  :671-679  cancel() = abort + synthetic task_cancelled. Does not clear the
            map-action queue, layer fetches, or in-flight observations.
        │
        ▼
useSSEStream.onEvent
  frontend/lib/hooks/use-sse-stream.ts:579-986
  :583-588  drop cross-session events
  :611-621  any event with mapspec → commitMapSpecDocument + syncSpecLayersToStore
  :661-681  tool_call → appendToolCall (running)
  :682-831  step_result → mark completed, addLayer, chip, async requestRefFC
  :867-888  map_finalization → dispatch MAP_FINALIZATION (camera) + optional toast
  :899-907  task_cancelled → finalize tool rows as 已取消
  :936-942  done/task_complete → finalize leftover rows as 未收到执行结果,
            clear isThinking
        │
        ├─ agent chrome
        │    top-bar.tsx:86-101          aiStatus → 感知中 / 执行中 / 完成
        │    agent-run-panel.tsx:74-137  same aiStatus, plus a manual graph refresh
        │    tool-call-card.tsx:229,372-389  completed row / 「N 个工具调用完成」
        │    message-reducer.ts:397-406  layerAdded chip on the assistant bubble
        │
        ▼
committed MapSpec + HUD layers
  session-cursor.ts:149-170   commit if revision missing OR revision >= cursor
  live-spec.ts:155-228        compose = committed paint/visibility ⊕ pending
                              visible/opacity only. Lock set is not an input.
  map-panel.tsx:875-937       reconcileAsync → issueCartographicObservation
        │
        ▼
POST /cartographic-observation
  use-cartographic-observation.ts:293-315
  :316-394  ACK: repair_action → dispatchAction; runtime_repair.mapspec →
            commitMapSpecDocument (revision optional)
```

Selection is a side channel, not part of the turn body:

```
click  use-feature-selection.ts:60-133  setSelectedFeature + publishSelection
                                        + emitFeatureSelected
brush  map-panel.tsx:1405-1478          publishSelection + emitFeatureSelected
                                        does NOT set selectedFeature
        │
        ▼
bus.ts:73-95 → outbox debounce 500ms (outbox.ts:70, :257-262)
        │
        ▼
ingest ACK → notifySituationInvalidated → situation bar refresh
situation-status-bar.tsx:102-169   chips come from the server snapshot,
                                   not from the local selection store
```

Next turn (`use-sse-stream.ts:1129-1214`) reads HUD `selectedFeature` and the live viewport. It does not flush the interaction outbox, does not wait for an ingest ACK, and does not attach the brush selection or a confirmed map generation.

Export is a third channel. `export_map` (`exportCommands.ts:76-145`) snapshots the next canvas frame. `export_vector_pdf` (`publication-export.ts:101-166`) posts the committed spec plus pending visibility. Neither reads `apply_ack`, `observation_accepted`, or the display-confirmation card.

---

## Confirmed findings

### UX-01 — Cancel, or the next question, still moves the map

Severity: high.

Reproduction:

1. Send a question that returns a `geojson_ref` layer (POI / buffer / thematic).
2. Before the feature fetch resolves, press cancel, or send a second question.
3. Watch the canvas.

What happens:

- `cancel()` (`useMapBridge.ts:671-679`) aborts the SSE reader and synthesizes `task_cancelled`. The chat row says 已取消 (`use-sse-stream.ts:899-907`). The composer frees.
- The map-action queue is cleared only on session switch (`useMapBridge.ts:251-262`, `map-action-context.tsx:362-374`). A `fly_to` / `MAP_FINALIZATION` / style command already queued keeps running.
- The geojson fetch is not aborted. `layerFetchAbortRef` is aborted on session change and unmount only (`use-sse-stream.ts:439-466`). The completion handler (`:782-812`) returns early for `cancelled` or a different `sessionId`, then `updateLayer` + `focusLayer`. Same session, next turn: the old fetch still writes features and flies the camera.
- The comment at `:769-770` (“取消后的迟到完成由调度器 stale 闸 + session 守卫”) overclaims. `cancelSession` (`data-plane/scheduler.ts:338`) is session-scoped. There is no turn generation on this promise.
- An in-flight cartographic observation is aborted only by a newer observation or unmount (`use-cartographic-observation.ts:106-120, :290-291`). Chat cancel does not touch it. A late `repair_action` still `dispatchAction`s (`:344-368`). A late `runtime_repair.mapspec` still `commitMapSpecDocument`s (`:386-393`). If that payload omits `mutation_revision`, the commit is unconditional (`session-cursor.ts:155-161`: the old-revision guard runs only when a number is present).

User impact: the user stopped the agent, or asked a new question, and the previous answer still pans the map and paints layers into the new turn. The chat says 已取消 / shows the new question. The canvas does not agree.

Root cause: turn lifetime is the SSE `AbortController` plus `streamEpoch`. Map mutations that have already left that loop (queued commands, ref fetches, observation repairs) are session-scoped, not turn-scoped.

Minimal fix: on `cancel()` and at the start of `send()`, bump a turn generation, `clearActions()` with a turn-cancel reason, abort `layerFetchAbortRef` and the observation `AbortController`, and drop fetch/repair completions whose generation is not current. Do not treat a missing `mutation_revision` as “newer than the cursor”.

---

### UX-02 — Tool success and “完成” are not the map on screen

Severity: high.

Reproduction:

1. Ask for a layer (schools, buffer, heatmap).
2. Watch the tool card, the assistant chip, the top-bar badge, and the canvas as separate clocks.

What the user actually sees:

| Signal | When it flips | What it claims |
|---|---|---|
| Tool row `completed` | `step_result`, before the feature fetch | `use-sse-stream.ts:701-708`; card copy `chat.json` “N 个工具调用完成” (`tool-call-card.tsx:229, :372-389`) |
| Layer chip on the bubble | same event, even if the row is still an empty `FeatureCollection` | `use-sse-stream.ts:826-830`, `message-reducer.ts:397-406` (`layerAdded`) |
| Store-mounted ACK `succeeded` | `onEvent` returned, source still the placeholder | `useMapBridge.ts:578-584` (`store_mounted: true`) |
| Top bar “完成” | `done` / `task_complete`, not observation ACK | `useMapBridge.ts:429-431`, `top-bar.tsx:86-101`, `layout.json` `statusDone` |
| Top bar still “执行中” | until that terminal event | `map_finalization` can already `fitBounds` and commit a repaired spec (`use-sse-stream.ts:867-888`, `adapters.ts:102-110`). `complete` finalization is silent (`finalizer.ts:77-85` returns null). |
| Layer row “就绪” | `feature_count === 0`, or no contrary evidence | `layer-status.ts:63-66, :116-117`. Ready badges are hidden (`layers-tab.tsx:563-566`). Count is hidden when 0 (`:600-604`). |

So both directions of the status split are real:

- Agent chrome says the tool finished and a layer was added while the canvas is still the empty placeholder, or stays empty because the fetch later fails (the failure toast is separate and easy to miss: `map-state-restore.ts:35-41`).
- The canvas can already have been fitted to the result bbox while the badge still says 执行中. `aiStatus` is not an input to `mapFinalizationDecision`.

The backend can also return a GIS result the live map refused. `app/agent_pi_bridge.py:958-966` still returns the tool payload and only attaches `stale mapspec generation: result not applied to live map` as a model warning. The frontend chat has no corresponding “not on the map” state. The chip and the completed row are the user’s evidence.

User impact: the user trusts the green tool row and the layer chip, then looks at a map that does not show that layer, or sees a finished map while the agent badge is still running and later overwrites it.

Root cause: three clocks (SSE terminal, tool-row terminal, render observation) are rendered as one success. The chat clock wins.

Minimal fix: do not attach `layerAdded` or mark the tool row completed until render evidence for that layer is `mounted && converged`, or show a distinct chip state (“已返回，地图未确认”). Keep the top-bar badge on 执行中 while any turn-owned fetch or observation for that turn is outstanding. Do not dispatch `MAP_FINALIZATION` camera repairs after `cancel` / a newer `sendGeneration`.

---

### UX-03 — “这些学校 / 刚才框的范围” is not on the next turn, and is not server-acked

Severity: high.

Reproduction:

1. Turn on 矩形框选 (`map-panel.tsx:1956-1957`) and drag a box over a school layer.
2. The transient toast says `已框选 N 个要素` (`map.json` `brush.matched`, cleared after 2.5s at `map-panel.tsx:1510-1514`).
3. Within a second, ask “把这些学校标红” or “只看刚才框里的”.

What is sent:

- Brush publish (`map-panel.tsx:1457-1477`) writes the selection store and emits `feature_selected`. It does not call `setSelectedFeature`.
- `handleSend` (`use-sse-stream.ts:1143-1186`) puts only HUD `selectedFeature` into `map_state.selected_feature`. After a box select that field is null. `focus_layer_id` is the last focused layer, not the brush set.
- The interaction event is debounced 500ms (`outbox.ts:70, :257-262`) and is not flushed before `bridge.send`. `handleSend` never calls `flushInteractionBus`.
- The event itself is lossy: `emitFeatureSelected` keeps at most 8 ids, 32 characters each (`bus.ts:209-221`). The brush bbox predicate (`map-panel.tsx:1462`) is not copied into the event. A truncated box (“高亮前 N 个”, `map.json` `brush.matchedTruncated`) cannot be reconstructed server-side.
- The situation bar does not show the local selection. Scope and “选中 N 个” come from `view.claimed_scope` / `interaction_snapshot.selection` (`situation-status-bar.tsx:102-134`), which update only after ingest ACK (`bus.ts:37`). Until then the bar still says `无分析范围` / `未选中` (`workbench.json`).

Click selection is better and should not be lumped in: a single click does set `selectedFeature` and is included in `map_state` (`use-feature-selection.ts:89-133`, `use-sse-stream.ts:1180-1182`). MVT clicks are explicitly `is_approximate` until backfill (`use-feature-selection.ts:100, :179-184`). That flag is the honest part. The box path has no equivalent flag because the set never enters the turn body.

“刚才的区域” as the current view is partly wired: `map_state.viewport.bounds` is taken from `getMapSnapshot()` (`use-sse-stream.ts:1144-1152`). “刚才的区域” as a drawn AOI is not: `emitAoiChanged` (`bus.ts:234-247`) is the same unflushed outbox, and the AOI geometry is not in `map_state`.

“上一张图” is not bound to a client-confirmed generation. `streamChat` (`chat.ts:188-195`) does not send `mapspec_revision`, fingerprint, or display-confirmation id. Whether the server then uses its own session spec is a backend question; the frontend does not tell the model which generation the user is looking at.

User impact: the user boxes schools, sees a local toast, asks about “these”, and the agent answers against no selection (or against a stale server snapshot). The situation bar can still say 未选中 while the map is highlighted. There is no error. The reference just misses.

Root cause: the turn snapshot and the interaction ledger are different products, and the turn does not wait for the ledger.

Minimal fix: before `bridge.send`, `flush()` the outbox and include the selection-store record (layer id, id field, ids or bbox predicate, matched count, approximate flag) in `map_state`. If the ACK has not landed, say so in the composer (“选择尚未被服务器确认”) instead of sending a null `selected_feature`. Do not cap the turn snapshot at 8 ids when a bbox predicate exists; send the predicate.

---

### UX-04 — A locked layer’s visibility, and a user’s paint, still change on the next agent map

Severity: medium.

Reproduction:

1. Lock a layer. The control says it guards “面板/批量/隔离/轮次收起/agent 显隐与删除” (`messages/zh-CN/sidebar.json` `lockLayerAria`; rendered at `layers-tab.tsx:630-645`).
2. Ask a follow-up that emits a new MapSpec (style change, hide, re-theme), not only an `apply_style_intent` / visibility command.
3. The locked layer’s paint and visibility follow the new spec.

What is actually gated:

- Command path: `partitionByLock` on visibility / remove (`layerCommands.ts` imports at line 27; call sites around `:339`, `:751`, `:870`, `:1054`). `apply_style_intent` returns `locked` (`styleCommands.ts:70-72`, `style-intent.ts:160`). Turn-focus will not park a locked layer (`turn-focus.ts:53-55`).
- Spec path, which is how a follow-up map arrives: `onEvent` commits any `mapspec` (`use-sse-stream.ts:611-621`) and `composeLiveMapSpec` copies committed `paint` / `layout` (`live-spec.ts:196-201`). `lockedLayerIds` is not an argument. Pending overlay is visible/opacity only (`session-cursor.ts:189-211`). `syncSpecLayersToStore` writes spec visibility onto the HUD row with `source: 'server'` and does not read the lock (`map-state-restore.ts:303-314`).

User impact: the lock control tells the user the agent cannot hide or delete the layer. A follow-up map spec does both, and also replaces colors the user set in the style panel. The style panel itself is disabled while locked (`layers-tab.tsx:627`), so the user cannot even re-apply the color without unlocking. The agent can.

Root cause: the lock is enforced at the command catalogue, not at MapSpec ingress. Agent follow-ups that carry a spec never hit the catalogue.

Minimal fix: in `commitMapSpecDocument` / `composeLiveMapSpec`, if a layer id is in `lockedLayerIds`, keep the previous committed paint and visibility for that layer and surface `layer_locked` on the situation bar. Do not let `syncSpecLayersToStore` overwrite a locked row’s presentation.

---

### UX-05 — PNG/PDF export is not the last confirmed map

Severity: medium.

Reproduction:

1. Ask for a map. While the tool row is already green and the layer row is still “加载中” (empty placeholder FC), or while the top bar still says 执行中, open Map Studio and export PNG or vector PDF.
2. Or export after the display-confirmation card is still pending (`map-harness-cards.tsx:14, :236` — pending means the user has not acked this generation).

What is exported:

- PNG (`exportCommands.ts:110-144`) waits for one `render` event, then `MapExporterEngine.export` reads the live canvas. No read of `apply_ack`, `observation_accepted`, cursor revision, or `aiStatus`. A placeholder `FeatureCollection` with zero features exports as an empty thematic frame and the command returns `succeeded` (`:127-128`).
- Vector PDF (`publication-export.ts:117-166`) posts `composeExportSpec(committed, pending visibility)`. It does not check that this revision was observed, and the system message says the PDF “已成功生成” with page count and a download link, and no generation id (`:160-165`).
- `exporter.ts` has no `mapspec_revision` / `observation_accepted` check (searched). The display-confirmation card (`map-harness-cards.tsx:10-14`) is explicitly “地图已改变 ≠ 用户已看到/认可”, and export ignores it.

User impact: the file the user downloads can be the empty placeholder, a spec the browser has not converged, or a generation they have not confirmed. The chat then says the export succeeded. There is no way to tell which map generation the file is.

Root cause: export is bound to “whatever is committed / on the canvas now”, not to the last observation-accepted generation.

Minimal fix: refuse export while `aiStatus` is thinking/acting, while any visible ref row is `loading`, or while the latest observation `apply_ack` revision ≠ cursor revision. Stamp the download and the system message with `mapspec_revision` + fingerprint. If the display-confirmation card is pending, say so on the export button instead of publishing.

---

### UX-06 — A legal empty result looks like a successful blank map

Severity: medium.

Reproduction:

1. Ask for something that matches zero features (wrong district, filter that excludes everything, empty overlay).
2. The tool row completes (UX-02). A layer row appears with a name, no count, no status badge.
3. The map is blank. Nothing says what was queried or why it is empty.

Copy the user actually gets:

- Layer status derivation treats `feature_count === 0` as not-pending, then falls through to `ready` (`layer-status.ts:63-66, :116-117`). Ready is omitted from the row (`layers-tab.tsx:563-566`). The count span renders only when `featureCount > 0` (`:600-604`). So a legal empty and a not-yet-counted row look the same: a name and a slider.
- Fetch failure does speak: toast `图层「{name}」数据加载失败，地图上可能显示为空` (`map-state-restore.ts:35-41`). Legal empty has no equivalent.
- Brush empty does speak: `框选范围内没有可选要素` (`map.json` `brush.noFeatures`, `map-panel.tsx:1429-1431`). An agent query empty does not.
- Chart/table chrome has empty cards (`暂无图表数据`, `暂无统计数据`). The map surface does not.
- Situation bar can still show `已验证结果 N 项` (`situation-status-bar.tsx:146-153`) for an observed empty product, which reads as success. `等待地图验证` (`:163-168`) only appears for `observed_behind_desired` / `observation_missing`, not for “query returned nothing”.

User impact: the user cannot tell “the query ran and matched nothing” from “the layer has not loaded” from “the agent claimed a layer that is not drawn”. The only honest failure string is the fetch-error toast, and it does not name the query.

Root cause: the closed status vocabulary has no `empty` state, and the row hides the zero count that would have been the explanation.

Minimal fix: when `_descriptor.feature_count === 0`, show a badge `无结果` and a title with the tool name / filter / extent that produced it. Do not attach a success layer chip for that row. Keep `ready` for “features landed and are on screen”.

---

## Unconfirmed hypotheses

These were hunted and not confirmed as production defects. Do not treat them as bugs without a new trace.

1. **SSE reconnect duplicates layers.** Not confirmed for the chat stream. Events inside `sse_event_id_scope` carry `id:` (`sse.py:125-140`, `chat.py:1374`). `useMapBridge.ts:400-417` skips a replayed id, and also skips `id <= lastEventId`. `addLayer` no-ops on the same id (`layersSlice.ts:72-73`). `syncSpecLayersToStore` dedupes by id and by unbound `_refId` (`map-state-restore.ts:272-319`). Residual, not observed: an `id`-less `step_result` whose only payload is `result.image` mints `layer-${Date.now()}` (`adapters.ts:373`). A replay of that event would add a second heatmap. Production chat events are stamped; this needs a captured id-less frame before it is a bug.

2. **Previous-turn SSE events rewrite the next turn.** The SSE loop itself is gated (`useMapBridge.ts:364`, streamEpoch). A new `send()` aborts the old controller before opening the next stream (`:304-308`). Cross-session events are dropped (`use-sse-stream.ts:583-588`). The pollution that is real is the non-SSE tail in UX-01 (fetches, queued commands, observation repairs), not a second application of a replayed `step_result`.

3. **The model’s final prose names layers the runtime does not show.** Plausible, and the backend even hands the model a success payload when the live map rejected the generation (`agent_pi_bridge.py:958-966`). The frontend does not parse that warning into the bubble. What is confirmed is the chip and the completed tool row (UX-02), not a specific false sentence. Needs a transcript to promote.

4. **“上一张图” server memory is broken.** Not confirmed. The frontend simply does not send the generation the user is looking at (UX-03). The server may still answer from its session MapSpec. That is a different bug, and it was not traced here.

5. **Same-revision MapSpec clobber.** `commitMapSpecDocument` rejects only `revision < cursor` (`session-cursor.ts:159-161`). An equal revision with a different object commits. Whether a production event does that against a user mutation in the same revision was not captured. The no-revision commit path is the one that is definitely open (UX-01).

6. **Reconnect + refresh double-mount of custom overlays.** `map-panel.tsx:892-896` remounts `custom-*` overlays after style reload via a registry. Session id change clears that registry (`session-cursor.ts:98-101`). No duplicate was demonstrated on SSE reconnect alone.

---

## Tests that already lock the good behavior

Do not “fix” these; they are the fences. The gaps below them are what the findings above fall through.

| Locked behavior | Test |
|---|---|
| Reconnect sends `Last-Event-ID` and does not re-dispatch a replayed `step_result` id | `frontend/lib/hooks/useMapBridge.test.ts` “tracks the last received event id…”, “dedups replayed events by id”, “does NOT re-dispatch replayed step_result ids” |
| No reconnect after a terminal event, including `task_cancelled` | same file, “never reconnects after a terminal event” |
| `cancel()` aborts the stream and emits synthetic `task_cancelled`; no-op when idle | same file, “#988: cancel() aborts…” |
| `task_cancelled` finalizes running tool rows as 已取消 and does not invent a completed answer | `frontend/lib/hooks/use-sse-stream.test.ts` “task_cancelled finalizes still-running rows”, “INV-4: task_cancelled event marks thinking message as cancelled without fabricating fake completion” |
| Turn-scoped tool args are not consumed by the next turn | same file, “task_cancelled drops remaining pending args”; `use-sse-stream.ts:1137-1141` |
| Session switch clears the map-action queue | `useMapBridge.test.ts` “session switch calls clearActions…” |
| Observation response is generation- and session-gated; runtime repair commit carries revision when the server sends one | `use-cartographic-observation.test.ts` “commits the reasserted spec with its revision…”, “skips posting when envelope revision lags the session cursor” |
| Spec-layer mirror does not add a second HUD row for the same ref | `frontend/lib/session/map-state-restore.test.ts` `syncSpecLayersToStore` cases |
| User pending visibility/opacity is omitted from apply ACK | `frontend/lib/render-protocol/render-apply-ack.test.ts` “user-pending layers are excluded entirely” |
| Agent camera moves are not recorded as user viewport events | `map-panel.interaction-ir.test.tsx` “程序性相机（agent flyTo，无 originalEvent）→ 不 emit” |
| A failed user mutation is not reported as committed interaction | `user-mutation.interaction-ir.test.ts` |
| Click selection snapshot is bounded and parent-layer scoped | `use-sse-stream` helpers `buildSelectedFeatureSnapshot` / `resolveParentLayerId` (unit coverage in `use-sse-stream.test.ts` / `use-sse-stream.a3-tdd.test.ts`) |
| Journey 2 cancel reaches a terminal task status | `frontend/e2e/journeys/j2-task-cancel-retry.journey.ts` |

Not covered, and that is why the findings stand:

- No test that a `geojson_ref` fetch completing after `cancel()` or a second `send()` must not `updateLayer` / `focusLayer`.
- No test that `clearActions` runs on cancel or on a new send in the same session.
- No test that an observation `repair_action` arriving after cancel is dropped.
- No test that a box selection is present in the next `map_state`, or that `handleSend` flushes the interaction outbox.
- No test that a locked layer’s paint/visibility survives `commitMapSpecDocument`.
- No test that export is refused unless `apply_ack.revision` matches the cursor.
- No test that `feature_count === 0` renders an empty-result explanation instead of a silent ready row.
- No test that the layer chip is withheld until render evidence says the layer is on screen.

`j2-task-cancel-retry` locks the task badge, not the canvas. `useMapBridge` `#988` locks the synthetic chat event, not the fetches that comment claims are stale-gated.
