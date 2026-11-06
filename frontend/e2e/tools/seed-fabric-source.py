"""Register the runner-owned PostGIS fixture through the real fabric manager.

This is a trusted deployment fixture, not a public API or SSRF override. The
browser separately proves private-source registration remains forbidden.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

SOURCE_NAME = "nightly postgis fixture"


def seed_source() -> None:
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models.db_model import User
    from app.models.data_fabric import CatalogItemModel, DataSourceModel
    from app.schemas.data_fabric_schema import QuerySpec
    from app.services.data_fabric.manager import DataFabricManager

    if os.environ.get("E2E_MODE") != "real" or settings.ENV != "development":
        raise RuntimeError("fabric fixture is only for the real development journey lane")
    database = make_url(settings.DATABASE_URL)
    if (
        settings.AUTH_DISABLED or database.get_backend_name() != "sqlite"
        or not database.database
        or not Path(database.database).resolve().is_relative_to(Path(settings.DATA_DIR).resolve())
    ):
        raise RuntimeError("fabric fixture requires authenticated runner-owned SQLite")
    dsn = os.environ["E2E_POSTGIS_DSN"]
    parsed = urlsplit(dsn)
    if (parsed.scheme, parsed.hostname, parsed.port, parsed.path) != (
        "postgresql", "localhost", 5432, "/gis",
    ) or parsed.query or parsed.fragment:
        raise RuntimeError("fabric fixture must target the runner's localhost:5432/gis service")
    username = os.environ["E2E_USER"]
    with SessionLocal() as db:
        owner = db.query(User).filter(User.username == username).one()
        if not owner.is_active or owner.role != "admin":
            raise RuntimeError("fixture owner must be the provisioned active admin")
        if db.query(DataSourceModel).filter(DataSourceModel.name == SOURCE_NAME).first():
            raise RuntimeError("fabric fixture requires a fresh runner database")
        source = DataFabricManager.create_data_source(
            db, name=SOURCE_NAME, source_type="postgis", endpoint_url=dsn,
            allow_private=True, org_id=owner.org_id, owner_id=owner.id,
        )
        if source.status != "healthy":
            raise RuntimeError("PostGIS fixture did not pass the real connection probe")
        item = db.query(CatalogItemModel).filter(
            CatalogItemModel.source_id == source.id,
            CatalogItemModel.name == "public.poi_commercial",
            CatalogItemModel.availability == "available",
        ).one()
        if item.descriptor_json.get("feature_count") != 6000:
            raise RuntimeError("PostGIS fixture catalog must describe all 6000 real rows")
        result = DataFabricManager.query_catalog_item(db, item.id, QuerySpec(limit=10))
        if result.is_demo or len(result.features) != 10:
            raise RuntimeError("PostGIS fixture must return 10 real feature rows")
        if any(feature.get("geometry", {}).get("type") != "Point" for feature in result.features):
            raise RuntimeError("PostGIS fixture query returned invalid geometry")
        if any(not feature.get("properties", {}).get("name", "").startswith("poi_") for feature in result.features):
            raise RuntimeError("PostGIS fixture query did not read the seeded table")
        sys.stdout.write("Owned PostGIS fixture verified: healthy, 6000 catalog rows, 10 queried points\n")


if __name__ == "__main__":
    seed_source()
