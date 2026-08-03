# Research: High-Definition Vector Map Export & SVG/PDF Rendering Seams

## 1. Executive Summary & Problem Context

Currently, the WebGIS AI Agent captures maps via high-DPI raster canvas snapshots (`pixelRatio` scaling in `frontend/lib/map-exporter/index.ts`).
While high-DPI raster PNGs work for quick preview snapshots, they suffer from pixelation when printed or zoomed in professional PDF reports. Standalone SVG exports currently wrap base64 PNGs inside an `<image>` tag rather than outputting true vector paths.

This research establishes the **MapSpec-to-SVG Vector Seam Architecture** for 100% vector-crisp standalone SVG/PDF exports and backend WeasyPrint PDF reports.

---

## 2. Technical Findings & Component Seams

### 2.1 Frontend vs WebGL Limits
- MapLibre GL JS operates on a WebGL context, rendering pixels to a HTML5 `<canvas>`.
- Direct WebGL canvas extraction can only produce raster pixels (`toDataURL` / PNG).
- **Solution**: To produce true vector SVG/PDF paths (`<path>`, `<circle>`, `<text>`), the system must process the declarative `MapSpec` (sources + GeoJSON + style properties) directly into SVG DOM nodes (using `d3-geo` / GeoJSON-to-SVG mapping), bypassing WebGL.

### 2.2 Four Core Architectural Seams

```
+──────────────────────+         +──────────────────────────────────────+
|   Declarative        | ──────> | MapSpec-to-SVG Generator             |
|   MapSpec JSON       |         | (GeoJSON -> SVG Path + Vector Style) |
+──────────────────────+         +──────────────────┬───────────────────+
                                                    │
                                ┌───────────────────┴───────────────────┐
                                ▼                                       ▼
                 +──────────────────────────────+       +──────────────────────────────+
                 | Standalone Client-Side       |       | Backend WeasyPrint Engine    |
                 | SVG/PDF Vector Download      |       | HTML+SVG -> Crisp Vector PDF |
                 +──────────────────────────────+       +──────────────────────────────+
```

1. **Seam 1: MapSpec as Universal Interchange Format**
   - The declarative `MapSpec` JSON is the sole source of truth between frontend UI and backend services.
   - Eliminates transferring multi-megabyte base64 image strings over the wire.

2. **Seam 2: MapSpec-to-SVG Generator ("MapGenerator")**
   - A pure, environment-agnostic compiler module (`mapspec-to-svg`) that maps GeoJSON geometries and `MapSpec` paint rules (stroke, fill, opacity, stop interpolation) to SVG vector elements (`<path>`, `<polygon>`, `<text>`).
   - Runs in both Browser (for client-side SVG download) and Node/Python runtime.

3. **Seam 3: Backend WeasyPrint Vector PDF Injection**
   - In `app/services/report_service.py`, WeasyPrint natively parses inline `<svg>` elements inside Jinja2 HTML templates and renders them as resolution-independent vector PDF paths.

4. **Seam 4: Declarative Marginalia Vector Components**
   - Legend, North Arrow, Scalebar, and Graticules are rendered as SVG vector elements rather than rasterized canvas overlays, ensuring identical visual quality across client and server exports.

---

## 3. Decision & Handoff

- **Issue #258**: Resolved & Closed.
- **Unblocked Tickets**: Issue #259 (`[Prototype] Print Layout Visual Marginalia & Vector Legend Rendering`) & Issue #260 (`[HITL Grilling] HD Map Resolution Scaling & Tile Rasterization Policy`).
