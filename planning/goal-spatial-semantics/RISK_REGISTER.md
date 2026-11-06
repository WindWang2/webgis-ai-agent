# RISK_REGISTER — GOAL 01

| 风险 | 等级 | 缓解 |
|---|---|---|
| 破坏既有 buffer/join/aggregate 测试（行为变更） | 高 | 适配层判定与既有路径对齐：declared/度样缺省路径判 pass（证据增强不改结果）；仅新增 blocked 类（投影样缺 CRS、无效范围）；全量跑相关测试目录 |
| 与 #1868/#1869 词表漂移 | 中 | 直接 import 其函数/常量而非复制 |
| app/tools/__init__.py 单行注册与他分支冲突 | 低 | 追加模式，rebase 时机械解决 |
| 前端图例改动触发 i18n/快照测试 | 中 | 仅添加可选显示，键复用既有 unit 词；跑 vitest 相关文件 |
| 50k 坐标采样预算 | 低 | 首个 ≤200 点采样即结构判定，O(1) |
| pyproj 缺席环境 | 低 | classify_crs 已有诚实缺省；契约 unknown 不虚构 |
