"""Data Lifecycle — 快照指针完整性披露（P4 补强 D1，只读审计面）。

manifest 的 durable_pointers 指向已删 blob → 披露为缺失；空项目/无目录
安全空结果。
"""
from __future__ import annotations

import json

import pytest

from app.services.data_lifecycle import quota as Q


@pytest.fixture()
def _snapshots(tmp_path, monkeypatch):
    pdir = tmp_path / "snapshots" / "p-int"
    pdir.mkdir(parents=True)

    def _fake_dir(project_id: str):
        return pdir if project_id == "p-int" else None

    monkeypatch.setattr("app.services.workspace.snapshot._project_snapshots_dir",
                        _fake_dir)

    def _write(name: str, manifest: dict) -> None:
        (pdir / name).write_text(json.dumps(manifest), encoding="utf-8")

    return _write


def test_empty_project_is_safe_empty_disclosure() -> None:
    out = Q.snapshot_pointer_integrity("")
    assert out == {"project_id": "", "snapshots_checked": 0,
                   "pointers_missing_total": 0, "items": []}


def test_unknown_project_directory_is_empty(_snapshots) -> None:
    out = Q.snapshot_pointer_integrity("p-other")
    assert out["snapshots_checked"] == 0
    assert out["pointers_missing_total"] == 0


def test_missing_blob_pointers_are_disclosed(_snapshots, tmp_path, monkeypatch) -> None:
    blobs = tmp_path / "blobs"
    (blobs / "a").mkdir(parents=True)
    (blobs / "a" / "exists.bin").write_bytes(b"x")

    class _Store:
        root = blobs

    monkeypatch.setattr("app.services.durable_blob_store.get_filesystem_blob_store",
                        lambda: _Store())
    _snapshots("snap-1.json", {
        "snapshot_id": "s1",
        "durable_pointers": {
            "art-ok": {"content_location": "a/exists.bin"},
            "art-gone": {"content_location": "a/gone.bin"},
        },
    })
    out = Q.snapshot_pointer_integrity("p-int")
    assert out["snapshots_checked"] == 1
    assert out["pointers_missing_total"] == 1
    assert out["items"] and out["items"][0]["snapshot_id"] == "s1"
    assert out["items"][0]["missing_pointers"] == ["art-gone"]


def test_manifest_without_pointers_is_clean(_snapshots, tmp_path, monkeypatch) -> None:
    class _Store:
        root = tmp_path / "empty-blobs"

    monkeypatch.setattr("app.services.durable_blob_store.get_filesystem_blob_store",
                        lambda: _Store())
    _snapshots("snap-2.json", {"snapshot_id": "s2"})
    out = Q.snapshot_pointer_integrity("p-int")
    # 无 durable_pointers 的 manifest 不计入 checked（无指针可审）。
    assert out["snapshots_checked"] == 0
    assert out["pointers_missing_total"] == 0
