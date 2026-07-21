# Ticket: Fix login rate limiter to prevent IP-based user lockout

**Label**: `wayfinder:task` | **Type**: HITL

## Question

The login rate limit key is `auth_login:{client_ip}:{req.identifier}`. An attacker behind a NAT can lock out legitimate users by intentionally failing logins with their username. Should we rate limit by IP only, and add exponential backoff?

## Context

- File: `app/api/routes/auth.py`, lines 186-194
- Severity: P1-high
