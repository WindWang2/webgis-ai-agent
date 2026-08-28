# ADR-0077: Multi-Pod Topology & Ingress Sticky Sessions for Pi Turn Ownership

## Status
Accepted

## Context
In multi-pod Kubernetes deployments (`replicas >= 2` + HPA), session state mutations are serialized across pods using Redis distributed session locks (`distributed_lock.py`), and turn capabilities are signed using HMAC turn tokens (`pi_turn_context.py`).

However, the Pi agent execution model involves a process-local subprocess (`PiRpcClient`) and local session files. During a multi-step turn, tool execution callbacks (`/pi-tools/execute`) and Server-Sent Events (SSE) streaming connections must maintain consistent turn ownership and context.

## Decision
1. **Distributed Active-Turn Coordination**:
   - `PiTurnRegistry` registers active turn identifiers in Redis under `webgis:pi:active_turn:{session_id}` with a bounded TTL upon turn start.
   - `is_active_pi_turn(session_id, turn_id)` validates turn ownership against local in-process memory first, falling back to Redis for cross-pod callback routing.
   - Atomic turn release via Lua script ensures keys are cleanly removed on turn completion without removing subsequent turn ownership.
2. **Actionable 409 Conflict Guidance**:
   - When a callback presents an expired, completed, or unrecognized turn token, `/pi-tools/execute` rejects the request with HTTP 409 Conflict and a structured payload (`TURN_CONTEXT_INACTIVE`).
   - The Pi `webgis-tools` extension formats this payload into clear recovery guidance for the LLM to prevent blind retries.
3. **Ingress Sticky Sessions Invariant**:
   - For multi-pod production environments, ingress controllers MUST configure session affinity (cookie-based or client-IP sticky sessions, or routing by `session_id`) so that streaming SSE and child callbacks are co-located with the active subprocess pod during a turn.

## Consequences
- Cross-pod callbacks gracefully verify turn capabilities without false rejections.
- Inactive turns fail fast with structured recovery guidance rather than causing LLM retry loops.
- Multi-replica topologies maintain strict data consistency and turn isolation.
