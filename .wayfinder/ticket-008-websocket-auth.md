# Ticket: Clarify WebSocket auth model and add proper rate limiting

**Label**: `wayfinder:grilling` | **Type**: HITL

## Question

The WebSocket endpoint allows anonymous connections (no token = allowed). Is this intentional? If so, what isolation and rate limiting is in place for anonymous sessions? If not, how should we enforce mandatory authentication?

## Context

- File: `app/api/routes/ws.py`, lines 21-35
- Severity: P1-high
