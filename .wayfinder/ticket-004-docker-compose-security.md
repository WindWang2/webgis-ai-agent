# Ticket: Harden docker-compose security defaults

**Label**: `wayfinder:task` | **Type**: HITL

## Question

The dev `docker-compose.yml` has three security issues: (1) bind-mount defaults to `./app` instead of requiring explicit opt-in, (2) Redis healthcheck exposes password in `docker inspect` output, (3) celery-worker lacks a healthcheck. Should we fix all three, and what is the safest default for the dev mount?

## Context

- File: `docker-compose.yml`, lines 35, 52-55, 76-96
- Severity: P0 + P1
- Blocks: Development environment security baseline
