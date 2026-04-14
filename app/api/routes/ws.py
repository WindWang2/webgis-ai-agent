from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import logging
import json
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])

from app.services.ws_service import manager, broadcast_ws_event
