from typing import Any, Dict, List
from app.core.config import settings

class PermissionFilter:
    @staticmethod
    def filter(raw_data: Any, user_role: str) -> Any:
        """
        Filter data based on user role:
        - admin: full access to all data
        - editor: can access most data except sensitive fields
        - user: can only access public data, sensitive fields removed
        - guest: only access basic public data
        """
        if user_role == "admin":
            return raw_data
        
        if isinstance(raw_data, dict) and "features" in raw_data:
            filtered_features = []
            for feature in raw_data["features"]:
                filtered_feature = PermissionFilter._filter_feature(feature, user_role)
                if filtered_feature:
                    filtered_features.append(filtered_feature)
            raw_data["features"] = filtered_features
        
        return raw_data

    @staticmethod
    def _filter_feature(feature: Dict[str, Any], user_role: str) -> Optional[Dict[str, Any]]:
        """Filter individual feature based on role"""
        properties = feature.get("properties", {})
        
        # Check if feature is public
        is_public = properties.get("is_public", True)
        if not is_public and user_role not in ["admin", "editor"]:
            return None
        
        # Remove sensitive fields for non-admin users
        sensitive_fields = ["owner_id", "contact_info", "internal_notes", "sensitive"]
        filtered_props = {k: v for k, v in properties.items() if k not in sensitive_fields or user_role == "admin"}
        
        # For guest users, remove even more fields
        if user_role == "guest":
            guest_allowed_fields = ["name", "address", "type", "geometry"]
            filtered_props = {k: v for k, v in filtered_props.items() if k in guest_allowed_fields}
        
        feature["properties"] = filtered_props
        return feature
