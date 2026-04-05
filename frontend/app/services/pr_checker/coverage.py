"""
PR 测试覆盖度检查器
"""
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings
logger = logging.getLogger(__name__)
@dataclass
class CoverageResult:
    """覆盖度检查结果"""
    passed: bool
    total_line_count: int
    covered_line_count: int
    coverage_percent: float
    output: str
    errors: list
    threshold: int = 80

def check_coverage(test_dir: str = "./tests", source_dirs: str = "./app") -> CoverageResult:
    """使用 pytest-cov 检查测试覆盖度"""
    if not settings.PR_CHECK_COVERAGE:
        return CoverageResult(
            passed=True,
            total_line_count=0,
            covered_line_count=0,
            coverage_percent=100.0,
            output="Coverage check disabled",
            errors=[],
            threshold=settings.PR_MIN_COVERAGE_PERCENT
        )
    
    pytest_path = shutil.which("pytest")
    if not pytest_path:
        return CoverageResult(
            passed=False, total_line_count=0, covered_line_count=0,
            coverage_percent=0.0, output="pytest 未安装", errors=["pytest not found"],
            threshold=settings.PR_MIN_COVERAGE_PERCENT
        )
    
    cmd = [
        "pytest", test_dir or "./tests",
        f"--cov={source_dir}",
        "--cov-report=term-missing",
        "--cov-report=json", "-v"
    ]
    min_cov = settings.PR_MIN_COVERAGE_PERCENT
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output = proc.stdout + proc.stderr
        
        cov_pct = 0.0
        m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", output)
        if m:
            cov_pct = float(m.group(1))
        
        passed = cov_pct >= min_cov
        return CoverageResult(
            passed=passed,
            total_line_count=0,
            covered_line_count=0,
            coverage_percent=cov_pct,
            output=output[:5000],
            errors=[] if passed else [f"Coverage {cov_pct}% < {min_cov}%"],
            threshold=min_cov
        )
    except subprocess.TimeoutExpired:
        return CoverageResult(False, 0, 0, 0.0, "timeout", ["timeout"], min_cov)
    except Exception as e:
        return CoverageResult(False, 0, 0, 0.0, str(e), [str(e)], min_cov)

def check_test_passing(test_dir: str = "./tests") -> tuple[bool, str]:
    """检查测试是否通过"""
    pytest_path = shutil.which("pytest")
    if not pytest_path:
        return False, "pytest not found"
    try:
        proc = subprocess.run(["pytest", test_dir, "-v", "--tb=short"],
                             capture_output=True, text=True, timeout=300)
        return proc.returncode == 0, proc.stdout + proc.stderr
    except Exception as e:
        return False, str(e)

__all__ = ["check_coverage", "check_test_passing", "CoverageResult"]