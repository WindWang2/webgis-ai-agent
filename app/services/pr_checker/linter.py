"""
PR 代码规范检查器 - Ruff/Black
"""
import asyncio
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional
from app.core.config import settings
logger = logging.getLogger(__name__)
@dataclass
class LintResult:
    """检查结果"""
    passed: bool
    tool: str
    output: str
    errors: List[str]
    warning_count: int = 0
    error_count: int = 0
def check_ruff(files: Optional[List[str]] = None) -> LintResult:
    """
    使用 ruff 检查代码规范
    
    Args:
        files: 要检查的文件列表，默认为空（检查所有 Python 文件）
        
    Returns:
        LintResult: 检查结果
    """"
    if not settings.PR_CHECK_ROUFF:
        return LintResult(passed=True, tool="ruff", output="Disabled", errors=[])
    
    # 检查 ruff 是否可用
    ruff_path = shutil.which("ruff")
    if not ruff_path:
        return LintResult(
            passed=False,
            tool="ruff",
            output="ruff 未安装，请运行: pip install ruff",
            errors=["ruff not found"]
        )
    
    cmd = ["ruff", "check"]
    if files:
        cmd.extend(files)
    else:
        # 检查 src 和 app 目录
        cmd.extend(["./app"])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = proc.stdout + proc.stderr
        error_lines = [line for line in output.split("\\n") if "error" in line.lower()]
        return LintResult(
            passed=proc.returncode == 0,
            tool="ruff",
            output=output or "检查完成",
            errors=error_line,
            error_count=len(error_line),
            warning_count=output.count("warning")
        )
    except subprocess.TimeoutExpired:
        return LintResult(passed=False, tool="ruff", output="检查超时", errors=["timeout"])
    except Exception as e:
        return LintResult(passed=False, tool="ruff", output=str(e), errors=[str(e)])
def check_black(files: Optional[List[str]] = None) -> LintResult:
    """
    使用 black 检查代码格式
    
    Args:
        files: 要检查的文件列表，默认为空（检查所有 Python 文件）
        
    Returns:
        LintResult: 检查结果
    """"
    if not settings.PR_CHECK_BLACK:
        return LintResult(passed=True, tool="black", output="Disabled", errors=[])
    black_path = shutil.which("black")
    if not black_path:
        return LintResult(
            passed=False,
            tool="black",
            output="black 未安装请运行: pip install black",
            errors=["black not found"]
        )
    cmd = ["black", "--check", "--diff"]
    if files:
        cmd.extend(files)
    else:
        cmd.append("./app")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = proc.stdout + proc.stderr
        return LintResult(
            passed=proc.returncode == 0,
            tool="black",
            output=output or "检查完成",
            errors=[line for line in output.split("\\n") if "error" in line.lower()],
            error_count=1 if proc.returncode != 0 else 0
        )
    except subprocess.TimeoutExpired:
        return LintResult(passed=False, tool="black", output="检查超时", errors=["timeout"])
    except Exception as e:
        return LintResult(passed=False, tool="black", output=str(e), errors=[str(e)])

def check_code_linting(pr_sha: str = "", files: Optional[List[str]] = None) -> dict:
    """
    综合代码规范检查
    
    Args:
        pr_sha: PR 的 SHA（可用于未来精确检查差异文件）
        files: 要检查的文件列表
        
    Returns:
        包含各项检查结果的字典
    """
    results = {}
    
    # Ruff 检查
    if settings.PR_CHECK_ROUFF:
        results["ruff"] = check_ruff(files)
    
    # Black 检查  
    if settings.PR_CHECK_BLACK:
        results["black"] = check_black(files)
    
    return results
__all__ = ["check_code_linting", "check_ruff", "check_black", "LintResult"]