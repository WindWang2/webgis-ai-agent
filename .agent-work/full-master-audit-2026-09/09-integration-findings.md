# 09 — Integration Findings（合并后集成审计）

详证：agents/D/integration-notes.md + 主 agent findings（M-1/M-2/M-3）。

1. 生成物 staleness（D-2/M-2，P1）：16 项 RED；HEAD 2aabdc43 手改指纹与重算不符
   （b3ea0091… vs 2d9b63c5…）；根因=多 epic 合并+7 个 post-merge 修复 commit 改
   输入未再生成。处置=分支合并后统一走 gen_* + staleness --update。
2. ADR（D-8/M-3，P3）：watermark=129 下 10 组撞号被容忍；CHANGELOG 5 处裸编号
   引用歧义。新增撞号已防（allocator）；处置=引用补 slug + 文档声明。
3. Migration（D-10，P3）：单 head 验证绿（alembic ScriptDirectory 权威）；4×0034
   merge 式消解 vs 自述重编号协议漂移（文档修正）。本轮 10 分支将带来 3 个新
   0035 撞号 → mergepoint 消解（遵循既有 c0d8322aa2cb 先例）。
4. Python/TS 契约（D-6，P2）：upload 响应 crs 可空性 + 9 字段缺失；drift gate
   仅 path 级，无字段级闸。
5. 质量工具自身缺陷（D-5 mapping 键 bug、D-12 CWD 相对路径、D-11 死代码）。
6. 平台可用性（D-1/M-1，P1）：SSRF 启动校验环境硬失败（fake-IP DNS）——同时
   阻断两个质量闸脚本。
7. 平台兼容（D-7/M-4，P3→本地全阻）：fcntl 裸 import。
