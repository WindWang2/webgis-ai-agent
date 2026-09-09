"""Semantic Integration Manifest（Quality V3 W6，架构 §E）。

每个分支/PR 一份机器可读清单：touched domains、tools/algorithms/
capabilities、API routes、migrations、frontend 契约、events、共享文件、
风险与必需集成车道。merge simulation（W8）与影响选择（W7）的底座。

语义提取定位（诚实披露）：
- **文件 → domain 归属是精确的**（ownership 规则第一命中 + 不相交校验）；
- **语义 ID 提取是尽力而为的**（工具/算法 `name="..."` 字符串匹配），
  `ids_extracted=False` 表示降级为文件级清单——消费方不得把缺 ID 解读为
  "无语义变化"；
- 确定性：输出按排序键稳定序列化，身份 = (branch, base, git commit)，
  **无时间戳**。
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.lib.integration.ownership import (
    OwnershipDocument,
    load_document,
    risk_of_path,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

#: 默认基线：远端 master（merge-base 三点 diff）
DEFAULT_BASE = "origin/master"

_TOOL_NAME_RE = re.compile(r"""\bname\s*=\s*["']([a-z0-9_][a-z0-9_.\-]*)["']""")
_FRONTEND_CONTRACT_RE = re.compile(
    r"frontend/src/.*\.(?:ts|tsx)$"
)
_FRONTEND_CONTRACT_HINT_RE = re.compile(
    r"(types?|contract|schema|api|client|store|reconcile|mapspec|mapspec)", re.I
)

#: 高风险 → 必须执行的集成车道兜底（ownership suites 之外）
_RISK_SUITES = {"high": ("quick", "integration"), "medium": ("quick",)}


@dataclass
class IntegrationManifest:
    branch: str
    base: str
    git_commit: str
    files_changed: List[str] = field(default_factory=list)
    domains: Dict[str, List[str]] = field(default_factory=dict)
    unclassified: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    algorithms: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    api_routes: List[str] = field(default_factory=list)
    migrations: List[str] = field(default_factory=list)
    frontend_contracts: List[str] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    shared_files: List[str] = field(default_factory=list)
    risk_level: str = "none"          # none | medium | high
    risk_reasons: List[str] = field(default_factory=list)
    required_suites: List[str] = field(default_factory=list)
    ids_extracted: bool = True        # 语义 ID 提取是否成功（False=降级）

    def as_dict(self) -> Dict[str, object]:
        return {
            "branch": self.branch,
            "base": self.base,
            "git_commit": self.git_commit,
            "files_changed": self.files_changed,
            "domains": {k: sorted(v) for k, v in sorted(self.domains.items())},
            "unclassified": self.unclassified,
            "tools": self.tools,
            "algorithms": self.algorithms,
            "capabilities": self.capabilities,
            "api_routes": self.api_routes,
            "migrations": self.migrations,
            "frontend_contracts": self.frontend_contracts,
            "events": self.events,
            "shared_files": self.shared_files,
            "risk": {"level": self.risk_level, "reasons": self.risk_reasons},
            "required_suites": self.required_suites,
            "ids_extracted": self.ids_extracted,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True,
                          indent=1) + "\n"


def _git(args: List[str], repo_root: Path, timeout: int = 60) -> str:
    proc = subprocess.run(["git"] + args, cwd=repo_root, capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败: {proc.stderr.strip()[:300]}")
    return proc.stdout


def changed_files(branch: str, base: str, repo_root: Path,
                  include_worktree: bool = False) -> List[str]:
    """base...branch 三点 diff（merge-base 之后）；可选并入工作区未提交改动。"""
    files = set(_git(["diff", "--name-only", f"{base}...{branch}"],
                     repo_root).splitlines())
    if include_worktree:
        files |= set(_git(["diff", "--name-only", "HEAD"],
                          repo_root).splitlines())
    return sorted(f for f in files if f)


def build_manifest(
    branch: str,
    base: str = DEFAULT_BASE,
    repo_root: Optional[Path] = None,
    doc: Optional[OwnershipDocument] = None,
    include_worktree: bool = False,
) -> IntegrationManifest:
    """从 git diff + ownership 规则构建分支清单（纯函数式，无 IO 副作用）。"""
    root = repo_root or REPO_ROOT
    doc = doc or load_document(root)
    files = changed_files(branch, base, root, include_worktree)
    commit = _git(["rev-parse", branch], root).strip()

    m = IntegrationManifest(branch=branch, base=base, git_commit=commit,
                            files_changed=files)
    risk_reasons: List[str] = []
    risk_hits: List[str] = []   # 命中 high 的文件（结构化风险信号）
    medium_hits = 0
    extraction_failures = 0     # 语义文件改了但提取不到 ID（降级披露）
    suites: set = {"quick"}

    for path in files:
        risk, rule = risk_of_path(doc, path)
        if rule is None:
            m.unclassified.append(path)
        else:
            m.domains.setdefault(rule.owner, []).append(path)
            if rule.policy in doc.conflicting_policies:
                m.shared_files.append(path)
                risk_reasons.append(
                    f"{path}: {rule.policy}（owner={rule.owner}）")
            if risk == "high":
                risk_hits.append(path)
            elif risk == "medium":
                medium_hits += 1
        # 语义面
        semantic_path = False
        if path.startswith("app/tools/"):
            semantic_path = True
            m.tools.extend(_extract_names(root / path))
        elif path.startswith("app/lib/gis/algorithms/"):
            semantic_path = True
            m.algorithms.extend(_extract_names(root / path))
        elif path.startswith("app/lib/gis/capabilities/"):
            semantic_path = True
            m.capabilities.extend(_extract_names(root / path))
        elif path.startswith("app/api/routes/"):
            m.api_routes.append(path)
        elif path.startswith("migrations/versions/"):
            m.migrations.append(path)
        elif _FRONTEND_CONTRACT_RE.match(path) and \
                _FRONTEND_CONTRACT_HINT_RE.search(path):
            m.frontend_contracts.append(path)
        elif path == "app/lib/observability/events.py":
            m.events.extend(_event_categories(root / path))
        if semantic_path:
            extracted = _extract_names(root / path)
            if not extracted:
                extraction_failures += 1

        if rule is not None:
            suites.update(rule.suites)

    if m.migrations:
        risk_reasons.append(
            f"包含 {len(m.migrations)} 个 migration（分配协调 + up/down/up）")
        risk_hits.extend(m.migrations)

    # unclassified 不是风险：ownership 元契约只覆盖跨分支共享面，常规
    # 业务文件本就无归属（如实列出即可，不制造噪音）。

    m.risk_level = "high" if risk_hits else (
        "medium" if risk_reasons or medium_hits else "none")
    m.risk_reasons = sorted(set(risk_reasons))
    m.ids_extracted = extraction_failures == 0
    m.shared_files = sorted(set(m.shared_files))
    m.tools = sorted(set(m.tools))
    m.algorithms = sorted(set(m.algorithms))
    m.capabilities = sorted(set(m.capabilities))
    m.api_routes = sorted(set(m.api_routes))
    m.migrations.sort()
    m.frontend_contracts = sorted(set(m.frontend_contracts))
    m.events = sorted(set(m.events))
    m.unclassified.sort()
    if m.risk_level == "high":
        suites.update(_RISK_SUITES["high"])
    elif m.risk_level == "medium":
        suites.update(_RISK_SUITES["medium"])
    m.required_suites = sorted(suites)
    return m


def _extract_names(path: Path) -> List[str]:
    """尽力而为的语义 ID 提取（`name="..."` 描述符形态）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return _TOOL_NAME_RE.findall(text)


def _event_categories(path: Path) -> List[str]:
    """EVENT_CATALOG 的 category 集合（events.py 被改 = 词表可能漂移）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    from app.lib.observability.events import EVENT_CATALOG
    return sorted(EVENT_CATALOG.keys()) if "EVENT_CATALOG" in text else []
