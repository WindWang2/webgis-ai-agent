# ADR-005: Tiered Tool Catalog with Keyword + Sticky TTL Activation

## Context

The system has 80+ tools across many domains (spatial, raster, OSM, RAG, etc.). Pushing all
tool schemas to the LLM every round wastes tokens and degrades selection accuracy.

## Decision

Tools are split into 3 tiers:
- **Tier 1**: Always-on core tools (buffer, overlay, chat).
- **Tier 2**: Activates on keyword match + sticky session memory (default 3 rounds).
- **Tier 3**: Only visible to explicit `list_available_tools` calls.

`ToolCatalog` dynamically subsets tools per LLM round based on the current `Plan` domains
and sticky activation history.

## Consequences

**Positive:**
- Token usage per LLM round is reduced by ~60% compared to all-tools-every-round.
- Tool selection accuracy improves because the LLM sees only relevant tools.
- Sticky TTL allows tools to remain active across related turns without re-matching keywords.

**Negative:**
- Heuristic activation can miss relevant tools (false negative) or activate irrelevant ones
  (false positive).
- Sticky TTL tuning is non-trivial; too long clutters the context, too short causes flicker.
- Changes to `DOMAIN_KEYWORDS` affect all sessions globally; A/B testing is difficult.
