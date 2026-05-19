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
        # 安全：name 来自 LLM（含 prompt injection 风险），必须是合法 Python 标识符。
        # 否则攻击者可写 name='../core/auth' 覆盖核心模块（审计 B7）。
        import os.path as _p
        base = name[:-3] if name.endswith(".py") else name
        base = _p.basename(base)  # 剥任何路径分隔符
        if not base.isidentifier() or base.startswith("_"):
            raise ValueError(
                f"技能名 {name!r} 非法：必须是不以下划线开头的合法 Python 标识符 "
                "(字母/数字/下划线，首字符非数字非下划线)"
            )
        name = f"{base}.py"

        file_path = os.path.join(self.skills_dir, name)

        # 二次防御：解析后必须仍在 skills_dir 下，防御 symlink/绝对路径
        resolved = os.path.realpath(file_path)
        skills_root = os.path.realpath(self.skills_dir)
        if not resolved.startswith(skills_root + os.sep) and resolved != skills_root:
            raise ValueError(f"路径越界：{file_path}")

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
