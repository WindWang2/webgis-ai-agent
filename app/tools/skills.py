import os
import importlib.util
import sys
import logging
from typing import Optional
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

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
