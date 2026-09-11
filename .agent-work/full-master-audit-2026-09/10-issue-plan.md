# 10 — Issue Plan（findings → issues → 修复批次）

汇总：A(8) + B(19) + C(9) + D(15) + 主agent(5，与 D 重叠后) ≈ **47 独立 findings**
P0=0 ｜ P1=8 ｜ P2=13 ｜ P3≈26

合并裁决（见 00b）：10 个 V7/V8 分支合入后需逐条复核；已确认 modelops-v3
修复 B-1（import 路径），其余待复核。

## P1（独立 issue）

| ID | 域 | 标题 | 预期处置 |
|---|---|---|---|
| B-1 | modelops | modelops 工具 import 不存在的模块，两主工具必崩 | modelops-v3 合并关闭 |
| B-2 | modelops | ReuseStore._evict glob 层级错，容量上限全失效（磁盘无界） | Phase B 修 |
| B-3 | modelops | 并发 run 共写 _RUN_LOCAL accumulator（静默损坏/泄漏） | 合并后复核+修 |
| C-1 | frontend | agent remove_layer durability POST 永不发出（守卫参数缺失） | Phase B 修 |
| C-2 | frontend | durability/component 响应缺 await 后会话复核（跨会话污染） | Phase B 修 |
| C-3 | frontend | agent reorder_layer 只改 HUD store（spec 层不动/不持久化） | Phase B 修 |
| D-1 | platform | SSRF 启动校验 fake-IP DNS 硬失败（allowlist 缺 overpass） | Phase B 修（第一批） |
| D-2 | integration | 16 生成物 stale + HEAD 手改指纹无效 | 合并后统一再生成 |

## P2（独立 issue）

A-1（TOOL_TIMEOUT 分类→plan livelock）、B-4（memmap+polygonize UnboundLocal）、
B-5（检测框 pad 偏移错位）、B-6（度/米口径漂移）、B-7（RasterReader 泄漏）、
B-8（registry 多副本不刷新）、B-9（driver 孤儿恢复阻塞）、B-10（Model 非能力
实体→V8 输入）、C-4（vector-pdf 死接口）、D-3（SSRF 哨兵死代码）、D-4
（project.py 同步 DB）、D-5（quality_runner 映射键 bug）、D-6（upload TS 契约漂移）。

## P3（umbrella ×4，按域 checklist）

- Umbrella-A（harness/tools 卫生）：A-2..A-8
- Umbrella-B（modelops/data 卫生）：B-11..B-19
- Umbrella-C（cartography/frontend 卫生）：C-5..C-9 + 前轮遗留 ST-P3-5、ST-P2-3
- Umbrella-D（platform/integration 卫生）：D-7..D-15 + M-3(=D-8)

## 合并阶段 issue（integration）

- INT-MERGE：10 分支合入 + ADR 0130 撞号重编号（0131/0132）+ 0035 migration
  mergepoint + CHANGELOG 合并 + 生成物再生成（一并解决 D-2）。

## Phase B 修复批次序（任务书 §13）

| Batch | 内容 | 涉及 |
|---|---|---|
| 0 | 环境解阻：D-1(allowlist)+M-4(fcntl 守卫) —— 先修否则无法本地验证 | D-1, D-7 |
| M | 10 分支合并 + INT 集成修复 | 00b 计划 |
| 1 | 合并后复核全部 findings（更新 issue 状态） | 全部 |
| 2 | 前端状态机修复 | C-1, C-2, C-3, C-9 |
| 3 | ModelOps 运行时修复 | B-2, B-3, B-4..B-8 |
| 4 | 工具/规划运行时 | A-1, A-4, B-9 |
| 5 | 平台/安全 | D-3, D-4, D-9, D-13, D-14, D-15 |
| 6 | 质量/契约工具 | D-5, D-6, D-12, C-4/C-5 |
| 7 | P3 umbrella 清理 | 各 umbrella |
| 8 | 生成物再生成 + preflight 绿 | D-2 |

每 batch：read→reproduce→fix→targeted tests→neighboring regression→review→commit→close。
