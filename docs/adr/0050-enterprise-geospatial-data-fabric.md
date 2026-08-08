# Enterprise Geospatial Data Fabric V1 Architecture

Decouples agent spatial data access from simple file uploads and single-protocol tools into a unified Data Fabric architecture (Data Source → Capability Discovery → Spatial Catalog → DatasetDescriptor → Lazy Pushdown Query → ref_id Materialization → GIS Analysis → MapSpec).

## Context & Key Decisions

1. **Unified Adapter Pattern (`GeospatialDataSourceAdapter`)**: All spatial data sources (PostGIS, OGC API Features, WFS, WMS/WMTS, ArcGIS REST, STAC, GeoParquet, FlatGeobuf, PMTiles, S3/MinIO) implement a uniform lifecycle interface (`probe`, `capabilities`, `list_datasets`, `describe`, `preview`, `query`, `health`, `sync`). Upper-level Agent tools never deal with protocol-specific URL parameters or proprietary query syntaxes.
2. **DatasetDescriptor Contract**: Standardizes metadata representation across all vector, raster, and tile sources. Serves as the single contract for schema, bounding box, temporal extent, freshness, and query capabilities.
3. **Pushdown-First Execution (`QuerySpec`)**: Pushes bounding box, field projections, and predicate filters to the underlying data source whenever supported (`pushdown_bbox`, `pushdown_filter`). Prevents dumping multi-gigabyte spatial datasets into Python memory or LLM context prompts.
4. **Fetch-on-Demand Materialization (`ref_id`)**: Query results or local derived datasets emit opaque `ref_id` cursors stored in `SessionStore`. The LLM only receives lightweight descriptors and statistical summaries.
5. **SSRF & Zero-Secret Security Boundary**: Enforces strict URL validation against loopback IPs, private subnets (RFC1918), AWS/GCP metadata endpoints (`169.254.169.254`), and XML XXE entity expansion attacks. `ConnectionProfile` stores secret references or token handles, never plain passwords.
