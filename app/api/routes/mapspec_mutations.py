"""User-origin MapSpec mutations — thin adapter over apply_mutation (#639/#640)."""
from typing import Annotated, Any, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import require_owned_session
from app.models.db_model import Conversation
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    PatchLayerPresentationIntent,
    RemoveLayerIntent,
    ReorderLayersIntent,
    SetLayoutIntent,
    SetTimeIntent,
    SetViewIntent,
)

router = APIRouter(prefix="/chat", tags=["对话"])
_engine = MapSpecLifecycleEngine()


class PatchLayerPresentationBody(BaseModel):
    intent: Literal["patch_layer_presentation"]
    expected_revision: int = Field(ge=0)
    layer_id: str = Field(min_length=1, max_length=200)
    visible: Optional[bool] = None
    opacity: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class SetViewBody(BaseModel):
    intent: Literal["set_view"]
    expected_revision: int = Field(ge=0)
    center: Optional[list[float]] = None
    zoom: Optional[float] = None
    pitch: Optional[float] = None
    bearing: Optional[float] = None


class RemoveLayerBody(BaseModel):
    intent: Literal["remove_layer"]
    expected_revision: int = Field(ge=0)
    layer_id: str = Field(min_length=1, max_length=200)


class ReorderLayersBody(BaseModel):
    intent: Literal["reorder_layers"]
    expected_revision: int = Field(ge=0)
    layer_ids: list[str] = Field(min_length=1, max_length=128)


class SetLayoutBody(BaseModel):
    intent: Literal["set_layout"]
    expected_revision: int = Field(ge=0)
    legend: Optional[dict[str, Any]] = None
    controls: Optional[list[dict[str, Any]]] = None
    margins: Optional[dict[str, Any]] = None


class SetTimeBody(BaseModel):
    intent: Literal["set_time"]
    expected_revision: int = Field(ge=0)
    enabled: Optional[bool] = None
    field: Optional[str] = None
    type: Optional[str] = None
    extent: Optional[list[Any]] = None
    current: Optional[Any] = None
    window: Optional[Any] = None
    playback: Optional[dict[str, Any]] = None
    step: Optional[float] = None
    speed: Optional[float] = None


class InitProjectBody(BaseModel):
    intent: Literal["init_project"]
    expected_revision: int = Field(ge=0)
    view: Optional[dict[str, Any]] = None


UserMapSpecMutationRequest = Annotated[
    Union[
        PatchLayerPresentationBody,
        SetViewBody,
        RemoveLayerBody,
        ReorderLayersBody,
        SetLayoutBody,
        SetTimeBody,
        InitProjectBody,
    ],
    Field(discriminator="intent"),
]


@router.post("/sessions/{session_id}/mapspec/mutations")
async def apply_user_mapspec_mutation(
    session_id: str,
    req: UserMapSpecMutationRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    if isinstance(req, PatchLayerPresentationBody):
        if req.visible is None and req.opacity is None:
            raise HTTPException(
                status_code=400,
                detail="patch_layer_presentation requires visible and/or opacity",
            )
        intent = PatchLayerPresentationIntent(
            layer_id=req.layer_id,
            visible=req.visible,
            opacity=req.opacity,
        )
    elif isinstance(req, SetViewBody):
        if (
            req.center is None
            and req.zoom is None
            and req.pitch is None
            and req.bearing is None
        ):
            raise HTTPException(
                status_code=400,
                detail="set_view requires center, zoom, pitch, and/or bearing",
            )
        intent = SetViewIntent(
            center=req.center,
            zoom=req.zoom,
            pitch=req.pitch,
            bearing=req.bearing,
        )
    elif isinstance(req, RemoveLayerBody):
        intent = RemoveLayerIntent(layer_id=req.layer_id)
    elif isinstance(req, ReorderLayersBody):
        intent = ReorderLayersIntent(layer_ids=req.layer_ids)
    elif isinstance(req, SetLayoutBody):
        intent = SetLayoutIntent(
            legend=req.legend, controls=req.controls, margins=req.margins
        )
    elif isinstance(req, SetTimeBody):
        intent = SetTimeIntent(
            enabled=req.enabled,
            field=req.field,
            type=req.type,
            extent=req.extent,
            current=req.current,
            window=req.window,
            playback=req.playback,
            step=req.step,
            speed=req.speed,
        )
    elif isinstance(req, InitProjectBody):
        intent = InitProjectIntent(view=req.view)
    else:
        raise HTTPException(status_code=400, detail="unsupported mapspec mutation intent")
    result = await _engine.apply_mutation(
        session_id,
        intent,
        origin="user",
        expected_revision=req.expected_revision,
    )
    if result.superseded:
        raise HTTPException(status_code=409, detail=result.to_dict())
    if result.is_error:
        raise HTTPException(status_code=400, detail=result.to_dict())
    return result.to_dict()
