# DECISIONS

1. **Projection over duplication** — EvidenceNode holds `ref` into ArtifactRegistry /
   dataset pin / MapSpec layer id; never copies GIS payloads.
2. **Typed claims** — claim_type ∈ closed vocab (count/density/rank/…); LLM prose
   is descriptive only via ClaimNarrativeProjection.
3. **Positive proof** — verification requires explicit present evidence for each
   obligation; absence → unsupported / unknown, never supported.
4. **Bounded graph** — RelationGraph max_depth / max_nodes / cycle guard (GOAL §22).
5. **Freshness** — upstream version change marks descendants stale via
   affected_descendants; no eager recompute.
6. **Contradiction** — same subject+predicate+comparator with incompatible values
   under overlapping scopes → contradicted; different scopes → scoped_divergence
   (both may be valid).
7. **Carto binding** — layer→style/class→breaks→metric→statistic claim→dataset version.
8. **Tenant isolation** — evidence/claim records carry tenant_id/session_id;
   cross-tenant ref resolution rejected.
9. **No SkillPolicy / Mission Runtime / StoryMap rewrite** — adapter only.
10. **Determinism** — same evidence graph → same verdict (sorted ids, no wall clock
    in verdict logic).
