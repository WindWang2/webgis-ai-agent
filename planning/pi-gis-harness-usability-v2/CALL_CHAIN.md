# Call chain — what V2 actually touches

```text
user text
  → Pi session / extension tool
  → webgis_map_product
       scope name + primary_ref
         → product_scope_geography
              name tokens → matched / mismatched (no admin lookup)
              no tokens → admin_bbox_wgs84
              disjoint lon/lat boxes → success false, code scope_bbox_disjoint
              missing or partial overlap → warning scope_unverified, product continues
  → MapSpec layout_set
  → frontend reconcile / observation
```

Export is a different door:

```text
export_thematic_map
  → frontend export_map command
  → constraint_layout=not_evaluated
  → browser raster chain (unchanged)

POST /export/vector-pdf
  → render_publication_pdf(page_profile=None) → original document, not_evaluated
  → render_publication_pdf(page_profile="a4_landscape")
       → prepare_publication_document
       → solve_layout_v5
       → chrome positions move only when the solved anchor differs
```

Cartographer:

```text
purpose=screen_16_9 → solve_layout_v4(viewport), not_evaluated
purpose=a4_landscape → solve_layout_v5 once, evaluated
purpose=a3_* → not in solver profiles, not_evaluated, v4 viewport kept
```

Not on this chain: a second solver, a MapSpec schema version bump, a browser port of the solver, an SSE reconnect patch.
