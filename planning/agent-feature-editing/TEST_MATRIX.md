# TEST_MATRIX

## 后端（pytest，目录 tests/…/feature_edit/）

| 组 | 用例 | 里程碑 |
|---|---|---|
| journal 纯函数 | apply/invert 往返恒等；链校验；FIFO 溢出 dropped；replay(corpus) ≡ 顺序执行 | M2 |
| identity | 物化确定性（同指纹同 wfid）；FEATURE_ID_KEYS 解析；>20000 拒绝 | M2 |
| plan/preview | selection 模式读 IR 投影；explicit 模式；findings 全 code 覆盖；TTL 过期 | M2/M6 |
| executor CAS | expected_revision 不符→409+head diff；同 idempotency_key 二次→同 receipt；锁竞争→503 语义 | M3 |
| 越权 | 跨会话 ref→dataset_not_found（不泄存在）；无 owner token→401/404；agent commit 非 safe→confirmation_required；模型传 actor 被忽略 | M3 |
| 属性编辑 | 类型校验（int/float/str/bool）；枚举拒绝（distinct 词表外）；rename_field 全集生效；未选中要素指纹不变；undo/redo 往返 | M4 |
| 几何编辑 | repair_geometry（自交修复/闭环/去重顶点，用构造坏数据 oracle）；move_by 米单位换算数值 oracle（含高纬）；snap_to_ref 容差命中/无候选 fail-closed；缺单位拒绝 | M5 |
| 缓存失效 | overwrite 后 MVT/descriptor/ref_payload_cache 取新数据（ref_lifecycle 断言） | M3/M8 |
| mapspec 戳 | RefreshDataRevisionIntent 只改 content_revision；user-wins 显隐/样式零变化；锁顺序无死锁 | M6 |
| durability | 恢复后 journal/policy/plans 键透传（若被剥离→修复）；checkpoint 回滚后 journal 一致性 | M8 |
| 并发 | 两并发 commit 同 ref（一成一 409）；undo 与 commit 竞争；SSE 乱序（戳后到） | M8 |
| 工具真链 | ToolDispatchService.dispatch 真入口：preview 纯读；commit policy 闸；Pi surface 投影含新工具（tier2/state_mutation 非 destructive）；tier/side-effect 声明断言 | M6 |

## 前端（vitest）

| 组 | 用例 | 里程碑 |
|---|---|---|
| diff.ts | delta→展示模型；有界 | M7 |
| api client | 双头鉴权；409/410 收敛 | M7 |
| EditProposalCard | 状态机 open→approving→applied/stale/error；findings 渲染；reject 路径 | M7 |
| EditPreviewOverlay | 改后几何渲染；确认前零持久写 | M7 |
| featureCommand undo | 入栈/出栈/REST 调用/CAS 409 收敛 | M7 |
| 增量回显 | dataset_updated/mapspec 镜像→data-plane 失效重拉；user-wins 保持；无 restyle | M7/M8 |
| 会话切换 | selection/preview store 冲刷；旧会话 receipt 不落新会话 | M8 |

## E2E（M9）

Playwright（若本地可跑）或 API 驱动 journey + 组件测试组合（如实披露）：上传 3+ 学校 fixture → 选中 → NL 编辑类型 → 预览卡 diff → approve → 地图数据新 revision + journal 记录 → undo 恢复 → 历史可见。

## 门禁

ruff（改动文件）→ pytest 邻域 → 全量相关族 → tsc/vitest 前端邻域 → openapi/生成物再生 → check_generated_staleness。不等待远端 CI；跳过项如实披露。
