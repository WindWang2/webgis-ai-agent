# Ticket: Use random dummy hash for password verification timing safety

**Label**: `wayfinder:task` | **Type**: HITL

## Question

The login endpoint uses a constant dummy hash (`"scrypt$1$1$1$00$00"`) when the user doesn't exist. This creates a measurable timing difference between "user not found" and "wrong password." Should we use a randomly generated dummy hash per request, or implement constant-time comparison?

## Context

- File: `app/api/routes/auth.py`, line 194
- Severity: P1-high
