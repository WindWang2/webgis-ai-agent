# ADR-002: Agent-Centric Architecture

## Context

The system needs an interface for users to create layers, run spatial analysis, and manage
geospatial data. Two options were available:
1. Traditional REST CRUD API (direct endpoints for layers, analysis, uploads).
2. LLM agent as the sole interface (all operations flow through tool calls).

## Decision

All spatial operations — layer creation, analysis, uploads, reports — flow exclusively through
the LLM agent via tool function calling. Direct REST CRUD endpoints for layers and analysis
were intentionally removed.

## Consequences

**Positive:**
- The agent is the single source of truth for map state.
- Ensures embodied perception: the agent always knows what data exists before acting.
- Natural audit trail: every operation is recorded in `Message.tool_calls`.

**Negative:**
- All operations incur LLM latency and token cost, even simple ones.
- Debugging is harder: no direct API access for testing or scripting.
- Tool selection failures (wrong parameters, missing context) propagate to the user as
  confusing agent responses rather than clear HTTP errors.
