"""校准脚本入库模式（ADR-0159 P6）回归锁。

覆盖任务书 §5 验收：``calibrate`` 脚本 ``--write`` 产出的 diff 报告正确
（dry-run 为默认）；建议值只作为 provisional 基线入库，**绝不改**现有
检查的硬编码默认值（``_carto_threshold`` / Settings.CARTO_* 一字不动）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.core.config import settings

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "calibrate_cartography_thresholds.py"


def _load_calibrate_module():
    spec = importlib.util.spec_from_file_location("calibrate_mod", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("calibrate_mod", module)
    spec.loader.exec_module(module)
    return module


_INPUT = {
    "checks": [
        {"rule": "carto.load.ratio", "status": "pass", "evidence": {"load_ratio": v}}
        for v in (0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.28, 0.34)
    ] + [
        {"rule": "carto.color.separability", "status": "pass",
         "evidence": {"min_adjacent_delta_e": v}}
        for v in (18.0, 15.5, 13.0, 11.5, 9.0, 7.5, 6.2, 5.4)
    ],
}


def _db_path_for_async(sync_path: Path) -> str:
    return f"sqlite+aiosqlite:///{sync_path}"


def _count_baselines(sync_path: Path) -> int:
    engine = sa.create_engine(
        f"sqlite:///{sync_path}", connect_args={"check_same_thread": False}
    )
    with engine.connect() as conn:
        n = conn.execute(
            sa.text("SELECT COUNT(*) FROM cartography_quality_baselines")
        ).scalar_one()
    engine.dispose()
    return int(n)


def _defaults_snapshot():
    """硬编码默认值快照：--write 前后必须一字不变（P6 红线）。"""
    return {
        name: getattr(settings, name)
        for name in (
            "CARTO_LOAD_WARN_RATIO", "CARTO_LOAD_FAIL_RATIO",
            "CARTO_COLOR_SEP_WARN_DELTA_E", "CARTO_COLOR_SEP_FAIL_DELTA_E",
            "CARTO_LABEL_WARN_RATIO", "CARTO_LABEL_FAIL_RATIO",
            "CARTO_SVS_AREA_PX", "CARTO_VISUALVAR_WARN_COUNT",
            "CARTO_VISUALVAR_FAIL_COUNT",
        )
    }


def test_dry_run_is_default_and_writes_nothing(facts_db, capsys, tmp_path):
    calibrate = _load_calibrate_module()
    inp = tmp_path / "review.json"
    inp.write_text(json.dumps(_INPUT), encoding="utf-8")
    defaults = _defaults_snapshot()

    rc = calibrate.main([str(inp)])
    assert rc == 0
    assert "[dry-run] 未写入任何内容" in capsys.readouterr().out
    assert _count_baselines(facts_db) == 0
    assert _defaults_snapshot() == defaults


def test_write_stores_provisional_baselines_and_diff(facts_db, capsys, tmp_path):
    calibrate = _load_calibrate_module()
    inp = tmp_path / "review.json"
    inp.write_text(json.dumps(_INPUT), encoding="utf-8")
    diff_out = tmp_path / "diff.json"
    defaults = _defaults_snapshot()

    rc = calibrate.main([
        str(inp), "--write", "--diff-out", str(diff_out),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "已入库" in out
    # load_ratio: warn(p66) + fail(p90)；color.separability: warn(p33) + fail(p10)
    assert _count_baselines(facts_db) == 4
    # provisional（未 --activate）
    engine = sa.create_engine(
        f"sqlite:///{facts_db}", connect_args={"check_same_thread": False}
    )
    with engine.connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT check_id, value, status, direction, source "
            "FROM cartography_quality_baselines ORDER BY check_id"
        )).all()
    engine.dispose()
    by_key = {r[0]: r for r in rows}
    assert all(r[2] == "provisional" for r in rows)
    assert all(r[4] == "calibration" for r in rows)
    assert by_key["carto.load.ratio.warn"][3] == "high_bad"
    assert by_key["carto.color.separability.warn"][3] == "low_bad"
    # 分位数：存储的是 render 层的建议值（{:.2f} 取整后的 p66/p90）
    assert by_key["carto.load.ratio.warn"][1] == pytest.approx(0.18, abs=1e-3)
    assert by_key["carto.load.ratio.fail"][1] == pytest.approx(0.30, abs=1e-3)
    # diff 报告：首轮 old_baseline 为 null（诚实缺省）
    diff = json.loads(diff_out.read_text(encoding="utf-8"))
    keys = {c["key"] for c in diff["changes"]}
    assert {"carto.load.ratio.warn", "carto.color.separability.fail"} <= keys
    assert all(c["old_baseline"] is None for c in diff["changes"])
    assert _defaults_snapshot() == defaults


def test_write_diff_reports_delta_against_existing_baseline(facts_db, capsys, tmp_path):
    """第二轮校准：diff 报告给出 old→new 与 delta_pct。"""
    calibrate = _load_calibrate_module()
    inp = tmp_path / "review.json"
    inp.write_text(json.dumps(_INPUT), encoding="utf-8")
    assert calibrate.main([str(inp), "--write"]) == 0
    capsys.readouterr()

    shifted = {
        "checks": [
            {"rule": "carto.load.ratio", "status": "pass",
             "evidence": {"load_ratio": v}}
            for v in (0.10, 0.14, 0.18, 0.22, 0.26, 0.30, 0.36, 0.44)
        ],
    }
    inp2 = tmp_path / "review2.json"
    inp2.write_text(json.dumps(shifted), encoding="utf-8")
    diff_out = tmp_path / "diff2.json"
    rc = calibrate.main([str(inp2), "--write", "--diff-out", str(diff_out)])
    assert rc == 0
    diff = json.loads(diff_out.read_text(encoding="utf-8"))
    load_change = next(
        c for c in diff["changes"] if c["key"] == "carto.load.ratio.warn"
    )
    assert load_change["old_baseline"] is not None
    assert load_change["delta_pct"] is not None
    assert load_change["new_suggestion"] > load_change["old_baseline"]
