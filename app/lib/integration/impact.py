"""影响分析 + 测试选择（Quality V3 W7，架构 §F / Subagent-A M-1 修订）。

AST import 图（app ↔ tests）驱动的最小可靠本地面：

- **增量缓存**：每文件内容哈希，只重解析变化的文件（资源纪律：
  全仓 AST 不因指纹未变而重跑）；
- **完备性护栏（M-1 修订）**：任何 changed app 文件必须产出 ≥1 个测试
  目标——import 闭包找不到时降级到目录映射，映射保证兜底到 ``tests``
  根；两条路都产不出 = 显式 ``uncovered`` 清单（CLI 退出非零，
  ``--allow-uncovered`` 需显式豁免）。dynamic import/importlib 是 AST
  盲区，正是该兜底存在的原因；
- **定位**：高效集成复测，**不替代** domain full tests（V2 changed
  profile 与各车道照常）。

边界：只追 ``app.*`` 包内边；相对 import 按所在包解析；import 边
O(文件数)，闭包有界（app 模块总数）。
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]

CACHE_DIR = Path(".agent-work/integration/cache")
CACHE_VERSION = 1
#: 全仓扫描文件硬上限（资源纪律）
_MAX_FILES = 20000
#: 选择结果目标上限（超出 = 显式警告，不静默截断）
DEFAULT_MAX_TARGETS = 60


@dataclass
class ImportGraph:
    #: path → 直接 import 的 app 顶层模块列表（"app.lib.x.y" 形态）
    app: Dict[str, List[str]] = field(default_factory=dict)
    tests: Dict[str, List[str]] = field(default_factory=dict)
    hashes: Dict[str, str] = field(default_factory=dict)

    def reverse_app_closure(self, changed_modules: Iterable[str]) -> Set[str]:
        """changed app 模块的反向传递闭包（含自身），限 app 内边。

        反向边源端从文件路径归一化为模块名——闭包全程是模块名空间。
        """
        reverse: Dict[str, Set[str]] = {}
        for src, imports in self.app.items():
            src_module = module_name_for(src)
            if src_module is None:
                continue
            for target in imports:
                reverse.setdefault(target, set()).add(src_module)
        closure: Set[str] = set()
        stack = list(set(changed_modules))
        while stack:
            mod = stack.pop()
            if mod in closure:
                continue
            closure.add(mod)
            stack.extend(reverse.get(mod, ()))
        return closure


def module_name_for(path: str) -> Optional[str]:
    """repo 相对 py 路径 → 模块名（app/ 或 tests/ 内）；其余 None。"""
    for prefix in ("app/", "tests/"):
        if path.startswith(prefix):
            parts = path[:-3].split("/") if path.endswith(".py") else None
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            return ".".join(parts) if parts else None
    return None


def _file_imports(path: Path, repo_root: Path) -> List[str]:
    """单文件 import 的 app 模块（相对 import 按所在包解析）。

    ``from pkg import sub`` 同时记录 ``pkg`` 与 ``pkg.sub``——闭包语义
    上消费者依赖的是被引出的子模块（漏记会导致反向闭包漏边）。
    ast.parse 全程抑制 SyntaxWarning（被扫描文件的历史转义序列不是
    本工具的关注点）。
    """
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="ignore"))
        except (OSError, SyntaxError, SyntaxWarning):
            return []
    rel = path.relative_to(repo_root).as_posix()
    own_module = module_name_for(rel) or ""
    own_parts = own_module.split(".") if own_module else []
    found: Set[str] = set()

    def _add(module: str) -> None:
        if module and module.split(".")[0] == "app":
            found.add(module)

    def _add_from(module: str, names: Iterable[str]) -> None:
        if not (module and module.split(".")[0] == "app"):
            return
        found.add(module)
        for name in names:
            _add(f"{module}.{name}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    _add_from(node.module,
                              (alias.name for alias in node.names))
            else:
                # 相对 import：基包 = 自身所在包上溯 level-1 层
                base = own_parts[:-(node.level)] if node.level <= len(own_parts) else []
                module = ".".join(base + ([node.module] if node.module else []))
                _add_from(module, (alias.name for alias in node.names))
    return sorted(found)


def _walk_py(root: Path, subdirs: Tuple[str, ...]) -> List[Path]:
    out: List[Path] = []
    for sub in subdirs:
        base = root / sub
        if base.is_dir():
            out.extend(sorted(base.rglob("*.py")))
    if len(out) > _MAX_FILES:
        # R2-m2：与 artifact_graph._expand 同纪律 —— 静默截断 = 闭包盲区
        raise ValueError(
            f"import 图扫描命中 {len(out)} 个 .py，超过有界上限 "
            f"{_MAX_FILES}；请显式上调上限（需评审资源影响）")
    return out


def build_graph(repo_root: Optional[Path] = None,
                use_cache: bool = True) -> ImportGraph:
    """增量 import 图：哈希未变的文件沿用缓存解析结果。"""
    import hashlib

    root = repo_root or REPO_ROOT
    paths = _walk_py(root, ("app", "tests"))
    graph = ImportGraph()
    cache: Dict[str, object] = {}
    cache_path = root / CACHE_DIR / "import-graph.json"
    if use_cache and cache_path.exists():
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            if raw.get("version") == CACHE_VERSION:
                cache = raw.get("files", {})
        except (OSError, json.JSONDecodeError):
            cache = {}

    for path in paths:
        rel = path.relative_to(root).as_posix()
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        graph.hashes[rel] = digest
        cached = cache.get(rel) if isinstance(cache, dict) else None
        if isinstance(cached, dict) and cached.get("hash") == digest:
            graph.app[rel] = list(cached.get("app_imports", []))
            graph.tests[rel] = list(cached.get("test_imports", []))
            continue
        imports = _file_imports(path, root)
        if rel.startswith("app/"):
            graph.app[rel] = imports
        else:
            graph.tests[rel] = imports

    if use_cache:
        _write_cache(root, graph)
    return graph


def _write_cache(root: Path, graph: ImportGraph) -> None:
    cache_path = root / CACHE_DIR / "import-graph.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CACHE_VERSION,
        "files": {
            rel: {"hash": graph.hashes[rel],
                  "app_imports": graph.app.get(rel, []),
                  "test_imports": graph.tests.get(rel, [])}
            for rel in sorted(graph.hashes)
            if rel in graph.app or rel in graph.tests
        },
    }
    # R2-m1：tmp + os.replace 原子换入（并发 runner/本地同时跑 impact
    # 不互相撕裂；读侧 JSONDecodeError 兜底保留为最后防线）
    import os
    import tempfile

    fd, tmp_path = tempfile.mkstemp(dir=str(cache_path.parent),
                                    suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload))
        os.replace(tmp_path, cache_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _app_modules_for_file(rel: str) -> str:
    """app 文件 → 其自身模块名（闭包种子）。"""
    return module_name_for(rel) or rel


#: V2 changed profile 的领域映射（quality_runner._changed_py_targets）：
#: 显式并入（M-1 防漏报护栏要求 selector ⊇ V2 映射，缺一条就是红线盲区）
_V2_DOMAIN_MAP: Tuple[Tuple[str, str], ...] = (
    ("app/lib/gis", "tests/unit/gis"),
    ("app/lib/quality", "tests/quality"),
    ("app/tools", "tests/unit/tools"),
    ("app/services", "tests/unit"),
    ("app/api", "tests"),
    ("app/core", "tests"),
)


def map_directory_target(rel: str, repo_root: Path) -> Optional[str]:
    """完备目录映射（M-1：V2 六条映射的缺口补全）。

    优先级：tests 自身 > quality/docs 红线 > V2 领域映射 > 包路径逐级
    上溯找存在的 tests 对应目录 > tests 根（恒存在）。
    """
    parts = rel.split("/")
    if rel.startswith("tests/"):
        return rel
    if rel.startswith("scripts/"):
        return "tests/quality"
    if rel.startswith("docs/quality/"):
        return "tests/quality"
    if rel.startswith("frontend/"):
        return None  # 前端目标由 frontend lane 处理
    if rel.startswith("app/"):
        for prefix, target in _V2_DOMAIN_MAP:
            if rel.startswith(prefix + "/") and (repo_root / target).is_dir():
                return target
        candidate_dirs = []
        for depth in range(len(parts) - 2, 0, -1):
            sub = "/".join(parts[1:depth + 1])
            if not sub:
                continue
            candidate_dirs.append(f"tests/unit/{sub}")
            candidate_dirs.append(f"tests/{sub}")
        candidate_dirs += ["tests/unit", "tests"]
        for cand in candidate_dirs:
            if (repo_root / cand).is_dir():
                return cand
        return "tests"
    return "tests"


@dataclass
class SelectionResult:
    targets: List[str] = field(default_factory=list)
    via_import: List[str] = field(default_factory=list)
    via_mapping: List[str] = field(default_factory=list)
    uncovered: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def complete(self) -> bool:
        return not self.uncovered


def select_tests(
    changed_files: List[str],
    repo_root: Optional[Path] = None,
    graph: Optional[ImportGraph] = None,
    max_targets: int = DEFAULT_MAX_TARGETS,
) -> SelectionResult:
    """最小可靠本地测试面（完备性护栏，见模块 docstring）。

    完备性归约（逐文件）：
    - changed app 文件：import 闭包命中任一测试 **或** 目录映射兜底目录
      存在 → covered；两者皆无 → uncovered（诚实失败）；
    - changed tests 文件：自身即目标；
    - 其他文件：按目录映射；frontend 归 frontend lane（映射 None 不算
      uncovered）；映射目录不存在 → uncovered。
    """
    root = repo_root or REPO_ROOT
    graph = graph or build_graph(root)
    result = SelectionResult()

    app_changed = [f for f in changed_files
                   if f.startswith("app/") and f.endswith(".py")]
    seeds = {_app_modules_for_file(f) for f in app_changed}
    closure = graph.reverse_app_closure(seeds)

    imported_targets = {
        test_rel for test_rel, imports in graph.tests.items()
        if closure & set(imports)
    }

    mapped_targets: Set[str] = set()
    for rel in changed_files:
        target = map_directory_target(rel, root)
        if target is None:
            continue
        if (root / target).is_dir():
            mapped_targets.add(target)
        else:
            result.uncovered.append(rel)

    def _import_covered(rel: str) -> bool:
        seed_closure = graph.reverse_app_closure(
            {_app_modules_for_file(rel)})
        return any(seed_closure & set(imports)
                   for imports in graph.tests.values())

    for rel in app_changed:
        mapped = map_directory_target(rel, root)
        mapped_ok = bool(mapped) and (root / mapped).is_dir()
        if not _import_covered(rel) and not mapped_ok:
            result.uncovered.append(rel)

    result.via_import = sorted(imported_targets)
    result.via_mapping = sorted(mapped_targets - imported_targets)
    merged = sorted(imported_targets | mapped_targets)
    if len(merged) > max_targets:
        result.warnings.append(
            f"目标数 {len(merged)} 超过上限 {max_targets}，显式截断"
            "（不静默；提高 max_targets 或缩小变更集）")
        merged = merged[:max_targets]
        result.truncated = True
    result.targets = merged
    result.uncovered = sorted(set(result.uncovered))
    return result
