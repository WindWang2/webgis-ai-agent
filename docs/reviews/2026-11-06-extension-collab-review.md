# Extension / collaboration issue verification — 2026-11-06

Scope delegated by core: #1951 S11-005..014, S12-009..017, S13-L1..L5/L8 (25 Low); #1950 S6-017/018 (2 Low). Additional Medium #1920 S6-004 and #1927 S13-003; later delegated JS tool-boundary Medium #1924 S10-005 and Low #1951 S10-L11. Artifact cache / atlas layout remain outside this scope.

| Finding | Status | Verified evidence / resulting behavior |
|---|---|---|
| #1951 S11-005 | fixed | Both certification producers return unknown trust/execution_mode on missing extension; cli.py human format uses safe defaults. |
| #1951 S11-006 | fixed | cli.py archive builder omits symlink files, pycache/pyc and .certification.json. Links cannot be published into an uninstallable archive. |
| #1951 S11-007 | fixed | certification.py and capability_certification.py require signed_verified; unsigned/untrusted signatures cannot produce certified=true. Positive certification fixtures now carry real HMAC signatures and trusted keys; unsigned CLI fixtures honestly fail certification. |
| #1951 S11-008 | fixed | context.py detects duplicate recipe IDs inside one spec before projection; registration failures become typed REGISTRY_PROJECTION_FAILED, preserving ledger rollback. |
| #1951 S11-009 | already_fixed | CartographicThemeRegistry.register_theme checks existing ID before mutation and raises; context records undo only after success, so collision cannot overwrite/delete existing theme. |
| #1951 S11-010 | fixed | In-process load_sibling removes the inserted sys.modules entry on any execution failure and rethrows; repaired source can import on retry. |
| #1951 S11-011 | fixed | conformance.py refreshes record after activation/reload; fail assertions include discovery and activation diagnostics. |
| #1951 S11-012 | fixed | Capability certification captures initial content fingerprint; final recheck rejects content drift and report retains fingerprint actually certified. Dedicated mutation-during-certification regression passes. |
| #1951 S11-013 | fixed | _build_host documentation now distinguishes construction from certify commands that execute activation code. |
| #1951 S11-014 | already_fixed | distribution.safe_extract_package docstring describes explicit regular-file/path/size checks and copying; no obsolete filter=data promise remains. |
| #1951 S12-009 | fixed | Repeated local_untrusted activation refusals replace the existing TRUST_BLOCKED diagnostic instead of appending forever. |
| #1951 S12-010 | already_fixed | Core's refresh_revocations stores newly loaded TrustStore through dataclasses.replace(self._policy,...), after successful load. |
| #1951 S12-011 | fixed | Public activation wrapper counts every result including early disabled/quarantined/failed paths; discovery quarantine counts trust_policy or revocation, with bounded label vocabulary. Existing revocation hooks retained. |
| #1951 S12-012 | fixed | Namespace underscores are doubled before underscore-separated module/tool/provider projections; purge uses the same encoding. aa_bb.c and aa.bb_c cannot collide or purge each other's module. |
| #1951 S12-013 | fixed | pack_catalog reads certification using host certification_trust / certification_key; strict catalog views verify HMAC, matching activation policy. |
| #1951 S12-014 | fixed | Permission docs state implemented policy: high-risk declarations warn and every use still requires explicit operator grant; no unsupported second-interaction promise. |
| #1951 S12-015 | fixed | VersionRecord validates parse_version; PackageRecord validates version dictionary key equals record.version. Malformed persisted versions rejected at load boundary. |
| #1951 S12-016 | fixed | SBOM scans text decoded with replacement, preserving secret detection despite non-UTF8 noise; symlink content excluded from manifest/signature scans. |
| #1951 S12-017 | fixed | Authoring checks enforce feature-count cap before implementation, compare integer/float expectations with finite numeric tolerance, invoke cancellation probe independently. Stream aggregation hard-caps event count; model SDK docs describe supported V3 worker streaming. |
| #1951 S13-L1 | fixed | TrustStore revocation holds kernel-owned file lock over fresh load, merge, atomic write and refresh. Concurrent stale-store revocation test retains both packages. |
| #1951 S13-L2 | fixed | Worker profile cache keys use full canonical SHA256 instead of 16-bit hash; provider instances capped128 with close on eviction. |
| #1951 S13-L3 | fixed | Worker load_sibling clears half-initialized module on exception; parameterized worker/in-process retry regression passes. |
| #1951 S13-L4 | fixed | Broker/unrelated stream frames do not renew idle deadline; only actual result events keep the request alive. |
| #1951 S13-L5 | fixed | fail_closed rejects unknown, empty and missing certificate states. Registry/runtime credential-only checks default False; core adapted broker permission/credential-only facts to False because host already verifies certification. Permission/credential default-deny behavior retained. |
| #1951 S13-L8 | fixed | Legacy call_stream window argument cannot control window; worker handshake window is authoritative. |
| #1950 S6-017 | fixed | Token-only /ws clients select no subprotocol; bearer is echoed only if actually offered. Real signed-token handshake/ping regression passes. |
| #1950 S6-018 | fixed | Hello includes document only under64KiB UTF8; large docs refetch by revision. Full-room connections are explicit read-only spectators: no presence join/update or lease mutation. Core heartbeat atomically refuses absent/expired participants, so it cannot recreate an over-cap member. |
| #1920 S6-004 | fixed | Fresh revision read short-circuits unchanged sync; doc read uses targeted deleted/mapspec fields, no L1 invalidation or whole-session fetch. Revision checked around read (bounded retry) and disk resurrection retained through empty state_hint. Two new targeted-read regressions pass. |
| #1927 S13-003 | fixed | Worker CPU soft deadline renewed from cumulative getrusage at each request, while original system hard limit remains authoritative. Whole streaming call shares one budget; no renewal per event. Eight legal0.25s CPU calls survive a1s per-call budget; original infinite loop is still killed. |

| #1924 S10-005 | fixed | webgis-tools/index.mjs restores frozen defaultActive every before_agent_start. Dynamic marker accepted only in final control paragraph adjacent to signed TURN_CONTEXT plus fixed internal trailer; Python disclosure suffixes preserved. |
| #1951 S10-L11 | fixed | currentTurnToken reads latest user entry only, recognizes actual fixed routing suffix, and never reuses a global pinned token. Bridge timeout and external cancellation remain active through full response body decoding, including HTTP error bodies; finally cleans listeners/timer. |

Validation: final whole extension + capability-policy + WS integration2683passed (47.11s). CPU/resource/stream23passed including cumulative legal CPU, runaway kill, junk frames not renewing idle and caller window unable to change credit policy. WS70passed plus dedicated full-room spectator regression1passed. Extension boundary18passed; MJS/Python bridge and dynamic-surface29passed. Entire owned extension/WS Python Ruff and diff whitespace checks pass.

## Additional canonical #1814 S6-003 — fixed

`worker/client.py` now starts a dedicated `StderrCapture` immediately after spawn, before the handshake. The daemon continuously drains the pipe with nonblocking reads and retains at most 65,536 bytes. `_stderr_tail` reads the retained buffer after bounded producer termination; it no longer competes to consume the pipe. Shutdown/kill close the drainer through a bounded join, and restarting an exited worker releases its previous capture before creating a new generation.

`/tmp/worker-stderr-review.log`: **51 passed** (real noisy worker startup/RPC, timeout/kill/restart, a held-open pipe shutdown, full worker integration, streaming and server cases). The regression writes more than 3 MiB to stderr during activation and again during a call; RPC succeeds and the last diagnostic is retained within the fixed buffer. `/tmp/worker-stderr-lint.log` Ruff clean; `/tmp/worker-stderr-mypy.log` new capture module clean. Other #1814 S6 findings remain core-owned.

Final portable lifecycle check: `/tmp/runtime-worker-final-boundaries.log` **20 passed** with metrics locking, plus `/tmp/runtime-worker-final-types2.log` **5 source modules clean**. Windows pipe polling avoids select-on-pipe; real worker/process assertions were executed on Linux.
