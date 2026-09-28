"""依赖上界守护（scripts/check_dependency_bounds.py）的回归测试（#1512，P1）。

把 #1512 的回归防线钉进测试套件：pandas 必须 <3.0、sqlalchemy 必须 <2.1、
greenlet 必须可解析、lock 不得与约束漂移。纯文本断言 —— 不跑 pip、不联网，
``--no-cov`` 友好。
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import check_dependency_bounds as guard  # noqa: E402

FIXED_REQ = """\
# Database & ORM
sqlalchemy>=2.0.25,<2.1
alembic>=1.13.0

# GIS Libraries
geopandas>=1.1.4
pandas>=2.2.2,<3.0
psycopg2-binary>=2.9.12
"""

BREAKER_REQ = """\
# 复刻 #1512 前的过宽约束
sqlalchemy>=2.0.25
geopandas>=1.1.4
pandas>=2.2.2,<4.0.0
"""

LOCK = """\
geopandas==1.1.4
    #   pandas
greenlet==3.5.6
    # via sqlalchemy
pandas==2.3.3
    #   geopandas
sqlalchemy==2.0.54
"""


def _write(tmp_path: Path, req: str, lock: str = LOCK, dev: str = "") -> tuple[Path, Path]:
    req_path = tmp_path / "requirements.txt"
    req_path.write_text(req, encoding="utf-8")
    lock_path = tmp_path / "requirements.lock"
    lock_path.write_text(lock, encoding="utf-8")
    dev_path = tmp_path / "requirements-dev.txt"
    dev_path.write_text(dev or "-r requirements.txt\npytest>=9.1.1\n", encoding="utf-8")
    return req_path, lock_path


class TestAdmits:
    """约束求值：放行/拒绝的语义单元。"""

    def test_fixed_bounds_admit_lock_versions(self):
        assert guard.admits(">=2.2.2,<3.0", "2.3.3")
        assert guard.admits(">=2.0.25,<2.1", "2.0.54")

    def test_fixed_bounds_reject_breakers(self):
        assert not guard.admits(">=2.2.2,<3.0", "3.0.6")
        assert not guard.admits(">=2.0.25,<2.1", "2.1.1")

    def test_unbounded_admits_everything(self):
        # #1512 根因形态：无上界 / 上界过宽 → 击穿版本被放行
        assert guard.admits(">=2.0.25", "2.1.1")
        assert guard.admits(">=2.2.2,<4.0.0", "3.0.6")

    def test_compatible_release_implies_ceiling(self):
        assert guard.admits("~=2.2", "2.3.3")
        assert not guard.admits("~=2.2", "3.0.6")
        assert not guard.admits("~=2.0.25", "2.1.0")

    def test_equality_and_exclusion(self):
        assert guard.admits("==2.3.3", "2.3.3")
        assert not guard.admits("==2.3.3", "2.3.4")
        assert not guard.admits("!=2.3.3", "2.3.3")

    def test_unparseable_spec_is_not_admitted(self):
        # 守护宁可误报也不漏报：看不懂的约束段按不放行处理
        assert not guard.admits(">=2.2.2,<banana", "2.3.3")

    def test_version_compare_pads_minor(self):
        assert guard._cmp("2.0.54", "2.1") < 0
        assert guard._cmp("3.0.6", "2.3.3") > 0
        assert guard._cmp("2.3.3", "2.3.3") == 0


class TestParseRequirements:
    def test_strips_comments_extras_and_markers(self, tmp_path):
        path = tmp_path / "requirements.txt"
        path.write_text(
            "uvicorn[standard]>=0.27.0  # 注释\n"
            'psycopg2-binary>=2.9.12; sys_platform == "linux"\n'
            "\n"
            "--index-url https://example.invalid/simple\n",
            encoding="utf-8",
        )
        specs = guard.parse_requirements(path)
        assert specs == {"uvicorn": ">=0.27.0", "psycopg2-binary": ">=2.9.12"}

    def test_follows_r_includes(self, tmp_path):
        (tmp_path / "base.txt").write_text("pandas>=2.2.2,<3.0\n", encoding="utf-8")
        dev = tmp_path / "requirements-dev.txt"
        dev.write_text("-r base.txt\npytest>=9.1.1\n", encoding="utf-8")
        specs = guard.parse_requirements(dev)
        assert specs["pandas"] == ">=2.2.2,<3.0"
        assert specs["pytest"] == ">=9.1.1"

    def test_lock_parsing_skips_via_comments(self, tmp_path):
        lock = tmp_path / "requirements.lock"
        lock.write_text(LOCK, encoding="utf-8")
        pins = guard.parse_lock(lock)
        assert pins["greenlet"] == "3.5.6"
        assert pins["pandas"] == "2.3.3"


class TestCheckGate:
    def test_fixed_bounds_are_clean(self, tmp_path):
        req, lock = _write(tmp_path, FIXED_REQ)
        assert guard.check([req], lock) == []

    def test_breaker_bounds_are_flagged(self, tmp_path):
        # #1512 现场复刻：两条根因约束都必须被抓住
        req, lock = _write(tmp_path, BREAKER_REQ)
        errors = guard.check([req], lock)
        joined = "\n".join(errors)
        assert any("pandas" in e and "3.0.6" in e for e in errors)
        assert any("sqlalchemy" in e and "2.1.1" in e for e in errors)
        assert "放行击穿版本" in joined

    def test_greenlet_missing_everywhere_is_flagged(self, tmp_path):
        req, lock = _write(tmp_path, FIXED_REQ, lock=LOCK.replace("greenlet==3.5.6\n", ""))
        errors = guard.check([req], lock)
        assert any("greenlet" in e for e in errors)

    def test_greenlet_declared_directly_is_clean(self, tmp_path):
        req, lock = _write(
            tmp_path, FIXED_REQ + "greenlet>=3\n",
            lock=LOCK.replace("greenlet==3.5.6\n", ""),
        )
        assert guard.check([req], lock) == []

    def test_lock_drift_is_flagged(self, tmp_path):
        # lock 陈旧：pin 了约束不容忍的版本
        req, lock = _write(tmp_path, FIXED_REQ, lock=LOCK.replace("pandas==2.3.3", "pandas==3.0.6"))
        errors = guard.check([req], lock)
        assert any("pandas==3.0.6" in e for e in errors)

    def test_missing_geopandas_is_flagged(self, tmp_path):
        req, lock = _write(tmp_path, FIXED_REQ.replace("geopandas>=1.1.4\n", ""))
        errors = guard.check([req], lock)
        assert any("geopandas" in e for e in errors)


class TestLiveRepoGate:
    """实测门：仓库当前状态必须干净（ oracle：退出码 0）。"""

    def test_repo_requirements_are_clean(self):
        errors = guard.check(
            [REPO / "requirements.txt", REPO / "requirements-dev.txt"],
            REPO / "requirements.lock",
        )
        assert errors == [], f"仓库依赖约束被 #1512 形态击穿：{errors}"

    def test_script_exits_zero_as_subprocess(self):
        proc = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "check_dependency_bounds.py")],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "[dependency-bounds] OK" in proc.stdout
