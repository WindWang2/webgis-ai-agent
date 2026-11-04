"""Default-org resolution must preserve DAO transaction boundaries."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def test_default_org_flush_preserves_caller_savepoint(tmp_path):
    from app.core.tenancy import get_or_create_default_org_id_sync
    from app.models.db_model import Organization

    engine = create_engine(f"sqlite:///{tmp_path / 'tenancy.db'}")
    Organization.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE pending_work (id INTEGER PRIMARY KEY)"))
    try:
        with Session(engine) as db:
            db.execute(text("INSERT INTO pending_work VALUES (1)"))
            savepoint = db.begin_nested()
            org_id = get_or_create_default_org_id_sync(db, commit=False)
            assert org_id
            assert savepoint.is_active
            savepoint.commit()
            db.rollback()
        with Session(engine) as db:
            assert db.execute(text("SELECT count(*) FROM pending_work")).scalar() == 0
            assert db.execute(text("SELECT count(*) FROM organizations")).scalar() == 0
            # A rolled-back ID must never leak through the process cache.
            new_id = get_or_create_default_org_id_sync(db, commit=False)
            assert db.get(Organization, int(new_id)) is not None
    finally:
        engine.dispose()


def test_default_org_first_write_rolls_back_without_prior_write():
    from app.core.tenancy import get_or_create_default_org_id_sync
    from app.models.db_model import Organization

    engine = create_engine("sqlite:///:memory:")
    Organization.__table__.create(engine)
    try:
        with Session(engine) as db:
            org_id = get_or_create_default_org_id_sync(db, commit=False)
            assert db.get(Organization, int(org_id)) is not None
            db.rollback()
        with Session(engine) as db:
            assert db.execute(text("SELECT count(*) FROM organizations")).scalar() == 0
    finally:
        engine.dispose()
