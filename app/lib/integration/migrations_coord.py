"""Migration 协调（Quality V3 W3，架构 §B）。

多 worktree 并发下 migration 的三类真实事故：
1. 多 head（两个分支各自追加 revision，merge 后 alembic 拒绝升级）；
2. NNNN 序号撞号（allocator 各自 max+1，合并后语义混乱）；
3. 同表 DDL 冲突（两个分支改同一张表，合并后 upgrade 中途失败）。

本模块：
- 图扫描用 **alembic ScriptDirectory**（m-4：仓内 revision 写法多样——
  ``revision: str = "..."`` 注解式、hex 族、文件名≠revision id（0024 反例）、
  ``down_revision`` 可能是序列（merge revision）——手写解析必然漏）；
- 高水位：`ownership.json.migration_watermark`；序号 > 水位而水位未推进
  = 分配后忘记提交推进 → preflight 红（C-2 同款机制）；
- 跨分支碰撞检测（``branch_collisions``）：输入两个分支的 changed
  migration 文件清单（来自 git diff，由 manifest 层提供），输出冲突类别。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]

#: NNNN 序号提取（文件名前缀或 revision id 前缀均可；0024 证明二者通常一致
#: 但不保证相等，冲突检测对两种口径都报）
_SEQ_RE = re.compile(r"^(?:\d{4}|(\d{4})_)")
_DIGITS_RE = re.compile(r"^(\d{4})")

#: DDL 提取（尽力而为）：upgrade() 内常见 op.* 调用的表名
_TABLE_OPS_RE = re.compile(
    r"op\.(?:create_table|drop_table|rename_table)\s*\(\s*['\"]([^'\"]+)['\"]"
)
_ADD_COLUMN_RE = re.compile(
    r"op\.add_column\s*\(\s*['\"]([^'\"]+)['\"]"
)


@dataclass(frozen=True)
class MigrationInfo:
    revision: str
    down_revision: Optional[Tuple[str, ...]]  # None=root；元组=merge revision
    file: str                                 # repo 相对路径
    seq: Optional[int]                        # NNNN 序号（无前缀 = None）
    tables: FrozenSet[str]                    # upgrade() 涉及表（尽力而为）

    def as_dict(self) -> Dict[str, object]:
        return {
            "revision": self.revision,
            "down_revision": list(self.down_revision) if self.down_revision else None,
            "file": self.file,
            "seq": self.seq,
            "tables": sorted(self.tables),
        }


@dataclass(frozen=True)
class MigrationGraph:
    revisions: Tuple[MigrationInfo, ...]

    @property
    def heads(self) -> List[str]:
        """被 down_revision 引用集合之外的 revision（升序）。"""
        referenced: set[str] = set()
        for info in self.revisions:
            if info.down_revision:
                referenced.update(info.down_revision)
        return sorted(r.revision for r in self.revisions
                      if r.revision not in referenced)

    def revisions_over_watermark(self, watermark: int) -> List[str]:
        """NNNN 序号 > 水位的 revision（升序）——水位未推进的信号。"""
        return sorted(
            (r.revision for r in self.revisions
             if r.seq is not None and r.seq > watermark),
            key=lambda rev: next(x.seq for x in self.revisions if x.revision == rev),
        )

    def as_dict(self) -> Dict[str, object]:
        return {"heads": self.heads,
                "revisions": [r.as_dict() for r in self.revisions]}


def scan(repo_root: Optional[Path] = None) -> MigrationGraph:
    """扫描 migrations/versions → MigrationGraph（alembic ScriptDirectory）。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = repo_root or REPO_ROOT
    cfg = Config()
    cfg.set_main_option("script_location", str(root / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    infos: List[MigrationInfo] = []
    for rev in script.walk_revisions():
        down = rev.down_revision
        if down is None:
            down_tuple: Optional[Tuple[str, ...]] = None
        elif isinstance(down, (tuple, list)):
            down_tuple = tuple(str(d) for d in down)
        else:
            down_tuple = (str(down),)
        path = getattr(rev, "path", "") or ""
        rel = Path(path).resolve().relative_to(root.resolve()).as_posix() if path else ""
        infos.append(MigrationInfo(
            revision=rev.revision,
            down_revision=down_tuple,
            file=rel,
            seq=_extract_seq(rev.revision, Path(rel).name),
            tables=_extract_tables(path),
        ))
    return MigrationGraph(revisions=tuple(infos))


def branch_collisions(
    base_files: List[str], other_files: List[str],
    repo_root: Optional[Path] = None,
) -> Dict[str, List[Dict[str, str]]]:
    """两个分支的 changed-migration 文件集碰撞检测（merge sim 的 migration 轴）。

    输入是 repo 相对路径清单（双方各自的 diff）；返回
    ``{conflict_kind: [{base, other, detail}]}``。检测：
    - same_revision：双方改了同一 migration 文件；
    - sequence_collision：文件名 NNNN 序号相同（双分支 allocator 各自
      max+1 的典型产物）；
    - forked_down_revision：双方各有一个**新增** revision 挂在同一
      down_revision 上（合并后多 head）。分支新增文件不在 master 图中，
      其 (revision, down_revision) 从文件 AST 提取（m-4：注解式/
      元组式赋值都能正确处理；解析失败的文件降级 unknown，不谎报）。
    """
    root = (repo_root or REPO_ROOT)
    base_set, other_set = set(base_files), set(other_files)
    out: Dict[str, List[Dict[str, str]]] = {
        "same_revision": [], "sequence_collision": [], "forked_down_revision": [],
    }

    for f in sorted(base_set & other_set):
        out["same_revision"].append(
            {"base": f, "other": f, "detail": "双方都修改同一 migration 文件"})

    base_seqs = {_seq_of_file(f): f for f in base_set
                 if _seq_of_file(f) is not None}
    for f in sorted(other_set):
        seq = _seq_of_file(f)
        if seq is not None and seq in base_seqs and f not in base_set:
            out["sequence_collision"].append({
                "base": base_seqs[seq], "other": f,
                "detail": f"NNNN={seq:04d} 被双方占用（allocator 并发 max+1）",
            })

    base_downs: Dict[str, List[str]] = {}
    for f in sorted(base_set - other_set):
        parsed = _parse_revision_fields(root / f)
        if parsed and parsed[1]:
            base_downs.setdefault(parsed[1], []).append(f)
    for f in sorted(other_set - base_set):
        parsed = _parse_revision_fields(root / f)
        if parsed and parsed[1] and parsed[1] in base_downs:
            out["forked_down_revision"].append({
                "base": ",".join(base_downs[parsed[1]]), "other": f,
                "detail": f"双方新增 revision 挂同一 down {parsed[1]}",
            })
    return {k: v for k, v in out.items() if v}


_REVISION_KEYS = ("revision", "down_revision")


def _parse_revision_fields(path: Path) -> Optional[Tuple[str, Optional[str]]]:
    """AST 提取模块级 revision / down_revision 赋值。

    覆盖实测写法：``revision: str = "..."``（AnnAssign）、
    ``down_revision: Union[str, Sequence[str], None] = "..." | None | ("a","b")``。
    元组 down 取第一个元素（merge revision 挂多父，fork 判定按主父）。
    """
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):
        return None
    values: Dict[str, Optional[str]] = {}
    for node in tree.body:
        targets: List[ast.expr] = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        if value is None:
            continue
        names = [t.id for t in targets
                 if isinstance(t, ast.Name) and t.id in _REVISION_KEYS]
        if not names:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for n in names:
                values[n] = value.value
        elif isinstance(value, ast.Constant) and value.value is None:
            for n in names:
                values.setdefault(n, None)
        elif isinstance(value, ast.Tuple):
            elems = [e for e in value.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            for n in names:
                values[n] = elems[0].value if elems else None
    if "revision" not in values:
        return None
    return values["revision"], values.get("down_revision")


def _extract_seq(revision_id: str, filename: str) -> Optional[int]:
    for candidate in (filename, revision_id):
        m = _DIGITS_RE.match(candidate)
        if m:
            return int(m.group(1))
    return None


def _seq_of_file(filename: str) -> Optional[int]:
    m = _DIGITS_RE.match(Path(filename).name)
    return int(m.group(1)) if m else None


def _extract_tables(path: str) -> FrozenSet[str]:
    """upgrade() 内 op.create_table/add_column 的表名（尽力而为，解析不到
    就是空集——调用方不得把空集当"无表操作"）。"""
    try:
        src = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return frozenset()
    tables = set(_TABLE_OPS_RE.findall(src)) | set(_ADD_COLUMN_RE.findall(src))
    return frozenset(tables)
