# Ticket: Audit and rotate .env credentials

**Label**: `wayfinder:task` | **Type**: HITL

## Question

A `.env` file exists in the repository root. While gitignored, it may have been committed before the rule was added. Should we check git history for committed secrets, and rotate any exposed credentials?

## Context

- File: `.env`
- Severity: P2-medium
