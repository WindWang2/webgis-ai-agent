# MapSpec mutations are optimistic on HUD and rejected on stale revision

User chrome must mutate MapSpec (ADR-0056) without making HUD a second desired
document (ADR-0054). Waiting for every ACK would make sliders unusable; last-write-wins
would drop either the Agent's paint or the user's opacity.

**Decision:** HUD may apply a **Pending Mutation** locally, then send Intent with
`expected_revision`. ACK clears pending and the render projection follows committed
MapSpec. Reject, timeout, or revision mismatch is `superseded`: HUD discards pending
and re-projects from the server MapSpec. Agent tools use the same revision rule;
conflicts return as tool errors, not silent overwrites. SessionLockRegistry still
serializes a session. Field-level merge is rejected.
