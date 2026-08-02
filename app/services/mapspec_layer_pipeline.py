"""MapSpec Layer Ingestion Pipeline (Compatibility Re-export).

Re-exports `process_layer_ingestion` from `app.services.mapspec.pipeline`.
"""
from app.services.mapspec.pipeline import process_layer_ingestion

__all__ = ["process_layer_ingestion"]
