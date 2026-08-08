"""
Geospatial Data Fabric: Dataset Fingerprint Service
Calculates deterministic hashes for dataset descriptors and data payloads to track data drift and integrity.
"""
import json
import hashlib
from typing import List, Dict, Any, Optional
from app.schemas.data_fabric_schema import DatasetDescriptor


class DatasetFingerprintService:
    """
    Deterministic dataset fingerprint calculation service for tracking data drift,
    cache validation, and data integrity verification.
    """

    def calculate_descriptor_fingerprint(self, descriptor: DatasetDescriptor) -> str:
        """
        Calculates SHA256 hash based on dataset descriptor metadata.
        """
        canonical_obj = {
            "id": descriptor.id,
            "source_type": descriptor.source_type,
            "geometry_type": descriptor.geometry_type,
            "srs": descriptor.srs,
            "bbox": descriptor.bbox,
            "feature_count": descriptor.feature_count,
            "fields": sorted(descriptor.fields, key=lambda f: f.get("name", "")) if descriptor.fields else [],
        }
        serialized = json.dumps(canonical_obj, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def calculate_data_fingerprint(self, features: List[Dict[str, Any]]) -> str:
        """
        Calculates SHA256 hash based on raw feature objects / GeoJSON features.
        """
        serialized = json.dumps(features, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def calculate_combined_fingerprint(
        self,
        descriptor: DatasetDescriptor,
        features: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        Calculates combined fingerprint hash incorporating descriptor and optional features.
        """
        desc_fp = self.calculate_descriptor_fingerprint(descriptor)
        if not features:
            return desc_fp

        data_fp = self.calculate_data_fingerprint(features)
        combined_payload = f"{desc_fp}:{data_fp}"
        return hashlib.sha256(combined_payload.encode("utf-8")).hexdigest()

    def verify_fingerprint(
        self,
        descriptor: DatasetDescriptor,
        expected_fingerprint: str,
        features: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        Verifies whether calculated fingerprint matches expected fingerprint.
        """
        computed = self.calculate_combined_fingerprint(descriptor, features)
        return computed == expected_fingerprint


# Global singleton instance
dataset_fingerprint_service = DatasetFingerprintService()
