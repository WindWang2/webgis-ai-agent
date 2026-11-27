# DECISIONS — 04 线·交互式只读专题地图发布闭环

## D1：发布快照是新契约，不改 PublicationIR
PublicationIR（`app/lib/cartography/publication_ir.py`）是纸面 PDF/atlas 的版面 IR。
交互只读发布使用独立的 `PublicationSnapshotDocument v1`（`app/services/publication/contract.py`），
引用源 MapSpec fingerprint/revision，不重写、不复用其页面布局语义。WP4 要求"与纸面导出共用
真实数据/分类/可见层/署名"通过共享源（MapSpec + accessibility_manifest）达成，而非共享 IR 结构。

## D2：物化采用"发布时投影落盘"而非读者端实时解析会话 ref
会话 ref 是 session 作用域、可 GC 的（`session_data_protocol.py`）；读者访问必须不依赖会话存活。
发布时做有界 release projection（字段白名单+精度+bbox+预算），产物落
`data/publications/{publication_id}/`，读时二次过滤。写时投影保证撤销字段后残留数据也不含
敏感字段（纵深防御）。数据量由既有 MapSpec 校验上界约束（inline ≤5000 要素）+发布总预算兜底。

## D3：访问模型 = private 默认 + 可撤销 link；无匿名公开注册面
复用 Report 分享范式（share_code=token_urlsafe(12)、share_expires_at naive UTC、撤销=置空），
新增 access_policy CHECK('private','link')。公开（public/匿名可发现）不在本期词表——目标明确
"不默认提供匿名公开"。过期/撤销一律 404 不泄露存在性（report 先例）。

## D4：发布/链接签发是作者显式操作，Pi 工具只读
Pi 工具族 = inspect/prepare/preview/validate（tier≤2，无副作用）。publish 与 link-mint 仅经
作者 API + UI 显式确认（两步：发布需 confirm token；链接签发本身是显式动作）。这与 plan_mode
破坏性确认 artifact 范式同构。Agent 永远不能替用户把地图设为可分享。

## D5：物化走 durable job（atlas_batch 范式），幂等键=publication_id:version
复用 `submit_durable_job` + `durable_job` 上下文 + JobKind 加性扩展；重复发布幂等；
取消/重启语义继承基建。发布事务：物化完成→原子置 published（CAS：仅 draft 可发布）。

## D6：读者数据面用物化 GeoJSON（有界），不新建 MVT 管线
发布快照自带 materialized per-layer GeoJSON；读者瓦片渲染走前端直接加载 GeoJSON source。
MVT 复用需服务端从物化数据重新切片，属重复管线；目标数据形态（区县/学校点面）有界，
GeoJSON 有预算护栏即可。大数据层超预算 → typed 阻断（"无法安全投影则不发布该层"）。

## D7：迁移锚定 master 单 head `aa356ce2fcdc`
PR #2290（01 线）的 g5201_turn_attempts 不在 master；本线迁移 down_revision 固定 master head，
命名沿 gNNNN 惯例 → `g5202_map_publications`。

## D8：前端公开页走 Next.js `/p/[code]`（story 页先例）
无 middleware 鉴权重定向；读者页不挂登录态，凭 share_code 经 URL 路径访问 API；
CSP 由 proxy.ts nonce 注入自动覆盖；瓦片/数据 fetch 用同源 /api（reader-api skipAuth）。

## D9：与 #2289 文件面板、StoryMap、图册零重写
作者入口复用既有 rail/panel 惯例最小接线；不触碰 generated-files-panel、storymap、atlas。

## D10：subagent 纪律
探查阶段用 2 个只读 Explore agent（已完成，合计 ≤3 并发）。实现期主 agent 串行实现共享
接线面；独立 review 用 1 个 subagent（非作者）做 R1；R2 主 agent 复查 + 冲突检查。
