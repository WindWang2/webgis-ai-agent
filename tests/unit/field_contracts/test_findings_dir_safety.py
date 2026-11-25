"""G44 P3 回归：findings 落盘路径安全（service._findings_dir）。

session_id 为空 / "." / ".." 或 resolve 后逃出 field-contracts 根时必须拒绝 ——
修复前 ".." 会把 findings 写出 DATA_DIR 根。
"""
from __future__ import annotations

import pytest

from app.services.field_contracts import service as fc_service
from app.services.field_contracts.service import FieldContractError


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from app.core.config import settings

    target = tmp_path / "data"
    monkeypatch.setattr(settings, "DATA_DIR", str(target), raising=False)
    return target


@pytest.mark.parametrize("session_id", ["", ".", ".."])
def test_findings_dir_rejects_dot_and_empty_session_ids(data_dir, session_id):
    with pytest.raises(FieldContractError) as ei:
        fc_service._findings_dir(session_id)
    assert ei.value.code == "invalid_session_id"
    # 写路径同样拒绝：不产生任何落盘（尤其不写到 DATA_DIR 根）
    with pytest.raises(FieldContractError):
        fc_service._write_findings_atomic("asm1", session_id, {"version": 1})
    assert not (data_dir / "asm1.json").exists()


def test_findings_dir_rejects_resolved_escape(data_dir):
    root = data_dir / "field-contracts"
    root.mkdir(parents=True)
    outside = data_dir.parent / "outside"
    outside.mkdir()
    (root / "evil").symlink_to(outside, target_is_directory=True)
    with pytest.raises(FieldContractError) as ei:
        fc_service._findings_dir("evil")
    assert ei.value.code == "invalid_session_id"


def test_findings_dir_normal_session_stays_under_root(data_dir):
    d = fc_service._findings_dir("sess-ok")
    assert d.parent == (data_dir / "field-contracts").resolve()
