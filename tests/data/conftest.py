"""tests/data 共享 fixture（Lakehouse V6 引入）。

BlobStore 单例的根经 ``project_artifact_promotion.content_store_root()``
的**进程级缓存**解析 —— 任何 monkeypatch ``settings.DATA_DIR`` 的测试若不
重置缓存，就会把 blob 写进前一个测试的（或仓库的）``./data``。此 fixture
在每个测试前后重置两级缓存，使内容根与当前测试的 DATA_DIR 严格一致。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_lakehouse_store_roots():
    from app.services.durable_blob_store import reset_filesystem_blob_store
    from app.services.project_artifact_promotion import reset_content_store_root_cache

    reset_filesystem_blob_store()
    reset_content_store_root_cache()
    yield
    reset_filesystem_blob_store()
    reset_content_store_root_cache()
