# Combined PR integration review

The local integration includes the reviewed and repaired heads of PRs #1965,
#1972, #1973, #1974 and #1975 as actual ancestors. This preserves their commit
history for the final merge; it does not claim that the remote PRs have merged.

| Boundary | Resolution and evidence |
| --- | --- |
| Scene/terrain and issue repairs | Preserve signed terrain URLs, live owner-auth fallback, wrapped scientific coverage and the usable TileJSON envelope. Both scene synchronization sites carry sessionId. The isolated integration passed 174 frontend and 40 backend tests plus production TypeScript and focused lint. |
| Analysis workflow and module split | Keep the split tool registry and move both partial-result publication checks into the real `tool_map_product` implementation. Existing focused integration passed 54 tests; two real registry dispatch regressions verify the gate and kill switch. |
| Pi startup and authority | Defer runtime actions until session startup, reset tools each turn, and accept only the bounded routing suffix. Preserve interrupted settlement in all consumers and migration/process-supervisor packaging. The integrated branch passed 53 focused tests and eight real vendor/local-stub tests; no paid model or Docker build was used. |
| Workbench and server authority | Preserve typed APIs and owner/session checks. The old J4 fixture now establishes mutation authority through the real ingestion dedup evidence. Production/test TypeScript, 94 frontend tests and 37 focused journey/IR tests passed after the fixture correction. |
| Publication and export correctness | Retain explicit chrome visibility while wrapping output text. Missing requested fields do not fall back to a different scientific quantity. The integration passed 108 export tests. A further red/green regression prevents derived label layers from reporting the same missing field twice; the final focused compiler/export run passed 38 tests. |
| Coordination metadata | Pi uses ADR-0282, publication ADR-0283, analysis ADR-0284 and workbench ADR-0285. References were updated, ADR watermark is 285, and migration watermark remains 103. Generated outputs are refreshed by their owning commands. |

A fresh independent review inspected the specific combined boundaries at local
HEAD `766b2d15d56318ec8050e2f3f0c3eae11f037fae`, including the subsequent label
guard correction. It found no actionable P1/P2 in that scope. Three tiny
behavioral probes passed: two real settlement-timeout consumers and the
unmatched-network to partial-result publication boundary. This is bounded
cross-component coverage, not a claim that every changed line was independently
reviewed.

The static integration preflight also now uses one consistent artifact snapshot
for stale-input and hand-edit checks, rather than hashing the tree twice. Its
red/green regression proves the snapshot is computed once. Existing checks and
time limits remain in force.

Complete combined suites, production build, final browser review, external
publication and remote merge verification remain separate requirements. Real
PostgreSQL/Redis service races, Docker images, paid-model journeys and GPU/WebGL
visual acceptance are not established by the results above.
