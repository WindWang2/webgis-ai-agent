"""Runner-local J17 fixture: real frame product on a real owned conversation.

Deliberately a CLI, never an application endpoint (same contract as
scripts/seed_journey_retry.py). The browser signs in and creates a real
conversation through the production chat API; this script then drives the
REAL mutation pipeline (apply_gis_mutation with UpsertSource/UpsertLayer/
SetLayout/SetFrameProduct intents — the same engine the mapspec mutation
route and the frame_product Pi tools delegate to). No state is manufactured
outside the production write path; nothing is granted that the UI does not
already own.

Usage:
  E2E_MODE=real python -m scripts.seed_journey_frames \
      --session-id <sid> --user-id <uid> [--frame-kind time_series]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.db_model import Conversation, User


def _require_owned_stack(session_id: str, user_id: str) -> None:
    """Fail closed outside the runner-owned, authenticated SQLite stack."""
    data_dir = Path(settings.DATA_DIR).resolve()
    db_url = make_url(settings.DATABASE_URL)
    if (
        os.environ.get("E2E_MODE") != "real"
        or settings.is_production()
        or settings.AUTH_DISABLED
        or db_url.get_backend_name() != "sqlite"
        or not db_url.database
        or not Path(db_url.database).resolve().is_relative_to(data_dir)
    ):
        raise RuntimeError("J17 fixture requires the authenticated runner-owned SQLite stack")
    username = os.environ.get("E2E_USER")
    if not username or not session_id or not user_id:
        raise RuntimeError("J17 fixture requires E2E_USER and an existing owned conversation")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        conversation = db.get(Conversation, session_id)
        if (
            user is None
            or user.username != username
            or not user.is_active
            or conversation is None
            or conversation.user_id != user.id
        ):
            raise RuntimeError("J17 fixture session/user ownership mismatch")


def _three_years_fc() -> dict:
    feats = []
    for year in (2020, 2022, 2024):
        for i, d in enumerate(("jinjiang", "wuhou", "gaoxin")):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.08, 30.6]},
                "properties": {"year": year, "district": d, "per_10k": 21 + i * 7 + (year - 2020)},
            })
    return {"type": "FeatureCollection", "features": feats}


async def seed(session_id: str, frame_kind: str) -> dict:
    """落图层+帧+产品（真实 mutation 管线）；返回指纹供观察 POST 指纹门。"""
    from app.lib.cartography.quality_loop import cartographic_fingerprint
    from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine

    out = await _seed_intents(session_id, frame_kind)
    engine = MapSpecLifecycleEngine()
    spec = await engine.store.get_mapspec(session_id)
    out["mapspec_fingerprint"] = cartographic_fingerprint(spec if isinstance(spec, dict) else {})
    return out


async def _seed_intents(session_id: str, frame_kind: str) -> dict:
    from app.services.gis_world_state import apply_gis_mutation
    from app.services.mapspec.intents import (
        SetFrameProductIntent,
        SetLayoutIntent,
        UpsertLayerIntent,
        UpsertSourceIntent,
    )

    fc = _three_years_fc()
    layer_id = "lyr-j17-districts"
    await apply_gis_mutation(
        session_id,
        UpsertSourceIntent(
            source_id="src-j17",
            source={"type": "geojson", "inlineData": fc},
        ),
        origin="agent", actor="e2e-j17",
    )
    await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(
            layer={
                "id": layer_id, "type": "circle", "source": "src-j17",
                "visible": True,
                "legend_spec": {
                    "type": "graduated", "field": "per_10k",
                    "breaks": [20, 28, 36, 44], "method": "equal_interval",
                    "k": 3, "palette": "Blues",
                },
            },
        ),
        origin="agent", actor="e2e-j17",
    )
    frame_ids = ["fp-r-2020", "fp-r-2022", "fp-r-2024"]
    await apply_gis_mutation(
        session_id,
        SetLayoutIntent(frames=[
            {"id": fid, "title": fid[-4:]} for fid in frame_ids
        ]),
        origin="agent", actor="e2e-j17",
    )
    if frame_kind == "time_series":
        frames = [
            {
                "frame_id": fid,
                "title": fid[-4:],
                "legend": "shared",
                "sync_group": "A" if idx < 2 else "B",
                "data": {
                    "ref": f"inline:{layer_id}",
                    "layer_id": layer_id,
                    "field": "per_10k",
                    "where": {"op": "eq", "field": "year", "value": int(fid[-4:])},
                },
            }
            for idx, fid in enumerate(frame_ids)
        ]
        product_kind = "time_series"
    else:
        frames = [
            {
                "frame_id": fid,
                "title": fid[-4:],
                "data": {"ref": f"inline:{layer_id}", "layer_id": layer_id},
            }
            for fid in frame_ids
        ]
        product_kind = "detail_frames"
    result = await apply_gis_mutation(
        session_id,
        SetFrameProductIntent(
            config={
                "schema_version": "frame_product.v1",
                "enabled": True,
                "product_kind": product_kind,
                "grid": {"columns": 3, "title": "J17 真实三幅"},
                "active_frame": frame_ids[0],
                "frames": frames,
                "shared_expression": {
                    "field": "per_10k", "semantics": "other",
                    "method": "equal_interval", "k": 3, "palette": "Blues",
                    "breaks": [20, 28, 36, 44], "domain_status": "complete",
                },
            },
        ),
        origin="agent", actor="e2e-j17",
    )
    revision = int(getattr(result, "mutation_revision", 0) or 0)
    return {"status": "ok", "frame_ids": frame_ids, "layer_id": layer_id, "mutation_revision": revision}


async def verify(session_id: str) -> dict:
    """读回真实 status 终验（含存储观察的逐帧行 —— G25 P6 服务端闭环）。"""
    from app.services.gis_harness.frame_products import service as fps

    status = await fps.frame_product_status(session_id)
    verification = status.get("verification") if isinstance(status, dict) else None
    return {
        "status": "ok",
        "bound": bool(status.get("bound")) if isinstance(status, dict) else False,
        "verification_summary": (verification or {}).get("summary"),
        "frames": [
            {"frame_id": r.get("frame_id"), "observation": r.get("observation")}
            for r in (verification or {}).get("frames", [])
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--frame-kind", choices=["time_series", "detail_frames"], default="time_series")
    parser.add_argument(
        "--mode", choices=["seed", "verify"], default="seed",
        help="seed=落图层/帧/产品并回指纹；verify=读回 status 终验（P6 闭环）",
    )
    args = parser.parse_args()
    _require_owned_stack(args.session_id, args.user_id)
    import asyncio

    if args.mode == "verify":
        out = asyncio.run(verify(args.session_id))
    else:
        out = asyncio.run(seed(args.session_id, args.frame_kind))
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
