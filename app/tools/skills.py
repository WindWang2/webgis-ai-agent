import os
import importlib.util
import sys
import logging
from typing import Optional
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

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
    from app.services.skill_creator import skill_creator
    from app.api.routes.chat import registry
    
    result = skill_creator.create_skill(module_name, code, description)
    # 立即触发热加载
    load_skills(registry)
    return result

def load_skills(registry: ToolRegistry, skills_dir: str = "app/skills"):
    """
    Dynamically load Python scripts from the skills directory and register them as tools.
    Each script should have a 'register' function or use the @tool decorator with a global registry.
    """
    if not os.path.exists(skills_dir):
        os.makedirs(skills_dir, exist_ok=True)
        logger.info(f"Created skills directory: {skills_dir}")
        return

    for filename in os.listdir(skills_dir):
        if filename.endswith(".py") and not filename.startswith("__"):
            file_path = os.path.join(skills_dir, filename)
            module_name = f"app.skills.{filename[:-3]}"
            
            try:
                spec = importlib.util.spec_from_file_location(module_name, file_path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                    
                    # Look for a register function that takes the registry
                    if hasattr(module, "register_skills"):
                        module.register_skills(registry)
                        logger.info(f"Loaded skills from {filename} via register_skills")
                    elif hasattr(module, "register"):
                        module.register(registry)
                        logger.info(f"Loaded skills from {filename} via register")
                    else:
                        # Fallback: find all functions decorated with @tool if they were registered to a local registry
                        # This depends on how the skill script is written. 
                        # Usually, we expect a register(registry) function.
                        logger.warning(f"Skill {filename} has no 'register' or 'register_skills' function.")
            except Exception as e:
                logger.error(f"Failed to load skill {filename}: {e}")

def watch_skills(registry: ToolRegistry, skills_dir: str = "app/skills"):
    """
    TODO: Implement a file watcher (e.g., using watchdog) to hot-reload skills.
    For now, we just load them once at startup.
    """
    load_skills(registry, skills_dir)

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
