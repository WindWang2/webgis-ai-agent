"""Data Lifecycle — staleness 传播（P4 补强 D4 级联主面）。"""
from __future__ import annotations

import pytest

from app.services.data_lifecycle import service as LS


class _Rec:
    def __init__(self, artifact_id: str, status: str = "valid"):
        self.artifact_id = artifact_id
        self.status = status


class _Graph:
    def __init__(self, dependents: dict):
        self._dependents = dependents

    def dependents(self, artifact_id: str) -> list:
        return list(self._dependents.get(artifact_id, []))


@pytest.fixture()
def _registry(monkeypatch):
    """可编程 registry：records + graph + metadata 写入捕获。"""
    state = {"records": {}, "graph": _Graph({}), "writes": []}

    async def list_artifacts(session_id):
        return list(state["records"].values())

    def build_graph(records):
        return state["graph"]

    async def update_meta(session_id, artifact_id, *, status=None, metadata=None):
        state["writes"].append({"id": artifact_id, "status": status,
                                "metadata": metadata})
        return True

    monkeypatch.setattr("app.services.artifact_registry.list_artifacts",
                        list_artifacts)
    monkeypatch.setattr("app.services.artifact_registry.build_artifact_graph",
                        build_graph)
    monkeypatch.setattr("app.services.artifact_registry.update_record_metadata",
                        update_meta)
    return state


@pytest.mark.asyncio
async def test_unknown_source_reports_not_in_ledger(_registry) -> None:
    svc = LS.ArtifactLifecycleService()
    report = await svc.propagate_staleness("s1", "ghost-artifact")
    assert report.verdict
    assert report.not_in_ledger == ["ghost-artifact"]
    assert report.marked == []


@pytest.mark.asyncio
async def test_content_change_marks_valid_downstream_stale(_registry) -> None:
    from app.lib.data.fingerprints import ChangeClass

    _registry["records"] = {"src": _Rec("src"),
                            "dep": _Rec("dep")}
    _registry["graph"] = _Graph({"src": ["dep"]})
    svc = LS.ArtifactLifecycleService()
    report = await svc.propagate_staleness("s1", "src",
                                           change=ChangeClass.CONTENT)
    assert report.marked == ["dep"]
    assert report.verdict == "recompute"
    write = _registry["writes"][0]
    assert write["status"] == "stale"
    assert write["metadata"]["stale_source"] == "src"


@pytest.mark.asyncio
async def test_already_stale_downstream_not_remarked(_registry) -> None:
    from app.lib.data.fingerprints import ChangeClass

    _registry["records"] = {"src": _Rec("src"), "dep": _Rec("dep", status="stale")}
    _registry["graph"] = _Graph({"src": ["dep"]})
    report = await LS.ArtifactLifecycleService().propagate_staleness(
        "s1", "src", change=ChangeClass.CONTENT)
    assert report.already_stale == ["dep"]
    assert report.marked == []
    assert _registry["writes"] == []


@pytest.mark.asyncio
async def test_metadata_only_change_marks_stale_with_verdict(_registry) -> None:
    from app.lib.data.fingerprints import ChangeClass

    _registry["records"] = {"src": _Rec("src"), "dep": _Rec("dep")}
    _registry["graph"] = _Graph({"src": ["dep"]})
    report = await LS.ArtifactLifecycleService().propagate_staleness(
        "s1", "src", change=ChangeClass.METADATA_ONLY)
    # metadata-only → 裁决 stale（不同于 content 的 recompute）。
    assert report.verdict == "stale"
    assert report.marked == ["dep"]
    write = _registry["writes"][0]
    assert write["status"] == "stale"
    assert write["metadata"]["stale_verdict"] == "stale"
