"""回归：recipe 资格审计脚本对空 registry 不得崩溃。

对应 review-swarm 任务 P3-scripts_recipe_eligibility_audit.py-00：
registry.all_ids 为空时 rows 为空，未修复代码在 rows[0] 抛
IndexError，随后 with_fb / total 抛 ZeroDivisionError。
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class _EmptyRegistry:
    """all_ids 为空的 registry 替身（触发条件：知识库一条 recipe 都没有）。"""

    @property
    def all_ids(self) -> list:
        return []


def _run_main(monkeypatch, tmp_path: Path, registry) -> int:
    import app.services.gis_harness.recipes as recipes_mod
    import scripts.recipe_eligibility_audit as audit

    monkeypatch.setattr(recipes_mod, "get_recipe_registry", lambda: registry)
    monkeypatch.setattr(
        sys, "argv", ["recipe_eligibility_audit.py", "--csv", str(tmp_path / "m.csv")]
    )
    return audit.main()


def test_audit_survives_empty_registry(tmp_path, monkeypatch, capsys) -> None:
    """空 registry：不得 IndexError/ZeroDivisionError，出 header-only CSV。

    未修复代码：rows[0] 直接 IndexError（连 CSV 都写不出来）。
    """
    rc = _run_main(monkeypatch, tmp_path, _EmptyRegistry())

    assert rc == 0
    out = capsys.readouterr().out
    assert "total_recipes=0" in out
    assert "with_fallback_decls=0" in out

    csv_path = tmp_path / "m.csv"
    assert csv_path.exists(), "空 registry 也必须落盘 CSV"
    with csv_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 1, "空 registry 只应有 header 行"
    import scripts.recipe_eligibility_audit as audit

    assert rows[0] == list(audit._CSV_FIELDS)


def test_csv_fields_stay_in_sync_with_row_keys(tmp_path, monkeypatch) -> None:
    """_CSV_FIELDS 必须与真实 rows 的键同序（空路径的 header 唯一来源）。

    防止有人往 rows 里加列却忘了同步 _CSV_FIELDS，使空 registry 的
    header 与正常输出漂移。
    """
    import scripts.recipe_eligibility_audit as audit

    monkeypatch.setattr(
        sys, "argv", ["recipe_eligibility_audit.py", "--csv", str(tmp_path / "m.csv")]
    )
    rc = audit.main()
    assert rc == 0

    with (tmp_path / "m.csv").open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    assert header == list(audit._CSV_FIELDS)
