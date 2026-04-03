"""
PR 检查汇总服务
整合所有 PR 检查项，执行检查并在 GitHub PR 上评论
"""
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import httpx

from app.core.config import settings
from app.services.pr_checker import (
    check_code_linting,
    check_coverage,
    check_bandit,
)
from app.services.feishu_notifier import send_pr_created, send_pr_check_failed
from app.services.pr_workflow.reviewer import assign_reviewer, parse_pr_file, detect_module

logger = logging.getLogger(__name__)


@dataclass
class PRCheckSummary:
    """PR 检查结果汇总"""
    pr_number: int
    passed: bool
    check_results: dict = field(default_factory=dict)
    comment_body: str = ""
    errors: list = field(default_factory=list)

    # 各检查项详细结果
    linter_passed: bool = True
    linter_output: str = ""

    coverage_passed: bool = True
    coverage_percent: float = 0.0

    security_passed: bool = True
    security_issue_count: int = 0

    commit_passed: bool = True
    commit_invalid_count: int = 0


def generate_pr_comment(check_result: PRCheckSummary) -> str:
    """
    生成 PR 评论内容
    
    Args:
        check_result: 检查结果
        
    Returns:
        Markdown 格式的评论内容
    """
    status_icon = "✅" if check_result.passed else "❌"
    
    lines = [
        f"## 🤖 PR 自动检查结果 {status_icon}",
        "",
    ]
    
    # 代码规范
    linter_status = "✅ 通过" if check_result.linter_passed else "❌ 未通过"
    lines.append(f"### 📝 代码规范 ({linter_status})")
    if check_result.linter_output:
        lines.append("```")
        lines.append(check_result.linter_output[:1500])
        lines.append("```")
    lines.append("")
    
    # 覆盖度
    cov_status = "✅ 通过" if check_result.coverage_passed else "❌ 未通过"
    lines.append(
        f"### 📊 测试覆盖度 ({cov_status})\n"
        f"- 当前: {check_result.coverage_percent:.1f}%\n"
        f"- 要求: ≥{settings.PR_MIN_COVERAGE_PERCENT}%"
    )
    lines.append("")
    
    # 安全
    sec_status = "✅ 通过" if check_result.security_passed else "⚠️ 存在问题"
    lines.append(
        f"### 🔒 安全扫描 ({sec_status})\n"
        f"- 高危: {check_result.security_issue_count}\n"
    )
    lines.append("")
    
    # 总体结论
    if not check_result.passed:
        lines.extend([
            "---",
            "**⚠️ 请修复以上问题后再次提交审核**",
        ])
    
    return "\n".join(lines)


def post_pr_comment(
    pr_number: int,
    comment_body: str,
    repo_owner: Optional[str] = None,
    repo_name: Optional[str] = None
) -> bool:
    """
    在 GitHub PR 上发布评论
    
    Args:
        pr_number: PR 编号
        comment_body: 评论内容
        repo_owner: 仓库所有者
        repo_name: 仓库名称
        
    Returns:
        是否成功
    """
    owner = repo_owner or settings.GITHUB_REPO_OWNER
    name = repo_name or settings.GITHUB_REPO_NAME
    
    if not settings.GITHUB_TOKEN or not owner or not name:
        logger.warning("GitHub 配置不完整，无法发布评论")
        return False
    
    url = f"https://api.github.com/repos/{owner}/{name}/issues s{pr_number}/comments"
    
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                url,
                json={"body": comment_body},
                headers={
                    "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "WebGIS-AI-Agent"
                }
            )
            
            if resp.status_code in (200, 201):
                logger.info(f"成功发布 PR #{pr_number} 评论")
                return True
            else:
                logger.error(f"发布评论失败: {resp.status_code}")
                return False
    except Exception as e:
        logger.exception(f"发布评论异常: {e}")
        return False


def trigger_full_pr_check(pr_data: dict, repo_data: dict) -> PRCheckSummary:
    """
    触发完整的 PR 检查流程
    
    Args:
        pr_data: PR 数据字典
        repo_data: 仓库数据字典
        
    Returns:
        PRCheckSummary 检查结果
    """
    pr_number = pr_data.get("number", 0)
    pr_title = pr_data.get("title", "")
    pr_url = pr_data.get("html_url", "")
    pr_sha = pr_data.get("head", {}).get("sha", "")
    author = pr_data.get("user", {}).get("login", "")
    
    logger.info(f"开始 PR #{pr_number} 的完整检查...")
    
    result = PRCheckSummary(pr_number=pr_number, passed=True)
    
    # 执行检查（在当前项目）
    try:
        # 代码规范
        if settings.PR_CHECK_ROUFF:
            import shutil
            ruff_path = shutil.which("ruff")
            if ruff_path:
                proc = subprocess.run(
                    ["ruff", "check", "./app"],
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                result.linter_passed = proc.returncode == 0
                result.linter_output = proc.stdout + proc.stderr
                result.check_results["ruff"] = {
                    "passed": result.linter_passed,
                    "output": result.linter_output[:2000]
                }
        
        # 覆盖度
        if settings.PR_CHECK_COVERAGE:
            try:
                proc = subprocess.run(
                    ["pytest", "./tests", "--cov=./app", "--cov-report=term-missing", "-v"],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                for line in (proc.stdout + proc.stderr).split("\n"):
                    if "TOTAL" in line:
                        match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", line)
                        if match:
                            result.coverage_percent = float(match.group(1))
                            result.coverage_passed = result.coverage_percent >= settings.PR_MIN_COVERAGE_PERCENT
                            break
                
                result.check_results["coverage"] = {
                    "passed": result.coverage_passed,
                    "percent": result.coverage_percent,
                    "threshold": settings.PR_MIN_COVERAGE_PERCENT
                }
                
                if not result.coverage_passed:
                    result.passed = False
            except Exception as e:
                logger.warning(f"覆盖度检查失败: {e}")
                result.errors.append(str(e))
        
        # 安全扫描
        if settings.PR_CHECK_BANDIT:
            import shutil
            bandit_path = shutil.which("bandit")
            if bandit_path:
                proc = subprocess.run(
                    ["bandit", "-r", "./app", "-f", "text"],
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                high_count = (proc.stdout + proc.stderr).count("[high]")
                med_count = (proc.stdout + proc.stderr).count("[medium]")
                
                result.security_passed = high_count == 0
                result.security_issue_count = high_count
                result.check_results["security"] = {
                    "passed": result.security_passed,
                    "high": high_count,
                    "medium": med_count
                }
                
                if not result.security_passed:
                    result.passed = False
    except Exception as e:
        logger.exception(f"检查过程异常: {e}")
        result.errors.append(str(e))
        result.passed = False
    
    # 生成分评论
    result.comment_body = generate_pr_comment(result)
    
    # 发布评论
    post_pr_comment(pr_number, result.comment_body)
    
    # 发送飞书通知
    if settings.ENABLE_FEISHU_NOTIFY:
        if not result.passed:
            send_pr_check_failed(
                pr_number,
                pr_title,
                result.errors or ["部分检查未通过"]
            )
    
    return result


__all__ = [
    "PRCheckSummary",
    "trigger_full_pr_check",
    "post_pr_comment",
    "generate_pr_comment",
]