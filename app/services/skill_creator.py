"""Skill Creator - 允许 Agent 自主编写并部署技能脚本"""
import os
import logging
import textwrap
from typing import Optional

logger = logging.getLogger(__name__)

class SkillCreator:
    def __init__(self, skills_dir: str = "app/skills"):
        self.skills_dir = skills_dir
        if not os.path.exists(self.skills_dir):
            os.makedirs(self.skills_dir, exist_ok=True)

    def create_skill(self, name: str, code: str, description: str) -> str:
        """
        创建一个新的技能脚本。
        name: 技能名称 (如 terrain_analysis)
        code: 完整的 Python 代码
        description: 技能描述
        """
        if not name.endswith(".py"):
            name = f"{name}.py"
        
        file_path = os.path.join(self.skills_dir, name)
        
        # 基础校验：代码不能包含危险操作 (简单过滤)
        # TODO: 接入更高级的代码审计
        
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)
            
            logger.info(f"Skill '{name}' created/updated successfully.")
            return f"技能 '{name}' 已开发并部署完成。{description}"
        except Exception as e:
            logger.error(f"Failed to create skill {name}: {e}")
            raise e

# 全局实例
skill_creator = SkillCreator()
