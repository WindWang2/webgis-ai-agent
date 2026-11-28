"""性能基线（WP6）——中等包导出/导出物化与导入注册计时（确定性合成数据）。

跑法：``pytest tests/unit/project_exchange/test_perf_baseline.py -m perf -q``。
输出仅记录（打印），不做硬阈值断言 —— 数值进 planning/TEST_MATRIX.md，
劣化由 R1/R2 人工对比（无既有基线，本轮建立首轮基线）。
"""
import asyncio
import json
import time
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.database import Base, Engine, SessionLocal
from app.models.db_model import User
from app.models.project import Project, ProjectDataset
from app.services.data_delivery.source_resolver import DeliveryActor
from app.services.project_exchange import contracts as pc
from app.services.project_exchange.closure import read_project_closure
from app.services.project_exchange.exporter import _execute_export_sync
from app.services.project_exchange.importer import (
    _register_all,
)
from app.services.project_exchange.exporter import _resolve_all_payloads

pytestmark = pytest.mark.perf

_OWNER = "ex-perf-owner"


class _FakeToken:
    cancelled = False


class _Progress:
    def __call__(self, pct, msg):
        pass


def _fc(n):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": i,
         "geometry": {"type": "Point",
                      "coordinates": [104.0 + (i % 100) * 0.001,
                                      30.5 + (i // 100) * 0.001]},
         "properties": {"name": f"p{i}", "v": i, "cat": i % 5}}
        for i in range(n)]}


def _seed(db, data_dir: Path, per_dataset: int, datasets: int):
    from app.services.data_parser import get_upload_dir

    db.add(Project(id="proj-perf", name="perf", status="active"))
    for k in range(datasets):
        upload_dir = get_upload_dir(str(data_dir), str(700 + k))
        (upload_dir / "original.geojson").write_text(
            json.dumps(_fc(per_dataset), ensure_ascii=False),
            encoding="utf-8")
        db.add(ProjectDataset(
            id=f"ds_perf{k}", project_id="proj-perf",
            name=f"perf-ds-{k}",
            source_type="upload", source_ref=str(700 + k),
            crs="EPSG:4326"))
    db.commit()


@pytest.mark.perf
def test_medium_package_export_import_baseline(tmp_path, monkeypatch):
    """3 数据集 × 1000 要素：导出物化 + ZIP + 导入注册全程计时。"""
    Base.metadata.create_all(bind=Engine)
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    from app.services.project_artifact_promotion import (
        reset_content_store_root_cache,
    )

    reset_content_store_root_cache()
    data_dir = Path(str(settings.DATA_DIR))
    with SessionLocal() as db:
        if db.get(User, _OWNER) is None:
            db.add(User(id=_OWNER, username="perf",
                        email="perf@example.com",
                        password_hash="x", role="editor", is_active=True,
                        token_version=0))
            db.commit()
        _seed(db, data_dir, per_dataset=1000, datasets=3)

    t0 = time.perf_counter()
    with SessionLocal() as db:
        closure = read_project_closure(db, "proj-perf")
        resolved = asyncio.new_event_loop().run_until_complete(
            _resolve_all_payloads(closure, None, _FakeToken()))
        t1 = time.perf_counter()
        actor = DeliveryActor(user_id=_OWNER)
        outcome = _execute_export_sync(
            job_id=0, package_id=pc.new_package_id(), closure=closure,
            resolved=resolved, package_name="perf", actor=actor,
            session_id=None, token=_FakeToken(), progress=_Progress(),
            cancel_check=lambda: None)
    t2 = time.perf_counter()

    zip_path = data_dir / "exports" / outcome["filename"]
    import zipfile

    unpack = data_dir / "unpacked-perf"
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(unpack)
    manifest = json.loads(
        (unpack / pc.MANIFEST_PATH).read_text(encoding="utf-8"))
    from app.services.project_exchange.untrusted_zip import (
        extract_zip, inspect_zip,
    )

    inspection = inspect_zip(zip_path)
    t3 = time.perf_counter()
    staging = data_dir / "staging-perf"
    extract_zip(zip_path, inspection, staging,
                expected_hashes={f["path"]: f["sha256"]
                                 for f in manifest["files"]})
    t4 = time.perf_counter()

    with SessionLocal() as db:
        actor = DeliveryActor(user_id=_OWNER)
        mapping = _register_all(
            db, pc.format_gate(manifest), staging, actor, "perf-import",
            None, [], lambda: None)
        db.commit()
    t5 = time.perf_counter()

    size = zip_path.stat().st_size
    print(f"\n[perf] payload resolve: {t1 - t0:.2f}s; "
          f"materialize+zip: {t2 - t1:.2f}s; inspect: {t3 - t2:.2f}s; "
          f"extract: {t4 - t3:.2f}s; register: {t5 - t4:.2f}s; "
          f"zip bytes: {size} ({size / 1024:.0f} KiB); "
          f"datasets: {len(mapping.datasets)}")
    assert len(mapping.datasets) == 3
    # 确定性上界（防意外爆炸；非精确阈值）
    assert (t5 - t0) < 120, "全程超过 120s —— 检查退化"
