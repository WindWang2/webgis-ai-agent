from typing import Any, Dict

class DataSourceAdapter:
    """Base interface for all data source adapters"""
    def query(self, query_params: Dict[str, Any]) -> Any:
        raise NotImplementedError("Subclasses must implement query method")
