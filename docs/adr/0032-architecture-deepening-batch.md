# Architecture Deepening Batch: Spatial Analytics, RAG Store, & Cartography Palettes

**Status:** accepted  
**Date:** 2026-08-02  

## Context

Following previous architectural reviews (F1–F4), an architecture audit evaluated five candidates for module deepening, seam discipline, and testability across spatial analytics, RAG vector retrieval, task tracking, and cartographic utilities.

## Decisions

1. **`h3_lisa` Reproducibility & Row Alignment**:
   - Fixed non-deterministic Monte Carlo seed behavior in `h3_lisa()` by passing explicit `seed=42` to `Moran_Local`.
   - Replaced unaligned `_extract_numeric_values` with `_filter_numeric_gdf(gdf, value_field)` so spatial weights matrix (`Queen.from_dataframe`) indexes match the filtered numeric feature rows.
   - Added `pytest.importorskip("esda")` and `pytest.importorskip("libpysal")` guards in `test_h3_lisa` for environments without optional PySAL dependencies.

2. **RAG Vector Store & Chunker Deep Module (`app/services/rag/`)**:
   - Defined `VectorStoreProtocol` interface (`add_vectors`, `search`, `get_stats`).
   - Encapsulated mutable global index state and file locking into `FaissVectorStore` in `app/services/rag/faiss_store.py`.
   - Extracted document text splitting into `app/services/rag/chunker.py`.
   - `rag_service.py` delegates vector operations to `FaissVectorStore` while retaining 100% backward compatibility for existing callers.

3. **Cartographic Color Palette Centralization (`app/lib/cartography/palettes.py`)**:
   - Centralized `COLOR_PALETTES` dictionary and `get_color_from_palette()` interpolation helper into pure module `app/lib/cartography/palettes.py`.
   - Re-exported from `CartographyService` without violating ADR-0007 (which retains separate rendering converters for live map overlay vs. compiled MapSpec).

4. **Explorer Pipeline Stage Context Contracts (`app/services/explorer/models.py`)**:
   - Introduced `ExplorerStageContext` and `StageResult` models to formalize payload contracts between the 5-stage discovery pipeline steps.

## Consequences

- **Leverage & Locality**: Vector storage logic, text chunking, and cartographic palette constants concentrate in dedicated, reusable deep modules with minimal caller interface surface.
- **Testability**: `FaissVectorStore` and `VectorStoreProtocol` allow isolated vector retrieval unit testing without global state leaks.
- **Build Quality**: All unit tests pass cleanly (508+ tests).
