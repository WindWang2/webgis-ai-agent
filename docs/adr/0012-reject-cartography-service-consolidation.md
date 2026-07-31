# Reject Candidate #2 — Keep CartographyService as Sole Classification Engine

**Status:** accepted

We will **not** delete or refactor `CartographyService` into a non-existent `CartographicStyle` module.

## Context

Candidate #2 in the architecture review suggested deleting `CartographyService` as a "shallow pass-through facade over `CartographicStyle`".

Inspection of the codebase reveals:
1. `CartographicStyle` does **not** exist in the codebase.
2. ADR-0007 (`0007-no-unified-cartographic-style-module.md`) explicitly decided *not* to create a unified `CartographicStyle` module because MapSpec compilation remains headless-only, and `CartographyService` is the single live classification engine used by the running application.
3. `CartographyService` is a deep domain module containing Fisher-Jenks natural breaks (O(n²k) DP), quantiles, equal interval classification, LISA spatial autocorrelation styling, and `legend_spec` generation used by `app/tools/cartography.py`, `app/tools/templates.py`, `app/tools/advanced_spatial.py`, and `app/services/raster_cartography_converter.py`.

## Decision

Reject Candidate #2. Maintain `CartographyService` as the canonical domain class for spatial classification and legend specification building. Do not create a hypothetical `CartographicStyle` wrapper.
