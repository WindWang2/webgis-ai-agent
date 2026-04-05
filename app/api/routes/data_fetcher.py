from fastapi import APIRouter, Depends, HTTPException
from typing import Any, Dict
from app.services.data_fetcher import DataFetcherService, DataQuery, StandardGISData
from app.api.deps import get_current_user

router = APIRouter()
data_fetcher = DataFetcherService()

@router.post("/query", response_model=StandardGISData, summary="Query GIS data from any source")
def query_data(
    query: DataQuery,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Query GIS data from multiple sources with unified interface:
    - **data_source**: Data source type (postgis/oss/third_party_api/local_file)
    - **query_params**: Source-specific query parameters
    - **user_role**: Automatically filled from current user, can be overridden for testing
    - **skip_cache**: Set to true to bypass cache and fetch fresh data
    """
    # Fill user info from current user if not provided
    if not query.user_id and current_user:
        query.user_id = current_user.get("user_id")
    if not query.user_role and current_user:
        query.user_role = current_user.get("role", "user")

    result = data_fetcher.query(query)
    if not result.success:
        raise HTTPException(status_code=500, detail=result.error_message)
    return result

@router.post("/invalidate-cache", summary="Invalidate cache for a specific query")
def invalidate_cache(
    query: DataQuery,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Invalidate cached data for a specific query"""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only admins can invalidate cache")
    
    data_fetcher._cache_manager.invalidate(query)
    return {"status": "success", "message": "Cache invalidated"}
