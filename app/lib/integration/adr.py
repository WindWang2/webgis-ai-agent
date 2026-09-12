"""ADR 协调（Quality V3 W4，架构 §C）。

审计实证（00-baseline P0-1）：``docs/adr/`` 已有 6×ADR-0118 撞号——6 个
并发分支各自"扫 max+1"分配了同一编号。本模块提供：

- ``scan``：解析既有 ADR（标题编号、文内引用），规则派生自实际语料
  （M-8）：标题形如 ``# ADR NNNN — Title``；引用形如 ``ADR NNNN`` /
  ``ADR-NNNN``；关系标签为中文语料的 前置/关联/取代/supersedes 等。
- watermark 机制（C-2）：``ownership.json.adr_watermark`` 之下的存量重复
  = known limitation（不红，报告列出）；**水位之上**的新增重复 = 红。
  分配器分配的编号即新水位——分配后必须随分支提交推进。
- ``check_referenced_files``：文内相对链接（``docs/...``、``app/...``、
  ``tests/...``、``scripts/...``、``deploy/...`` 等 repo 路径）存在性。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
ADR_DIR = "docs/adr"
#: 悬空链接棘轮基线（存量 7 条不红；新增悬空 = 红 —— 与 findings 棘轮同哲学）
DANGLING_BASELINE_PATH = "docs/integration/adr-link-baseline.json"

_TITLE_RE = re.compile(r"^#\s*ADR[- ](\d{4})\b", re.MULTILINE)
_REF_RE = re.compile(r"\bADR[- ](\d{4})\b")
_REPO_LINK_RE = re.compile(
    r"`?((?:\.github/)?(?:app|docs|tests|scripts|deploy|frontend|migrations|"
    r"extensions|wayfinder|workflows)/[A-Za-z0-9_./\-]+\.(?:py|md|json|ya?ml|"
    r"ts|tsx|toml|sh))`?"
)

#: 关系标签（实际语料观察：前置/关联/取代/supersedes/related）
_RELATION_LABELS = ("前置", "后继", "关联", "取代", "supersedes", "related",
                    "superseded-by")


@dataclass(frozen=True)
class AdrInfo:
    file: str            # repo 相对
    number: int          # 文件名编号
    title_number: Optional[int]  # 文内标题编号（None=解析不到）
    title: str
    references: Tuple[int, ...]   # 文内引用的 ADR 编号（去重升序）
    repo_links: Tuple[str, ...]   # 文内 repo 相对路径引用（去重升序）

    def as_dict(self) -> Dict[str, object]:
        return {
            "file": self.file, "number": self.number,
            "title_number": self.title_number, "title": self.title,
            "references": list(self.references),
            "repo_links": list(self.repo_links),
        }


@dataclass(frozen=True)
class AdrScan:
    adrs: Tuple[AdrInfo, ...]

    def duplicates(self) -> Dict[int, List[str]]:
        """编号 → 文件清单（>1 即重复）。"""
        out: Dict[int, List[str]] = {}
        for info in self.adrs:
            out.setdefault(info.number, []).append(info.file)
        return {n: files for n, files in sorted(out.items()) if len(files) > 1}

    def as_dict(self) -> Dict[str, object]:
        return {"adrs": [a.as_dict() for a in self.adrs],
                "duplicates": {str(k): v for k, v in self.duplicates().items()}}


def scan(repo_root: Optional[Path] = None) -> AdrScan:
    root = repo_root or REPO_ROOT
    infos: List[AdrInfo] = []
    for path in sorted((root / ADR_DIR).glob("*.md")):
        name_m = re.match(r"^(\d{4})", path.name)
        if not name_m:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        title_m = _TITLE_RE.search(text)
        first_line = text.splitlines()[0] if text.splitlines() else ""
        refs = tuple(sorted({int(m) for m in _REF_RE.findall(text)
                             if m != name_m.group(1)}))
        links = tuple(sorted(set(_REPO_LINK_RE.findall(text))))
        infos.append(AdrInfo(
            file=f"{ADR_DIR}/{path.name}",
            number=int(name_m.group(1)),
            title_number=int(title_m.group(1)) if title_m else None,
            title=first_line.lstrip("# ").strip(),
            references=refs,
            repo_links=links,
        ))
    return AdrScan(adrs=tuple(infos))


def next_number(repo_root: Optional[Path] = None) -> Tuple[int, List[str]]:
    """下一个可分配编号 = max+1；同时返回**当前将撞号**的警告清单（若另一
    分支已在 master 合并了同号 ADR，调用方应在 rebase 后重查）。

    并发窗口不可消除（10 worktree 无共享锁），本函数的职责是把撞号从
    "静默发生、合并后爆" 变为 "分配时可见 + 合并前 preflight/merge-sim 红"。
    """
    current = scan(repo_root)
    if not current.adrs:
        return 1, ["docs/adr/ 为空"]
    return max(a.number for a in current.adrs) + 1, []


def check_watermark(repo_root: Optional[Path],
                    watermark: int) -> List[str]:
    """水位之上的重复撞号 → 错误清单；水位之下 → 不红（存量 known limitation）。"""
    root = repo_root or REPO_ROOT
    errors: List[str] = []
    for number, files in scan(root).duplicates().items():
        if number > watermark:
            errors.append(
                f"ADR {number:04d} 撞号（watermark={watermark}）: {files}；"
                "重新分配编号并更新 ownership.adr_watermark")
    return errors


def dangling_links(repo_root: Optional[Path] = None) -> List[str]:
    """ADR 文内 repo 相对链接中悬空的全部条目（``file: 引用不存在 path``）。"""
    root = repo_root or REPO_ROOT
    errors: List[str] = []
    for info in scan(root).adrs:
        for link in info.repo_links:
            if not (root / link).exists():
                errors.append(f"{info.file}: 引用不存在 {link}")
    return sorted(errors)


def load_dangling_baseline(repo_root: Optional[Path] = None) -> List[str]:
    path = (repo_root or REPO_ROOT) / DANGLING_BASELINE_PATH
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8"))["dangling"])


def check_referenced_files(repo_root: Optional[Path] = None) -> List[str]:
    """悬空链接棘轮：基线之外的**新增**悬空 → 错误清单。

    master 存量悬空（历史 ADR 引用已删除/改名文件）不红——重建历史不属
    本 Epic；棘轮保证不再新增。基线刷新：preflight 脚本 --update-baselines。
    """
    baseline = set(load_dangling_baseline(repo_root))
    return [e for e in dangling_links(repo_root) if e not in baseline]


def known_limitations(repo_root: Optional[Path],
                      watermark: int) -> List[Dict[str, object]]:
    """水位之下的存量重复（诚实披露用，不红）。"""
    root = repo_root or REPO_ROOT
    return [{"number": n, "files": files}
            for n, files in scan(root).duplicates().items() if n <= watermark]
