# GROK BOT AUTONOMOUS DEVELOPMENT GOAL
# Direction 04 — Harness Hot-path Convergence (Mission × SkillPolicy × Evidence)

Repository: https://github.com/WindWang2/webgis-ai-agent

## Goal

Wire Durable Mission Runtime (D01), Production Skill Policy (D02), and Spatial Evidence/Claim Graph (D03) into the **default** Pi / SessionPlan multi-step GIS turn — without building a new platform, DAG, agent host, or second Artifact Registry.

## Baseline

- Base: latest `origin/master` (post #1327/#1328).
- Branch: `harness/hotpath-convergence-v1`
- Worktree: `../webgis-ai-agent-wt-hotpath-convergence`
- Never work on master. Do NOT merge own PR. Do NOT enable auto-merge.

## Architectural rules

- REUSE only: MissionRuntime, SkillPolicy/hotpath, evidence_claim/*.
- MUST NOT: SecondArtifactRegistry, SecondProvenanceDatabase, second planner, second ExecutionGraph.
- Kill-switches must preserve baseline behavior when flags off.
- Fail-closed: claim ingest / skill bind / mission bind must not invent PASS or mute errors into success.
- LLM prose never authoritative numeric source — claims from analysis outputs only.

## Problem

D01–D03 landed as envelopes but reviews document intentional gaps:
- Mission not auto-created on chat turns (optional `mission_id` swarm mirror only).
- SkillPolicy only via `gis_skill_policy` tool; planners do not import it.
- ClaimStore projectors ready but no tool/API/SSE hot-path ingest.

## Ownership

This branch OWNS:
- Situation→plan seam SkillPolicy attach (`attach_skill_guidance_to_plan_inputs` / equivalent)
- Optional Mission create/reuse for multi-step GIS turns + swarm bridge when enabled
- Analysis/product/map settle → EvidenceNode + typed Claim projection into ClaimStore
- Bounded Pi context card: skill guidance + claim grounding (no CoT dump)

MUST NOT implement: StoryMap compiler rewrite, Alembic ClaimStore (that is D05), promotion ledger durability (D06), Foundry glue (exp-rs).

## DoD

1. Multi-step GIS turn can optionally own/reuse a Mission; crash/restart recovers via Mission frontier when enabled.
2. SkillPolicy runs once at Situation→plan seam; `GIS_SKILL_POLICY=0` (or existing kill-switch) → unchanged planner.
3. Analysis/product/map settle paths project EvidenceNodes + typed Claims; missing evidence never SUPPORTED.
4. Pi gets bounded `pi_context` / grounding card from claim query + skill decision.
5. Hermetic tests:
   - no skill → unchanged planner behavior
   - no mission flag → no durable mission side effects
   - claim ingest fail-closed / positive-proof preserved
6. PR open against master with census of call sites; DO NOT merge.

## Key touchpoints (start here)

- `app/agent_pi_bridge.py` (mission_id swarm ~optional)
- `app/api/routes/chat.py` / situation env
- `app/services/mission_runtime/`
- `app/services/gis_harness/skills/hotpath.py`
- plan_candidates / planner / candidate_planner_v8 (currently zero skill imports)
- `app/services/gis_harness/evidence_claim/`
- goal satisfaction / ReplayTrace emit sites

## Workflow

Fully autonomous. Fetch master, inspect existing code, implement, test offline, push, open PR. No user questions for ordinary architecture choices.
