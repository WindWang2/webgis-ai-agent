# MapSpec holds cartographic chrome; gesture camera is Observed Map

If every pointer event wrote MapSpec, checkpoints would drown in hover and pan.
If visibility and opacity stayed Observed-only, the Agent would not see the map
the user is looking at.

**Decision:** Desired MapSpec mutations include add/remove/reorder layers, paint,
`legend_spec`, layout, time, visibility, opacity, and explicit framing
(`SetViewIntent` / fit / “frame this layer”). Observed Map includes pan/zoom/rotate,
hover, highlight/flash, popup, and rendered-feature queries.

Restore a Checkpoint: layers and style return with it; camera snaps only if that
Checkpoint recorded an explicit SetView. `flyTo` remains an imperative action, not
a reconcile field (ADR-0036 boundary). Redis `map_state` remains a session
transport cache, not a third authority. Feature selection is session working
memory, not MapSpec (ADR-0059).
