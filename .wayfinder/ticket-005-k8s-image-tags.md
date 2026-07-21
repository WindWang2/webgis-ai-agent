# Ticket: Pin Kubernetes image tags and add dev celery healthcheck

**Label**: `wayfinder:task` | **Type**: HITL

## Question

The K8s deployment uses `webgis-prod:latest` which breaks rolling updates. Should we switch to immutable tags (commit-sha or semver), and should we add a celery-worker healthcheck to the dev compose file for proper `depends_on` behavior?

## Context

- File: `deploy/k8s/02-api-deployment.yaml`, line 20
- Severity: P1-high
