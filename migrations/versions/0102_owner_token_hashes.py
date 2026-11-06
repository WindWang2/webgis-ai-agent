"""Replace anonymous session/job plaintext credentials with one-way verifiers.

Rollback cannot recover plaintext. It revokes these anonymous credentials by
clearing them, retaining the history/job rows. Restore service with the new
application or issue fresh sessions; never reinterpret a hash as a credential.
"""
import hashlib
import os

from alembic import op
import sqlalchemy as sa

revision = "0102_owner_token_hashes"
down_revision = "0101_cluster_reservation_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    tables = [sa.Table(name, sa.MetaData(), autoload_with=connection)
              for name in ("conversations", "analysis_tasks")]
    has_credentials = any(connection.execute(
        sa.select(table.c.id).where(table.c.owner_token.is_not(None)).limit(1)
    ).first() is not None for table in tables)
    if has_credentials and os.environ.get("OWNER_TOKEN_MIGRATION_QUIESCED") != "1":
        raise RuntimeError(
            "0102 requires a maintenance cutover: stop ALL old API, Celery and other "
            "database writers, back up the database, then run migration with "
            "OWNER_TOKEN_MIGRATION_QUIESCED=1. Old and new credential formats cannot "
            "coexist. See docs/DEPLOYMENT.md."
        )
    for table in tables:
        last_id = None
        while True:
            query = sa.select(table.c.id, table.c.owner_token).where(table.c.owner_token.is_not(None))
            if last_id is not None:
                query = query.where(table.c.id > last_id)
            rows = connection.execute(query.order_by(table.c.id).limit(500)).all()
            if not rows:
                break
            for identity, token in rows:
                connection.execute(table.update().where(table.c.id == identity).values(
                    owner_token=hashlib.sha256(str(token).encode("utf-8")).hexdigest()
                ))
            last_id = rows[-1][0]


def downgrade() -> None:
    connection = op.get_bind()
    for name in ("conversations", "analysis_tasks"):
        table = sa.Table(name, sa.MetaData(), autoload_with=connection)
        connection.execute(table.update().values(owner_token=None))
