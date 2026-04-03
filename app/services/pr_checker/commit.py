"""PR 提交信息检查器 - Conventional Commits"""
import logging
import re
from dataclasses import dataclass
from app.core.config import settings
logger = logging.getLogger(__name__)
COMMIT_PATTERN = re.compile(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\(.+\))?: .{1,50}")
@dataclass
class CommitCheckResult:
    passed: bool
    valid_commits: int
    invalid_commits: list
    output: str
def check_conventional_commits(commits: list[str]) -> CommitCheckResult:
    if not settings.PR_CHECK_COMMIT:
        return CommitCheckResult(True, 0, [], "Disabled")
    if not commits:
        return CommitCheckResult(False, 0, [], "No commits provided")
    valid_cnt = 0
    invalid_list = []
    for idx, msg in enumerate(commits):
        msg = msg.strip()
        if COMMIT_PATTERN.match(msg):
            valid_cnt += 1
        else:
            invalid_list.append(f"Commit {idx+1}: {msg[:50]}...")
    passed = len(invalid_list) == 0
    output = f"Valid: {valid_cnt}/{len(commits)}"
    return CommitCheckResult(passed, valid_cnt, invalid_list, output)
__all__ = ["check_conventional_commits", "CommitCheckResult"]