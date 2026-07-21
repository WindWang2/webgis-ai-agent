# Ticket: Fix SSRF validation to cover all URLs including defaults

**Label**: `wayfinder:task` | **Type**: HITL

## Question

The `_validate_external_urls` validator in config.py skips SSRF checks for URLs matching hardcoded defaults. If an attacker overrides a default via env var injection, SSRF protection is bypassed. Should we validate ALL URLs regardless of defaults, and should we move the allowlist to a separate config section?

## Context

- File: `app/core/config.py`, lines 169-177
- Severity: P1-high
