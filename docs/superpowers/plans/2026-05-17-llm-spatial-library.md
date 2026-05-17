# LLM-Ready Comprehensive Spatial Analysis Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor and expand spatial analysis capabilities into a professional library specifically optimized for LLM interaction (high-fidelity schemas, self-correcting errors, and insight-ready outputs).

**Architecture:** Create a decoupled `app/lib/geoprocessing/` package. Each tool will implement a dual-output pattern: `data` (for MapLibre) and `summary` (for LLM narration). Standardize on a "Reference-First" data flow to minimize context window bloat.

**Tech Stack:** Python, GeoPandas, Shapely, Scipy, NetworkX.

---

### Task 1: LLM Tool Interface & Schema Standardization

**Files:**
- Create: `app/lib/geoprocessing/interface.py`
- Modify: `app/tools/registry.py`

- [ ] **Step 1: Define the standard "LLM-Response" wrapper**
Implement a result structure that explicitly separates raw data from textual insights to prevent LLMs from "reading" thousands of coordinates.
- [ ] **Step 2: Enhance Registry with self-correcting error hooks**
Modify `dispatch` to capture common GIS errors (missing fields, invalid geometries) and format them as "correction prompts" for the LLM.
- [ ] **Step 3: Commit**
`git add app/lib/geoprocessing/interface.py app/tools/registry.py && git commit -m "chore: standardize LLM tool interface and error recovery"`

### Task 2: Robust Geometric Operations (Precision Focused)

**Files:**
- Create: `app/lib/geoprocessing/geometry.py`

- [ ] **Step 1: Implement "Smart" Buffer and Clip**
Add logic to automatically detect CRS and warn the LLM if it attempts a meter-based buffer on degree-based data.
- [ ] **Step 2: Implement Multi-Layer Overlay (Identity, SymDiff)**
Ensure overlay results include a `change_summary` (e.g., "Area increased by 15%") for the LLM to report.
- [ ] **Step 3: Commit**
`git add app/lib/geoprocessing/geometry.py && git commit -m "feat: add precision-aware geometric operations with LLM feedback"`

### Task 3: Spatial Statistics & Pattern Discovery (Insight Ready)

**Files:**
- Create: `app/lib/geoprocessing/statistics.py`

- [ ] **Step 1: Standard Deviational Ellipse (SDE)**
Calculate directional trends. Output must include a "Directional Insight" string (e.g., "The trend is North-West to South-East").
- [ ] **Step 2: Moran's I & Hotspot with Narration**
Migrate existing stats but add a `narrative` field to the output that explains p-values in plain language.
- [ ] **Step 3: Commit**
`git add app/lib/geoprocessing/statistics.py && git commit -m "feat: implement spatial stats with automated insight narration"`

### Task 4: Aggregation, Binning & Feature Engineering

**Files:**
- Create: `app/lib/geoprocessing/aggregation.py`

- [ ] **Step 1: Dynamic Grid Generation**
Support Square and Hexagon grids. Automatically suggest a reasonable `cell_size` if the LLM provides an absurd one.
- [ ] **Step 2: Multi-Statistic Spatial Aggregate**
Allow counting points in polygons with auto-grouping by attributes.
- [ ] **Step 3: Commit**
`git add app/lib/geoprocessing/aggregation.py && git commit -m "feat: add dynamic binning and multi-stat aggregation"`

### Task 5: Network Analysis & Reachability

**Files:**
- Create: `app/lib/geoprocessing/network.py`

- [ ] **Step 1: Isochrone (Service Area) Generation**
Build true network-based service areas using NetworkX. Output should include a "Coverage Percentage" for the LLM.
- [ ] **Step 2: Nearest Neighbor (Feature-to-Feature)**
Find the closest school/park for every residential point and return a summary of average access distances.
- [ ] **Step 3: Commit**
`git add app/lib/geoprocessing/network.py && git commit -m "feat: implement network isochrones and accessibility analysis"`

### Task 6: Chat Engine Integration & Prompt Tuning

**Files:**
- Modify: `app/services/chat_engine.py`

- [ ] **Step 1: Refactor SYSTEM_PROMPT for "Thinking in Layers"**
Update the prompt to guide the LLM on how to chain these new tools (e.g., "If I have points, I should Clip first, then Aggregate").
- [ ] **Step 2: Implement "Tool Output Slimming"**
Ensure that when a tool returns a massive GeoJSON, the `ChatEngine` only passes the `summary` and a `ref_id` back to the LLM's context.
- [ ] **Step 3: Commit**
`git add app/services/chat_engine.py && git commit -m "refactor: integrate library and tune LLM reasoning for spatial chaining"`

### Task 7: Final Verification & End-to-End Testing

- [ ] **Step 1: Verify "Self-Healing" behavior**
Run a test where the LLM calls a tool with a missing field and verify it corrects itself based on the returned error.
- [ ] **Step 2: Verify Visualization Sync**
Ensure all new tools correctly trigger MapLibre layer additions via the `command` injection.
- [ ] **Step 3: Commit**
`git add . && git commit -m "test: verify end-to-end LLM spatial reasoning and tool execution"`
