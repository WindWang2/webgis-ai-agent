# session_id owns the GIS world; Pi session is a replaceable runtime

Pi can new/resume/fork/tree, but the app hosts Pi with `--no-session` and only sends
`prompt`/`abort`. MapSpec, refs, and checkpoints already key off `session_id`.

**Decision:** The GIS world's identity is the existing Session/`session_id`
(Conversation). Pi's session is a replaceable agent runtime. When Pi tree/fork is
wired, it records a Checkpoint id on the Pi entry and restores that Checkpoint; it
does not become the MapSpec primary key.

Rejected: `pi_session_id` as GIS identity; a parallel `gis_session_id`; leaving
Agent memory and map state permanently unsynchronized.
