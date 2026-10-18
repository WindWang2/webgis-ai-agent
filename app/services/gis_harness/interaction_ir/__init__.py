"""Interaction IR V1 —— 用户地图交互事件总线（Goal 02 / ADR-0225）。

统一 ``typed event + client sequence + causality + authority revision +
dedup + replay + bounded projection`` 的观察层协议。**不是第二份
MapSpec/Requirement/世界状态真相** —— 用户 layer 可见性/排序/样式 mutation
仍走 MapSpec lifecycle，IR 事件只记录发生了什么与 receipt 链接。

子模块：

- ``contracts`` —— kind 词表/白名单/有界常量（单一权威，前端 manifest 双闸）；
- ``ingest`` —— 幂等摄入状态机（纯函数 fold，生产/回放同路径）；
- ``projection`` —— 有界 agent 上下文投影（序号驱动，确定性）;
- ``replay`` —— 确定性场景回放 harness。
"""
from app.services.gis_harness.interaction_ir.contracts import (  # noqa: F401
    ALLOWED_KINDS,
    INTERACTION_KINDS,
    SCHEMA_VERSION,
)
