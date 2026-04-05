import requests
from typing import Any, Dict
from app.core.config import settings
from .base import DataSourceAdapter

class ThirdPartyAPIAdapter(DataSourceAdapter):
    def query(self, query_params: Dict[str, Any]) -> Any:
        """
        Query third-party GIS APIs:
        Supported params: api_provider (gaode/tianditu), api_type (poi/road/geocode), keywords, bbox, location
        Returns standardized GeoJSON FeatureCollection
        """
        api_provider = query_params.get("api_provider", "gaode")
        api_type = query_params.get("api_type", "poi")

        if api_provider == "gaode":
            return self._query_gaode_api(api_type, query_params)
        elif api_provider == "tianditu":
            return self._query_tianditu_api(api_type, query_params)
        else:
            raise ValueError(f"Unsupported API provider: {api_provider}")

    def _query_gaode_api(self, api_type: str, params: Dict[str, Any]) -> Any:
        """Query Amap (Gaode) API"""
        base_url = "https://restapi.amap.com/v3"
        api_key = settings.GAODE_API_KEY
        if not api_key:
            raise Exception("Gaode API key not configured")

        request_params = {"key": api_key}

        if api_type == "poi":
            request_params.update({
                "keywords": params.get("keywords", ""),
                "types": params.get("types", ""),
                "city": params.get("city", ""),
                "offset": params.get("limit", 20),
                "page": params.get("page", 1),
                "extensions": "all"
            })
            response = requests.get(f"{base_url}/place/text", params=request_params)
            data = response.json()
            # Convert Gaode POI response to GeoJSON
            features = []
            for poi in data.get("pois", []):
                if poi.get("location"):
                    lon, lat = map(float, poi["location"].split(","))
                    features.append({
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [lon, lat]},
                        "properties": {
                            "name": poi.get("name"),
                            "address": poi.get("address"),
                            "type": poi.get("type"),
                            "province": poi.get("pname"),
                            "city": poi.get("cityname"),
                            "district": poi.get("adname")
                        }
                    })
            return {"type": "FeatureCollection", "features": features}
        else:
            raise ValueError(f"Unsupported Gaode API type: {api_type}")

    def _query_tianditu_api(self, api_type: str, params: Dict[str, Any]) -> Any:
        """Query Tianditu API"""
        # Implementation similar to Gaode, can be extended as needed
        raise NotImplementedError("Tianditu API support coming soon")
