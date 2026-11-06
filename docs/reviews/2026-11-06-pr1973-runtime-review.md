# PR1973 deployment runtime review

Reviewed PR head `06c6df78c9e8b23410475b0e2c24bf86b39e7855` against master, with focused inspection of the Pi package boundary, runtime entry selection, extension startup and Docker/Compose target shape. This supplements the original PR review; it is not a claim to have read every changed line or built either Docker image.

| Finding | Disposition | Evidence |
|---|---|---|
| P1: the RPC bundle still imports external jiti when loading an extension, including `.mjs`; copying only the bundle breaks a clean runtime | Fixed: both runtime images and the Compose backend-builder target copy the exact builder-installed jiti package alongside the bundle. Corrected the incorrect dependency-free claims in source/build notes and the ADR/matrix. | Added a real deployment-shape test that copies only bundle, package metadata, jiti and WebGIS extension into an isolated directory. It completes get_state, invokes the registered WebGIS tool through the local stub LLM and observes exactly one agent_settled. It cannot resolve the builder's ancestor node_modules. |
| P2: the Compose API targets backend-builder, which had no Node executable | Fixed: backend-builder explicitly receives the official Node runtime binary. Its bundle/jiti copies are independently checked by a non-heavy image contract test. | No actual Docker build is claimed. The independent isolated Pi build pin was f9a26365568127461c47a07a37dcb42c140a3ac5 (v1.5.0). |

Validation:

- Real vendor protocol and schema-coercion lane: 9 passed (`/tmp/pr1973-runtime-contract-final.log`).
- Opt-in real Pi bridge/local stub journey: 1 aggregate journey test passed (`/tmp/pr1973-stub-journeys-final.log`); no paid LLM request was used.
- Packaging/compatibility/turn-token unit lane: 18 passed (`/tmp/pr1973-packaging-unit-final3.log`).
- Extension hardening, Pi chaos and deployment wiring: 53 passed (`/tmp/pr1973-bridge-boundaries-final.log`).
- Ruff passes; `git diff --check` passes.

The original workspace's modified vendor checkout was preserved. Validation uses an independent worktree of the already built, pinned upstream clone. The final merged tree still needs conflict resolution and relevant integration checks, including retaining the main repair branch's Node copy, process supervisor, migration and credential protections.
