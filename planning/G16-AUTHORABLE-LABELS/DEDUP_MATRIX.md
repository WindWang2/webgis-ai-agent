# DEDUP_MATRIX — G16-AUTHORABLE-LABELS (base 816705218)

Legend: present (already in master, regression only) / partial / missing (G16 builds) / owned-by-other / blocked.

| Capability | Status | Evidence (call chain / test) |
|---|---|---|
| Auto label field selection, priority thinning, zoom bands, halo, CJK wrap, deterministic export collision | present | `mapspec_schema.py:454-476`; `frontend/lib/mapspec-runtime/label-layout.ts` (selectTopLabels :258, resolveDegrade :342); solver twins `label_collision.py` ↔ `label-solver.ts`; parity corpus `tests/cartography/golden_corpus/label_collision/` |
| Along-line repeat, representative anchors | present | `_line_repeat_anchors` (app/services/mapspec_svg_labels.py) used at mapspec_to_svg.py:1799; TS twin mapspec-to-svg.ts |
| Point-symbol (circle) export obstacles | present | mapspec_to_svg.py:1093-1100,1516 → solve :1880; `test_label_obstacles_v1.py` |
| Feature-level hide/pin/offset/text override persistence | missing | No slot in `MapSpecLayerLabel`; no mutation intent; no UI; grep "authoring" in app/lib/cartography → none |
| Line/polygon ink obstacles w/ identity | missing | mapspec_to_svg.py:1096-1097 “line/polygon 图元障碍为后续波次”; solver obstacles are anonymous AABB tuples |
| Own-anchor obstacle exemption policy | missing | label_collision.py docstring: caller-side convention only, never implemented |
| Stable feature identity | present (consume) | feature_edit/identity.py (`_wfid`), contracts FEATURE_ID_KEYS; G14 #2075 extends — additive, G16 consumes either raw chain ids or WFIDs, no changes needed |
| Source content_revision | present (consume) | GeoJSONMapSpecSource.content_revision (schema:210) |
| MapSpec mutation registry/CAS/undo/checkpoint | present (consume) | mutation_registry.py descriptors; apply_gis_mutation CAS; agent_undo.py |
| user-wins provenance | present (consume) | apply_gis_mutation(origin=...) + superseded 409 path (mapspec_mutations.py:115) |
| MapLibre label sublayer runtime | present (extend) | runtime.ts addLabelSublayerSafe :1604; label-only fast path applyLabelOnlySafe :1579 |
| FE drag/escape/undo gesture framework | present (consume pattern) | sketch-editor.tsx; undo.ts recordCommand |
| Pi tool registration + evidence forwarding | present (consume) | cartography_tools.py @tool + _forward_evidence |
| Export routes PNG/SVG/PDF | present (consume) | mapspec_to_svg.py (backend SVG/PDF); FE map-kit pdf-vector.ts (svg2pdf); raster capture exporter.ts |
| Frame-scoped products | owned-by-other (G15, unmerged branch exists, no PR) | MapSpecFrame present in schema; G16 keeps frameId optional & inert until G15 lands |
| Pi pin/bridge/version changes | owned-by-other (G12, unmerged) | vendor/pi gitlink; G16 never touches |
| Capability availability projection | owned-by-other (#2073) | G16 tools self-describe tier/domains as existing tools do |
| Batch parametric analysis (G09) | excluded | per task §B.5 — do not include |
| “Why label not shown” explanations | missing | No hidden_by_user/below_scale/... taxonomy anywhere; solver reasons today: budget/collision/obstacle/empty_text |
