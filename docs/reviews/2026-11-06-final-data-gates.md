# Final data gate follow-up

2026-11-06. Core NEW_TASK reactivated this completed specialist; earlier message-only handoffs had not started execution. Actual failed entries parsed from five XML files into `/tmp/data-final-handoffs.json`: **29 failures**, across preparation9, quality writeback2, schema5, aggregate pushdown1, terrain1, FlatGeobuf1, ModelOps4, CI journey2, UTM2, publish cap1 and strict parsing1. No commits or GitHub operations.

Deployment S1-059 findings are separately recorded in `2026-11-06-deploy-S1-059-independent-review.md` and sent immediately to core; root-owned deployment files were not edited. Core owns the two real residual fixes.

Checkpoint: preparation/quality/schema/ModelOps/UTM/parse fixtures now carry real persisted users/conversations or explicit trusted numerical roots; strict plan-size and parse rejection contracts are preserved. The current workflow already uses create-admin --password-stdin, so the CI journey tests are being rerun against that existing core fix. `/tmp/data-final-gates134.log` is running. Aggregate proof, reprojected DEM metric spacing, FlatGeobuf truthfulness and publish-cap caller precondition are still under review, not claimed closed.

Final checkpoint: all original 29 failed entries plus 3 goal scenarios now pass in focused suites: `/tmp/data-final-gates134.log` (92 passed), `/tmp/data-final-science136.log` (21 passed), `/tmp/data-final-goal139.log` (22 passed, including 10 subprocess supervisor checks).

Two real source defects were fixed: federated aggregate proof now receives the actual single left scan rather than all request sources (unsafe multi-scan plans retain rejection); FlatGeobuf synthetic in-memory paths disclose no spatial index. Terrain expectation now derives spacing from the actual reprojected WarpedVRT grid; ownership fixtures use persisted users/conversations or explicit trusted numerical roots. Production authorization, parse rejection, and numerical policies were preserved.
