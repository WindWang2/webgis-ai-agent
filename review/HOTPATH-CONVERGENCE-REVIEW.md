# Review — Harness Hot-path Convergence (Direction 04)

Date: 2026-09-15 (UTC+8)
Branch: `harness/hotpath-convergence-v1`
Base master: `b44c1c9b`

## Summary

Wires D01 MissionRuntime, D02 SkillPolicy, and D03 Evidence/Claim into the
default multi-step GIS turn via a small adapter package — no new platforms.

## Kill-switches

| Flag | Default | Effect when off |
|---|---|---|
| `GIS_SKILL_POLICY` | ON | No skill_guidance; planner byte-identical |
| `GIS_MISSION_HOTPATH` | OFF | No auto Mission create/reuse |
| `GIS_MISSION_RUNTIME` | ON | Disables durable mission subsystem |
| `GIS_CLAIM_INGEST` | ON | No ClaimStore projection on settle |

## Tests

`pytest tests/unit/gis_harness/test_hotpath_convergence_v1.py -o addopts=` → 14 passed

## Residual limits

See PROGRESS.md. Not merged by design.
