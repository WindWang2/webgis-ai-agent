import functools
import inspect
from typing import Optional, List
from app.lib.geo_processor.core import GeoAnalysisResult, to_feature_collection

def spatial_operator(
    name: str,
    progress_pct: int = 20,
    feature_keys: Optional[List[str]] = None,
    normalize_geojson: bool = True,
):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            bound_args = inspect.signature(func).bind(*args, **kwargs)
            bound_args.apply_defaults()
            
            callback = bound_args.arguments.get("callback")
            if callback and callable(callback):
                callback(progress_pct, f"Executing {name} analysis...")
            
            if normalize_geojson:
                keys = list(bound_args.arguments.keys())
                if feature_keys is not None:
                    for key in feature_keys:
                        if key in bound_args.arguments and bound_args.arguments[key] is not None:
                            bound_args.arguments[key] = to_feature_collection(bound_args.arguments[key])
                else:
                    if "features" in bound_args.arguments and bound_args.arguments["features"] is not None:
                        val = bound_args.arguments["features"]
                        if isinstance(val, (dict, list)):
                            bound_args.arguments["features"] = to_feature_collection(val)
                    elif len(keys) > 1 and keys[1] not in ("cls", "self"):
                        first_arg_key = keys[1]
                        val = bound_args.arguments[first_arg_key]
                        if val is not None and isinstance(val, (dict, list)):
                            bound_args.arguments[first_arg_key] = to_feature_collection(val)

            try:
                result = func(*bound_args.args, **bound_args.kwargs)
                if isinstance(result, GeoAnalysisResult):
                    return result
                return GeoAnalysisResult(True, result, f"{name} analysis completed successfully")
            except Exception as e:
                return GeoAnalysisResult(False, None, f"{name} failed: {str(e)}")
        return wrapper
    return decorator
