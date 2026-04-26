import os
import ast
import importlib.util
import sys
import logging
from typing import Optional
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_BLOCKED_IMPORTS = {"os", "subprocess", "multiprocessing", "ctypes", "socket", "http", "urllib", "ftplib", "smtplib", "telnetlib", "xmlrpc", "shutil", "pathlib", "signal"}
_BLOCKED_BUILTINS = {"eval", "exec", "compile", "__import__", "open", "input", "getattr", "setattr", "delattr", "globals", "locals", "vars", "dir"}
_BLOCKED_ATTRS = {"system", "popen", "call", "run", "Popen"}


def _validate_skill_code(code: str) -> list[str]:
    """Validate skill code for dangerous patterns. Returns list of errors."""
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

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BLOCKED_BUILTINS:
                errors.append(f"Blocked builtin: {func.id}")
            if isinstance(func, ast.Attribute) and func.attr in _BLOCKED_ATTRS:
                errors.append(f"Blocked attribute: {func.attr}")

    return errors


def register_skill_tools(registry: ToolRegistry):
    """注册用于管理和创建技能的元工具"""
    
    registry.register(
        name="create_new_skill",
        description="【核心进化】为 Agent 开发并部署一个新的技能脚本。你可以根据需要编写 Python 代码来实现复杂的地理分析逻辑。代码将自动部署并立即生效。",
        func=create_new_skill,
        param_descriptions={
            "module_name": "技能模块名称 (如 hydrology_analysis, change_detection)",
            "code": "完整的 Python 代码块。必须包含 register_skills(registry) 函数来注册在该模块内定义的工具。",
            "description": "对该技能功能的简要描述"
        }
    )

async def create_new_skill(module_name: str, code: str, description: str) -> str:
    """Agent 调用的创建技能函数"""
    errors = _validate_skill_code(code)
    if errors:
        return f"Skill validation failed:\n" + "\n".join(f"- {e}" for e in errors) + "\nPlease revise your code to remove dangerous patterns."

    from app.services.skill_creator import skill_creator
    from app.api.routes.chat import registry

    result = skill_creator.create_skill(module_name, code, description)
    # 立即触发热加载
    load_skills(registry)
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
    except Exception as e:
        logger.error(f"Failed to load skill {filename}: {e}")


def load_skills(registry: ToolRegistry, skills_dir: str = "app/skills"):
    """Load all skill scripts from the skills directory."""
    if not os.path.exists(skills_dir):
        os.makedirs(skills_dir, exist_ok=True)
        return

    for filename in os.listdir(skills_dir):
        if filename.endswith(".py") and not filename.startswith("__"):
            _load_single_skill(registry, os.path.join(skills_dir, filename), filename)

def watch_skills(registry: ToolRegistry, skills_dir: str = "app/skills"):
    """Poll-based file watcher for hot-reloading skills.

    Tracks file modification times. Returns a check function that can be
    called periodically (e.g., every 5s) to detect new or changed skill files.
    Only reloads files that actually changed.
    """
    _mtimes: dict[str, float] = {}

    def _check():
        if not os.path.exists(skills_dir):
            return
        for filename in os.listdir(skills_dir):
            if not filename.endswith(".py") or filename.startswith("__"):
                continue
            filepath = os.path.join(skills_dir, filename)
            try:
                mtime = os.path.getmtime(filepath)
            except OSError:
                continue
            if filepath not in _mtimes or _mtimes[filepath] < mtime:
                _mtimes[filepath] = mtime
                _load_single_skill(registry, filepath, filename)

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
    load_skills(registry)
    return {"status": "success", "count": 0}
