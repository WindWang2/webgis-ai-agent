# MapSpecValidity ceiling is SEMANTIC_VALID

ADR-0051 published MapSpecValidity as `NOT_EVALUATED → MUTATION_REJECTED → MUTATION_ACCEPTED → SEMANTIC_VALID → COMPILE_VALID → RUNTIME_VALID`. Production never wrote the last two rungs; the live ceiling was already SEMANTIC_VALID. Collecting them would put a Node compile and a Playwright canvas onto the intent ladder.

**Decision:** Delete `COMPILE_VALID` and `RUNTIME_VALID` from MapSpecValidity. `is_valid` remains `tier >= SEMANTIC_VALID`. TS `compile-report` stays a tool artifact (`webgis_compile_maplibre`). Runtime evidence is Observed Map / cartography (ADR-0054), not a validity rung. This supersedes the ladder paragraph of ADR-0051 decision 1; the rest of ADR-0051 stands.
