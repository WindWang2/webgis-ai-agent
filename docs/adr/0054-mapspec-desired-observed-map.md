# MapSpec is desired intent; Observed Map is MapLibre; HUD is not a source of truth

Live pixels were driven by the HUD Zustand store via `hudStateToMapSpec` (ADR-0036
decision 2), while backend MapSpec was authoritative only for intent and headless
acceptance. Agent mutations and human map chrome therefore diverged by design.

**Decision:** MapSpec is the backend-authoritative desired map. The live runtime and
the headless compiler both consume a projection of that document. Observed Map is a
readback from the MapLibre instance and must not write MapSpec except by becoming a
mutation. HUD is a UI cache, not a writer of intent.

This supersedes ADR-0036 decision 2. Who may mutate MapSpec without an LLM turn is
ADR-0056. What counts as a mutation versus Observed Map is ADR-0057. New names
`DesiredGISState` / `RuntimeRenderSpec` / `ObservedGISState` are rejected; they
duplicate MapSpec, the render projection, and Observed Map.
