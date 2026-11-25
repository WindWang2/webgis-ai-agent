"""任务中心视图的安全摘要边界（app/services/jobs/views.py）。"""
from types import SimpleNamespace

from app.services.jobs.views import _import_member_status


def _record(member: dict) -> SimpleNamespace:
    return SimpleNamespace(job_kind="data_import", result_summary={"members": [member]})


def test_member_status_rejects_unbounded_row_count_shapes():
    """input_rows/rejected_rows 只能是 [0, 1e9] 的 int，其他形状一律 None。"""
    out = _import_member_status(
        _record({"member_key": "m1", "input_rows": "x" * 100_000, "rejected_rows": {"n": 1}})
    )
    assert out[0]["input_rows"] is None
    assert out[0]["rejected_rows"] is None


def test_member_status_keeps_valid_row_counts():
    out = _import_member_status(
        _record({"member_key": "m1", "input_rows": 12, "rejected_rows": 0})
    )
    assert out[0]["input_rows"] == 12
    assert out[0]["rejected_rows"] == 0


def test_member_status_rejects_negative_and_out_of_range_counts():
    out = _import_member_status(_record({"input_rows": -1, "rejected_rows": 10**10}))
    assert out[0]["input_rows"] is None
    assert out[0]["rejected_rows"] is None
