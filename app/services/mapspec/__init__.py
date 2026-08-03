"""MapSpec Package (app/services/mapspec).

暴露深层 MapSpec 生命周期引擎 `MapSpecLifecycleEngine` 与意图值对象。
"""
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    mapspec_lifecycle_engine,
    MapSpecResult,
    InitProjectIntent,
    SetViewIntent,
    UpsertLayerIntent,
    RemoveLayerIntent,
    SetLayoutIntent,
    CheckpointIntent,
    RollbackIntent,
)
from app.services.mapspec.store import MapSpecStore, mapspec_store_instance

__all__ = [
    "MapSpecLifecycleEngine",
    "mapspec_lifecycle_engine",
    "MapSpecResult",
    "InitProjectIntent",
    "SetViewIntent",
    "UpsertLayerIntent",
    "RemoveLayerIntent",
    "SetLayoutIntent",
    "CheckpointIntent",
    "RollbackIntent",
    "MapSpecStore",
    "mapspec_store_instance",
]
