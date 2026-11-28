"""Project Exchange —— 项目级工程包导出/导入（05 线 · 工程包交换与可移植复现）。

层级 A（原生可重开工程包）：manifest（依赖闭包 + 分类 + 复现分级）→
标准 GIS 物化（复用 data_delivery writers / cog / GTiff）→ ZIP 容器
（staging → 原子发布 → exports 下载链）→ 隔离导入（不可信 ZIP 防护 +
拓扑序注册 + ID 重映射 + 回滚）→ 复现证据报告。

单一事实源边界：本包**不新建**任何存储后端/任务中心/权限判定 ——
数据走 BlobStore/exports/uploads，任务走 DurableJobStore，授权走
ProjectService/owner 侧车，指纹走 provenance.fingerprint。
"""
