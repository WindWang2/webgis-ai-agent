"""COG 工件发布纪律（raster_materialization）：原子 move 不得泄漏临时文件。"""
from __future__ import annotations


async def test_same_dir_publish_leaves_no_tmp_file(tmp_path, monkeypatch):
    """同目录发布走 os.replace 分支：先前 mkstemp 造出的空 .tmp 必须删掉，
    否则每次发布在会话 fabric-cog 目录泄漏一个孤儿临时文件（评审 P2）。"""
    from pathlib import Path

    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))

    from app.services.data_fabric.raster_materialization import publish_cog_artifact

    # durable 发布 / 台账注册与本次断言正交（且各自 best-effort）：隔离掉，
    # 只验 _move 的文件系统纪律。
    monkeypatch.setattr(
        "app.services.lakehouse.raster_object.publish_cog_data_object",
        lambda *a, **k: {"published": False, "reason": "test-isolation"},
    )

    async def _noop_register(*a, **k):
        return None

    monkeypatch.setattr("app.services.artifact_registry.register_artifact", _noop_register)

    # 源文件与目标同目录 → 走 os.replace 分支（缺陷分支）
    cog_dir = Path(settings.DATA_DIR) / "sess-tmp-leak" / "fabric-cog"
    cog_dir.mkdir(parents=True, exist_ok=True)
    source_path = cog_dir / "window-source.tif"
    source_path.write_bytes(b"pretend-cog-bytes")

    receipt = await publish_cog_artifact(
        session_id="sess-tmp-leak",
        source_path=source_path,
        title="leak probe",
        producer_tool="pytest",
        provenance={},
    )
    assert receipt["status"] == "success"
    final_path = Path(receipt["path"])
    assert final_path.parent == cog_dir
    assert final_path.is_file()
    assert not source_path.exists()  # os.replace 语义：源被移动走
    leftovers = sorted(p.name for p in cog_dir.iterdir() if p.suffix == ".tmp")
    assert leftovers == []
