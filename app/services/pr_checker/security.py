"""PR 安全扫描器 - Bandit"""
import logging
import shutil
import subprocess
from dataclasses import dataclass
from app.core.config import settings
logger = logging.getLogger(__name__)
@dataclass
class SecurityResult:
    passed: bool
    high_severity_count: int
    medium_severity_count: int
    low_severity_count: int
    output: str
    issue_details: list
def check_bandit(target_dir: str = "./app") -> SecurityResult:
    if not settings.PR_CHECK_BANDIT:
        return SecurityResult(True, 0, 0, 0, "Disabled", [])
    b_path = shutil.which("bandit")
    if not b_path:
        return SecurityResult(False, 0, 0, 0, "bandit 未安装", ["not found"])
    cmd = ["bandit", "-r", target_dir, "-f", "json", "-ll"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = proc.stdout + proc.stderr
        hi = mid = lo = 0
        try:
            import json
            data = json.loads(output.split("---")[0] if "---" in output else "{}")
            for r in data.get("results", []):
                sev = r.get("issue_severity", "LOW")
                if sev == "HIGH": hi += 1
                elif sev == "MEDIUM": mid += 1
                else: lo += 1
        except Exception:
            hi = output.count("[high]")
            mid = output.count("[medium]")
        passed = hi == 0 and mid <= 3
        return SecurityResult(passed, hi, mid, lo, output[:5_000], [])
    except subprocess.TimeoutExpired:
        return SecurityResult(False, 0, 0, 0, "timeout", ["timeout"])
    except Exception as e:
        return SecurityResult(False, 0, 0, 0, str(e), [str(e)])
__all__ = ["check_bandit", "SecurityResult"]