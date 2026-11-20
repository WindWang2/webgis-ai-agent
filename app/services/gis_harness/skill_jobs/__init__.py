"""G30 — 可执行 GIS 技能作业域模块。

技能从「可查阅的方法知识」推进为「可选版本、可绑定数据、可校验参数、
可审阅计划、可启动执行、可回看结果、可复用配置」的作业产品。

边界（与 ADR-0182/0220 一致，不越权）：

- prepare **零业务副作用**（只读校验 + 确定性编译）；不授权、不执行；
- start 经 ToolDispatchService 同一 owner/scope/side-effect 闸执行既有
  生产工具；SessionPlan 仍是步骤状态唯一真相（经统一派发自动喂
  ``apply_tool_result``）；不建第二 planner/DAG/工具注册表；
- run 记录落在既有 durable job 表（JobKind=skill_job，ADR-0052 加性），
  只新增「技能选择与绑定出处」快照，不复制步骤状态真相；
- Pi 回调内启动不开新 turn、不递归 prompt —— 绑定当前 turn/envelope。

子模块：

- ``contracts``：PreparedSkillCall 等域模型（纯数据 + 闭词表）；
- ``prepare``：prepare 服务（typed 拒绝码、数据事实抽取、能力核对、
  幂等键铸造）；
- ``store``：prepared call 进程内 LRU（TTL + 会话绑定，仿 G24 句柄）；
- ``runner``：start/执行/取消/状态（durable job + 逐步 dispatch）；
- ``catalog_views``：分类型目录投影（runnable/guidance_only/…）；
- ``presets``：参数预设（配置引用，不含数据/凭据）。
"""
from app.services.gis_harness.skill_jobs.contracts import (
    PreparedBinding,
    PreparedParam,
    PreparedSkillCall,
    PreparedStep,
    SkillJobError,
    SkillJobErrorCode,
)
from app.services.gis_harness.skill_jobs.prepare import prepare_skill_job

__all__ = [
    "PreparedBinding",
    "PreparedParam",
    "PreparedSkillCall",
    "PreparedStep",
    "SkillJobError",
    "SkillJobErrorCode",
    "prepare_skill_job",
]
