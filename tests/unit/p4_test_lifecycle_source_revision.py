"""Data Lifecycle — 变更检测与修订 token（P4 补强 D4 检测面）。"""
from __future__ import annotations

import pytest

from app.services.data_lifecycle import service as LS
from app.services.data_lifecycle.service import SourceRevision


class _Descriptor:
    """可编程 descriptor 通道。"""

    def __init__(self, descriptor=None):
        self._descriptor = descriptor

    async def get_ref_descriptor(self, session_id, ref):  # noqa: ANN001
        if isinstance(self._descriptor, Exception):
            raise self._descriptor
        return self._descriptor


@pytest.fixture()
def _descriptor_channel(monkeypatch):
    def _install(descriptor):
        channel = _Descriptor(descriptor)
        monkeypatch.setattr("app.services.session_data.session_data_manager",
                            channel)
    return _install


@pytest.mark.asyncio
async def test_current_revision_token_from_descriptor(_descriptor_channel) -> None:
    _descriptor_channel({"content_revision": 7, "crs": "EPSG:4326"})
    rev = await LS.ArtifactLifecycleService().current_source_revision("s1", "ref:x")
    assert rev is not None
    assert rev.revision_token == "7"
    assert rev.crs == "EPSG:4326"
    assert rev.source_type == "session_ref"


@pytest.mark.asyncio
async def test_descriptor_failure_yields_none(_descriptor_channel) -> None:
    _descriptor_channel(RuntimeError("store down"))
    assert await LS.ArtifactLifecycleService().current_source_revision(
        "s1", "ref:x") is None


@pytest.mark.asyncio
async def test_non_dict_descriptor_yields_none(_descriptor_channel) -> None:
    _descriptor_channel("not-a-dict")
    assert await LS.ArtifactLifecycleService().current_source_revision(
        "s1", "ref:x") is None


def test_source_revision_defaults_are_honest() -> None:
    rev = SourceRevision(source_type="session_ref", source_ref="ref:y",
                         revision_token="", crs="")
    assert rev.revision_token == ""  # 绝不虚构 token
    assert rev.crs == ""  # 未知 CRS 保持 NULL/空（INV-ART1 同纪律）
