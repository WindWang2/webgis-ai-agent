"""Simulation 服务层（ADR-0193）。

当前子模块：
- ``whatif``：反事实假设推演分支管理器（Scenario Branching v1）。

与相邻层的分工：单次型 What-If 评估工具在 ``app/tools/what_if_simulate.py``
与 ``app/services/spatial_decision/``（保持不变）；本层只负责**世界状态级**
分支、差分与处方建议。
"""
