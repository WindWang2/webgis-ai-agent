# app/skills —— 技能数据目录（非 Python 包）

## 性质（V9 P7 结构债盘点结论：保留原样）

本目录是**文件系统数据目录**，不是 Python 包（无 `__init__.py`，也不应有）。
内容是 Agent 技能的声明文档（.md，frontmatter 携带元数据），经文件系统路径
访问而非 import：

- `app/api/routes/config.py` —— skills_dir 只读目录披露；
- `app/tools/skills.py` —— .md frontmatter 解析进技能注册表；
- `app/services/skill_creator.py` —— Agent 写入新技能（.py 技能名必须为
  合法标识符，防路径穿越）。
