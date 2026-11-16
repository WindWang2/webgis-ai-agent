"""G44 业务字段契约域 —— 声明、完整评估、激活与统一写入门禁。

分层纪律（与 data_join_authoring / topology_workbench 一致）：
- ``validator``：纯验证内核（预览 / 提交 / 评估 / undo-redo 四处共用，无 I/O）；
- ``store``：DB 持久层（版本化合同 + 评估收据，CAS 语义）；
- ``service``：owner 编排（草稿 / 评估 / 激活 / 退役 / 违规分页）；
- ``enforcement``：feature_edit 执行链接缝（解析 active 合同 + 候选态门禁）；
- ``jobs``：完整评估 durable job（G23 runner，可取消 / 可恢复）。

不重复制造：观测画像（attr_ops）、journal/CAS/锁（feature_edit）、选择引擎（G24）、
任务运行时（jobs/）、工具注册（tools/registry）。
"""
