# User map chrome mutates MapSpec without an LLM turn

ADR-0002 made the LLM agent the sole interface for spatial operations so the agent
would always know the map. ADR-0054 then forbade HUD from being a private intent
document, which would have forced every slider through a model call.

**Decision:** MapSpec has three origins — `agent`, `user`, `system`. User cartographic
chrome (layers, style, visibility, opacity, layout, time, explicit set-view) goes
through the same `apply_mutation` / Intent seam as Agent tools. It does not wait
on an LLM turn. GIS compute (analysis, geocode, routing, raster, reports) still
only runs via tools; those REST CRUD surfaces stay gone.

The agent knows the map because MapSpec is the only desired document, not because
every human gesture was a tool call. This supersedes ADR-0002's sole-interface
clause for map chrome only.
