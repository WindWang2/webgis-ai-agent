"""F14 — EXPORT_DIR 单一真相契约（export_paths.exports_root）。

此前三轨并行取值：map.py import 期常量 / artifact_lifecycle import 期 Path /
artifact_registry.export_file_path 调用时实时 —— DATA_DIR 运行时覆写后
registry probe 与写盘/GC 指向不同目录。收口后所有消费方同一调用时派生：

1. 调用时取值：monkeypatch settings.DATA_DIR 后，map.py 路径助手、
   registry export_file_path、lifecycle sweep 看到同一新目录；
2. probe 一致性：写入方写下的成品，registry probe 立即可见（同根）；
3. GC 用户交付物护栏：无 .owner 边车且不匹配服务端生成器模式的文件
   **永不回收**（超龄也不删）；盖章文件照常按龄回收。
"""
import os
import time

import pytest

from app.core.config import settings
from app.services import artifact_lifecycle as lifecycle
from app.services import export_paths
from app.services.artifact_registry import (
    export_file_path,
    export_ref_exists,
)
from app.services.export_lineage import export_ref


@pytest.fixture
def runtime_data_dir(tmp_path, monkeypatch):
    """运行时覆写 DATA_DIR（模拟配置变更/monkeypatch 时机差）。"""
    target = tmp_path / "data-now"
    target.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(target))
    return target


def test_exports_root_follows_runtime_data_dir(runtime_data_dir):
    root = export_paths.exports_root()
    assert root == runtime_data_dir / "exports"
    # map.py 读/写助手与 registry/lifecycle 同一根
    from app.api.routes import map as map_mod

    assert map_mod._exports_dir_str() == str(root)
    assert map_mod._exports_dir_write() == str(root)
    assert export_file_path(export_ref("map_export_1_abc.png")) == root / (
        "map_export_1_abc.png")


def test_write_then_probe_agree_after_runtime_data_dir_change(
        runtime_data_dir):
    path = export_file_path(export_ref("map_export_9_feed.png"))
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"pdf-or-png")
    assert export_ref_exists("s1", export_ref("map_export_9_feed.png"))


def test_sweep_targets_call_time_root(runtime_data_dir):
    exports = export_paths.ensure_exports_root()
    old = exports / "map_export_1_deadbeefcafe.png"
    old.write_bytes(b"x")
    aged = time.time() - 30 * 86400
    os.utime(old, (aged, aged))

    import asyncio

    result = asyncio.run(lifecycle.sweep_aged_artifacts())
    assert result["exports_removed"] == 1
    assert not old.exists()


def test_sweep_never_removes_unstamped_foreign_files(runtime_data_dir):
    """GC 护栏：操作员放置的文件（无边车、非生成器命名）超龄也绝不回收。"""
    exports = export_paths.ensure_exports_root()
    foreign = exports / "quarterly_atlas_final.png"
    foreign.write_bytes(b"operator file")
    aged = time.time() - 30 * 86400
    os.utime(foreign, (aged, aged))

    import asyncio

    result = asyncio.run(lifecycle.sweep_aged_artifacts())
    assert result["exports_removed"] == 0
    assert foreign.exists()


def test_sweep_removes_stamped_files_without_generator_name(runtime_data_dir):
    """有 .owner 边车（导出路由盖章）即视为交付物，可按龄回收 —— geojson
    等用户命名前缀的成品不依赖生成器正则。"""
    exports = export_paths.ensure_exports_root()
    deliverable = exports / "quarterly_atlas_final.geojson"
    deliverable.write_bytes(b"{}")
    (exports / "quarterly_atlas_final.geojson.owner").write_text("u1")
    aged = time.time() - 30 * 86400
    os.utime(deliverable, (aged, aged))
    os.utime(exports / "quarterly_atlas_final.geojson.owner", (aged, aged))

    import asyncio

    result = asyncio.run(lifecycle.sweep_aged_artifacts())
    assert result["exports_removed"] == 1
    assert not deliverable.exists()
