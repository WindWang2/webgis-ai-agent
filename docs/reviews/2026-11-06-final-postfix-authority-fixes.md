# Targeted independent review follow-up: deployment failure and Pi settlement

This follow-up fixes only the two findings confirmed by the post-fix authority review. It does not start another project-wide audit or declare the integrated branch green.

| Finding | Correction | Behavioral evidence |
| --- | --- | --- |
| SSH reports a successful deployment after maintenance failed and left writers stopped | The actual production SSH heredoc now starts with `set -eu`. Image-load or maintenance failure exits the remote shell before pruning, so SSH propagates a nonzero deployment result. The maintenance helper still leaves writers stopped when backup or migration fails; it does not restart against an uncertain schema. | `tests/test_maintenance_upgrade.py::test_production_remote_script_propagates_failure_before_pruning` extracts and executes the actual workflow remote body and the actual maintenance helper with a Docker command recorder. Only the image file location is adapted to the temporary directory. Success restarts services and then prunes; image-load, backup and migration failures return nonzero, skip restart and skip prune. The existing helper failure-ordering cases remain unchanged. |
| Coordinator timeout persists `interrupted` but Pi tracker and diagnostics report success | Tracker and both prompt/stream diagnostic consumers classify `interrupted` as failure. Diagnostics use `pi_settle_interrupted`. Non-stream success is recorded after the coordinator result, preserving the first-wins recorder without preempting a later settlement failure. Existing cancellation, aborted/failed, completed/refused and receipt replay behavior remains covered by neighboring tests. | `tests/unit/test_review_settle_timeout_bridge.py::test_coordinator_timeout_fails_pi_tracker_and_turn_evidence` drives the real non-stream and stream Pi host paths, a real Memory session store/runtime, real durable claim and real coordinator. The coordinator budget is reduced to 0.02 seconds and its refusal read is blocked. Both paths cancel that read, persist terminal `interrupted` plus `settle_finished_at`, fail the tracker exactly once and record failed turn evidence. The streaming result sink reports `completed=false`. No claim, completion or terminal runtime method is mocked. |

Verification on 2026-11-06:

- Before source corrections, the six new cases produced **5 failed, 1 passed**: all three injected remote failures were swallowed and both Pi timeout cases completed the tracker instead of failing it.
- After corrections, the maintenance tests, new Pi timeout tests, existing Pi settlement matrix, coordinator tests and Memory/Redis claim/control regressions passed together: **67 passed in 8.18s**.
- `mypy app/agent_pi_bridge.py`: **Success: no issues found in 1 source file**.
- Ruff passes for the changed Python source and both test files; `git diff --check` is clean.

The first focused neighbor run exposed a teardown pollution in the new test's patch of a lazy package export. That test now patches the package dictionary so teardown restores the absent export rather than leaving a concrete function behind; existing neighbor assertions were not changed. The subsequent 67-test result includes those neighbors after both new host-path cases.

Limits: the command recorder does not perform live SSH, Docker or PostgreSQL deployment. The Pi timeout integration uses Memory storage; existing neighboring claim tests exercise Memory and fakeredis, not a live Redis failover. The previously started full backend run predates these source corrections and cannot validate them. Its outcome remains the root integration gate's responsibility. No baseline, version, generated artifact, commit or push was changed by this follow-up.

Scope status: **SOURCE_FROZEN** after the checks above.
