"""Deep-review API/DATA batch — DATA-06 CHECK-constraint drift guard.

静态闸（Windows 也可跑）：模型声明的每条 CHECK 约束名必须出现在某个迁移里；
0094 的谓词必须与模型 ``__table_args__`` 一字不差。
"""
from __future__ import annotations

import importlib
from pathlib import Path

import app.models.ads_fabric  # noqa: F401
import app.models.cartography_quality  # noqa: F401
import app.models.data_fabric  # noqa: F401
import app.models.data_lifecycle  # noqa: F401
import app.models.data_quality  # noqa: F401
import app.models.db_model  # noqa: F401
import app.models.lakehouse_catalog  # noqa: F401
import app.models.lakehouse_datasets  # noqa: F401
import app.models.mission  # noqa: F401
import app.models.project  # noqa: F401
import app.models.report  # noqa: F401
import app.models.spatial_events  # noqa: F401
import app.models.template_version  # noqa: F401
import app.models.upload  # noqa: F401
from app.core.database import Base

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO / "migrations" / "versions"


def _model_checks() -> dict:
    out = {}
    for tname, table in Base.metadata.tables.items():
        for c in table.constraints:
            if c.__class__.__name__ == "CheckConstraint" and c.name:
                out[(tname, c.name)] = str(c.sqltext)
    return out


def test_every_model_check_is_created_by_a_migration():
    mig_text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in MIGRATIONS.glob("*.py")
    )
    missing = [
        f"{t}.{name}" for (t, name) in _model_checks() if name not in mig_text
    ]
    assert missing == [], f"模型 CHECK 未被任何迁移创建: {missing}"


def test_0094_checks_match_model_predicates_exactly():
    mod = importlib.import_module(
        "migrations.versions.0094_model_check_constraint_drift"
    )
    specs = {(table, name): expr for table, name, expr in mod._CHECKS}
    assert len(specs) == 10
    models = _model_checks()
    for key, expr in specs.items():
        assert key in models, f"0094 声明了模型不存在的约束: {key}"
        assert models[key] == expr, (
            f"{key[0]}.{key[1]} 谓词漂移: 0094={expr!r} 模型={models[key]!r}"
        )
