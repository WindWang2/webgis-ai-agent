# Architecture Deepening Workflow Spec

## Overview
Automated workflow loop for identifying, grilling, and executing deep module refactoring candidates across the WebGIS AI Agent framework.

## Loop Trigger
- Event: User issues `/improve-codebase-architecture`, `/loop-me`, or selects a deepening candidate.

## Pipeline Stages
1. **Discovery & Report Generation**:
   - Scan target subsystems (`app/services/`, `app/lib/`, `frontend/lib/`).
   - Evaluate against `codebase-design` principles (Module, Interface, Depth, Seam, Adapter, Leverage, Locality, Deletion Test).
   - Generate interactive HTML report at `/tmp/architecture-review.html`.

2. **Grilling & Decision Tree Resolution**:
   - Present candidate summary to the user.
   - Walk down the decision tree one question at a time with default recommendations attached.
   - Lock in shared understanding before writing code.

3. **Interface-Driven Execution & Dual-Axis Review**:
   - Implement deep module interface & value objects (`ports & adapters` / `in-process`).
   - Add/update unit test coverage (`pytest --no-cov tests/unit/ -v` and `npm test`).
   - Run parallel `/code-review` (Standards & Spec subagents).
   - Commit clean changes to `master`.

## Active Workflow Candidates
- **Candidate 1**: SessionStore Ref & Token Seam — **Completed & Verified**
- **Candidate 2**: MapSpec Compiler Single Entrypoint — **Completed & Verified**
- **Candidate 3**: Explorer Pipeline Runner Engine (`app/services/explorer/pipeline.py`) — **Next for Processing (Recommended)**
- **Candidate 4**: Chat Context Assembler Engine (`app/services/chat/context_assembler.py`) — **Completed & Verified**
