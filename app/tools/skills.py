import os
import re
import ast
import importlib.util
import sys
import logging
import yaml
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_BLOCKED_IMPORTS = {
    "os", "subprocess", "multiprocessing", "ctypes", "socket", "http",
    "urllib", "ftplib", "smtplib", "telnetlib", "xmlrpc", "shutil",
    "pathlib", "signal", "importlib", "builtins", "sys", "types",
    "code", "io", "pickle", "marshal", "shelve", "tempfile",
    "webbrowser", "antigravity", "asyncio",
    # Issue #399 (P3 defense-in-depth): module aliases that expose the same
    # OS-level capabilities as the entries above. `platform.os` is a verified
    # file-read escape; `_posixsubprocess.fork_exec` a verified process-exec
    # escape; `posix`/`nt` are the raw C modules `os` wraps; `runpy` executes
    # code from files/modules (same family as importlib); `zipimport` loads
    # and executes modules from a zip; `pty.fork` spawns child processes.
    "platform", "_posixsubprocess", "posix", "nt", "runpy", "zipimport",
    "pty",
}
_BLOCKED_BUILTINS = {
    "eval", "exec", "compile", "__import__", "open", "input",
    "getattr", "setattr", "delattr", "globals", "locals", "vars",
    "dir", "breakpoint", "memoryview", "type",
}
_BLOCKED_ATTRS = {
    "system", "popen", "call", "run", "Popen", "exec_module",
    "execl", "execle", "execlp", "execv", "execve", "execvp",
    "spawn", "fork", "startfile",
    # Issue #399: complete the exec*/fork*/spawn* family and dynamic-import
    # primitives (importlib.import_module / reload, runpy.run_path ...).
    "execlpe", "execvpe", "forkpty", "fork_exec",
    "spawnv", "spawnve", "spawnlp", "spawnlpe", "spawnvp", "spawnvpe",
    "posix_spawn", "posix_spawnp",
    "getoutput", "getstatusoutput", "check_output", "check_call",
    "import_module", "reload", "run_module", "run_path", "load_module",
    # Dunder attributes that enable MRO chain / sandbox escape
    "__subclasses__", "__globals__", "__init__", "__bases__",
    "__mro__", "__class__", "__import__", "__builtins__",
    "__loader__", "__spec__", "__getattribute__",
}
# Issue #399: attribute-access chains (e.g. `platform.os.open`) bypass the
# bare-name attribute check above. Any segment of a resolved chain that hits
# this set is rejected. The set covers:
#  - module names that alias OS/process/import capabilities (mirror of
#    _BLOCKED_IMPORTS, minus "io"/"types" which legitimately appear as
#    scientific-library submodules, e.g. scipy.io, pandas.api.types);
#  - process-exec / OS-level file-I/O attribute names (system, popen,
#    exec*/fork*/spawn* family, open, ...). Note: attribute `open` is blocked
#    aggressively on purpose (codecs.open / tokenize.open are file-read
#    escapes) — skills that need library-level file open (rasterio.open,
#    xarray.open_dataset) must be individually reviewed by an admin.
_BLOCKED_CHAIN_SEGMENTS = (
    {
        "os", "platform", "posix", "nt", "sys", "ctypes", "subprocess",
        "multiprocessing", "importlib", "builtins", "shutil", "pathlib",
        "tempfile", "pickle", "marshal", "shelve", "code", "signal",
        "socket", "http", "urllib", "webbrowser", "xmlrpc", "ftplib",
        "smtplib", "telnetlib", "runpy", "zipimport", "pty",
        "_posixsubprocess", "asyncio", "antigravity",
    }
    | {
        "system", "popen", "Popen", "call", "run", "startfile", "open",
        "exec_module", "exec", "execv", "execve", "execvp", "execvpe",
        "execl", "execle", "execlp", "execlpe", "fork", "forkpty",
        "fork_exec", "spawn", "spawnv", "spawnve", "spawnlp", "spawnlpe",
        "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp",
        "getoutput", "getstatusoutput", "check_output", "check_call",
        "load_module", "run_module", "run_path", "import_module", "reload",
    }
)

# In-memory store for .md skill files: {name: {description, body, filename}}
_md_skills: dict[str, dict] = {}


def _parse_md_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown string.

    Returns (metadata_dict, body_text). If no valid frontmatter is found,
    returns ({}, text) gracefully.
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
    if not match:
        return {}, text
    yaml_str, body = match.group(1), match.group(2)
    try:
        meta = yaml.safe_load(yaml_str)
        if not isinstance(meta, dict):
            return {}, text
    except yaml.YAMLError:
        return {}, text
    return meta, body


def list_md_skills() -> list[dict]:
    """Return a list of all loaded .md skills as [{name, description}, ...]."""
    return [{"name": k, "description": v["description"]} for k, v in _md_skills.items()]


def get_md_skill(name: str) -> dict | None:
    """Return the full skill data dict for the given name, or None."""
    return _md_skills.get(name)


def _load_md_skill(file_path: str, filename: str):
    """Read a .md file, parse frontmatter, and store in _md_skills."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        meta, body = _parse_md_frontmatter(text)
        name = meta.get("name")
        if not name:
            logger.warning(f"Skipping {filename}: no 'name' in frontmatter")
            return
        description = meta.get("description", "")
        _md_skills[name] = {
            "description": description,
            "body": body.strip(),
            "filename": filename,
        }
        logger.info(f"Loaded .md skill '{name}' from {filename}")
    except OSError as e:
        logger.error(f"Failed to load .md skill {filename}: {e}")


def _attr_chain(node: ast.Attribute) -> str:
    """Resolve an Attribute chain to its dotted form, e.g. `platform.os.open`.

    Non-attribute bases (calls, subscripts, ...) are rendered as a literal
    placeholder so the remaining segments still get checked.
    """
    parts = [node.attr]
    cur = node.value
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    elif isinstance(cur, ast.Call):
        parts.append("<call>")
    else:
        parts.append("<expr>")
    return ".".join(reversed(parts))


def _validate_skill_code(code: str) -> list[str]:
    """Validate skill code for dangerous patterns. Returns list of errors.

    Defense-in-depth (issues #399, #916): beyond bare-name checks,
    attribute-access chains are resolved to their full dotted name
    (`platform.os.open`, `_posixsubprocess.fork_exec`) and rejected if any
    segment hits the dangerous set. Computed/dynamic imports via
    ``getattr(builtins, "__import__")`` / ``__builtins__.__import__`` are
    also detected via a dedicated dunder-import probe. This is still a
    deny-list — NOT a security boundary; admin-only + ALLOW_DYNAMIC_SKILLS
    + tier-3 gate are the real boundary.
    """
    errors = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_mod = alias.name.split(".")[0]
                if root_mod in _BLOCKED_IMPORTS:
                    errors.append(f"Blocked import: {alias.name}")

        if isinstance(node, ast.ImportFrom):
            if node.module:
                root_mod = node.module.split(".")[0]
                if root_mod in _BLOCKED_IMPORTS:
                    errors.append(f"Blocked import: {node.module}")

        # Block dunder attribute/name access on any node (MRO chain / sandbox escape)
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            errors.append(f"Blocked dunder attribute: {node.attr}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            errors.append(f"Blocked dunder name: {node.id}")

        # Issue #399: resolve attribute chains (platform.os.open) and reject
        # any chain whose segment hits the dangerous set. Catches aliased
        # os/module access and exec/fork/spawn/open attribute chains that the
        # bare-name checks below cannot see.
        if isinstance(node, ast.Attribute):
            chain = _attr_chain(node)
            for seg in chain.split("."):
                if seg in _BLOCKED_CHAIN_SEGMENTS:
                    errors.append(
                        f"Blocked attribute chain: {chain} (segment: {seg})"
                    )
                    break

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BLOCKED_BUILTINS:
                errors.append(f"Blocked builtin: {func.id}")
            if isinstance(func, ast.Attribute) and func.attr in _BLOCKED_ATTRS:
                errors.append(f"Blocked attribute: {func.attr}")
            # #916: getattr(builtins, "__import__") / getattr(__builtins__, "eval")
            # is a verified bypass — the string literal carries a dunder that
            # the attribute checks above cannot see (the dunder is a Constant,
            # not an Attribute node). Probe Call(getattr) string args for dunders.
            if isinstance(func, ast.Name) and func.id == "getattr":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("__"):
                        errors.append(f"Blocked getattr dunder arg: {arg.value!r}")

        # #916: string-literal dunder smuggling outside Call(getattr) —
        # e.g. ``x = "__import__"; getattr(b, x)`` or ``__import__("os")`` via
        # a computed string variable. A bare "__import__" literal anywhere in
        # skill code is a strong bypass signal; reject it with a correction hint.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in ("__import__", "__builtins__", "__loader__", "__spec__"):
                # Allow the word appearing in a comment string that is also
                # used in legitimate GIS tool docstrings only if the skill file
                # is trivially small — here skill code is attacker-controlled
                # LLM output, so be strict.
                errors.append(f"Blocked dunder string literal: {node.value!r}")

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            deduped.append(e)
    return deduped


def register_skill_tools(registry: ToolRegistry):
    """注册用于管理和创建技能的元工具"""
    
    registry.register(
        name="create_new_skill",
        description="【核心进化】为 Agent 开发并部署一个新的技能脚本。你可以根据需要编写 Python 代码来实现复杂的地理分析逻辑。代码将自动部署并立即生效。",
        func=create_new_skill,
        # 破坏性工具，仅在用户明确请求时由 catalog 注入
        tier=3,
        domains=["meta"],
        param_descriptions={
            "module_name": "技能模块名称 (如 hydrology_analysis, change_detection)",
            "code": "完整的 Python 代码块。必须包含 register_skills(registry) 函数来注册在该模块内定义的工具。",
            "description": "对该技能功能的简要描述"
        }
    )

async def create_new_skill(module_name: str, code: str, description: str) -> str:
    """Agent 调用的创建技能函数。

    审计 SEC-02：AST 沙箱（_validate_skill_code）是 deny-list，**不能**
    防止有经验的攻击者逃逸（Python 对象图可通过无数非 dunder 路径到达
    os/subprocess）。此工具默认禁用，需显式设置 ALLOW_DYNAMIC_SKILLS=true
    才可用 —— 此时运维明确承担风险。

    正确的沙箱方案（separate subprocess + seccomp / wasm）是独立大工作。
    """
    if os.getenv("ALLOW_DYNAMIC_SKILLS", "").lower() != "true":
        return (
            "动态技能创建已禁用（ALLOW_DYNAMIC_SKILLS 未设置）。\n"
            "此功能在主进程中执行 importlib.exec_module，等同 RCE。\n"
            "如需启用，设置 ALLOW_DYNAMIC_SKILLS=true 并确保 only trusted users "
            "有 admin 权限。"
        )

    import hashlib as _hashlib

    errors = _validate_skill_code(code)
    if errors:
        return "Skill validation failed:\n" + "\n".join(f"- {e}" for e in errors) + "\nPlease revise your code to remove dangerous patterns."

    # #916 audit log: sha256 + truncated description + module name for forensics
    try:
        sha = _hashlib.sha256(code.encode()).hexdigest()[:16]
        logger.warning("[skill-audit] create_new_skill module=%s sha256=%s size=%d desc=%r", module_name, sha, len(code), description[:200])
    except Exception:
        pass

    from app.services.skill_creator import skill_creator
    # E-2（#893）：经 services 层持有器取 registry（此前反向 import 路由层）
    from app.services.chat.engine_instance import try_get_app_registry

    app_registry = try_get_app_registry()
    if app_registry is None:
        return "Skill created but registry not initialized yet; skill will load on next dispatch."
    result = skill_creator.create_skill(module_name, code, description)
    # 立即触发热加载
    load_skills(app_registry)
    return result

def _load_single_skill(registry: ToolRegistry, file_path: str, filename: str):
    """Load or reload a single skill file into the registry."""
    module_name = f"app.skills.{filename[:-3]}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            if hasattr(module, "register_skills"):
                module.register_skills(registry)
                logger.info(f"Loaded skill from {filename} via register_skills")
            elif hasattr(module, "register"):
                module.register(registry)
                logger.info(f"Loaded skill from {filename} via register")
            else:
                logger.warning(f"Skill {filename} has no 'register' or 'register_skills' function.")
    except (ImportError, SyntaxError, AttributeError) as e:
        logger.error(f"Failed to load skill {filename}: {e}")


def load_skills(registry: ToolRegistry, skills_dir: str = "app/skills"):
    """Load all skill scripts from the skills directory.

    #1062: .md 技能按目录重建 —— 已删除的 .md 文件此前永远残留在
    ``_md_skills``（无任何驱逐路径），list_md_skills 会持续广告幽灵技能。
    """
    if not os.path.exists(skills_dir):
        os.makedirs(skills_dir, exist_ok=True)
        return

    _md_skills.clear()
    for filename in os.listdir(skills_dir):
        if filename.endswith(".py") and not filename.startswith("__"):
            _load_single_skill(registry, os.path.join(skills_dir, filename), filename)
        elif filename.endswith(".md"):
            _load_md_skill(os.path.join(skills_dir, filename), filename)

def watch_skills(registry: ToolRegistry, skills_dir: str = "app/skills"):
    """Poll-based file watcher for hot-reloading skills.

    Tracks file modification times. Returns a check function that can be
    called periodically (e.g., every 5s) to detect new or changed skill files.
    Only reloads files that actually changed.

    #1062 NOTE: zero callers in app/ and tests/ — production reload goes
    through ``create_new_skill`` → ``load_skills``. Retained as a public
    helper for operational scripts; do not extend without wiring a caller.
    """
    _mtimes: dict[str, float] = {}

    def _check():
        if not os.path.exists(skills_dir):
            return
        for filename in os.listdir(skills_dir):
            if filename.startswith("__"):
                continue
            if not (filename.endswith(".py") or filename.endswith(".md")):
                continue
            filepath = os.path.join(skills_dir, filename)
            try:
                mtime = os.path.getmtime(filepath)
            except OSError:
                continue
            if filepath not in _mtimes or _mtimes[filepath] < mtime:
                _mtimes[filepath] = mtime
                if filename.endswith(".py"):
                    _load_single_skill(registry, filepath, filename)
                else:
                    _load_md_skill(filepath, filename)

    _check()
    return _check

async def fetch_remote_skills(registry: ToolRegistry, repo_url: str):
    """
    从远端仓库拉取技能清单并加载。
    目前为 Mock 实现，实际可对接 GitHub Gist 或专用 Skills Hub。
    """
    logger.info(f"Fetching remote skills from {repo_url}...")
    # 模拟远程获取并写入本地 app/skills/remote_xxx.py
    # ...
    from app.services.chat.engine_instance import try_get_app_registry

    app_registry = try_get_app_registry()
    if app_registry is not None:
        load_skills(app_registry)
    return {"status": "success", "count": 0}
