# G3D05 独立评审记录

## 第一轮（2026-10-27，独立 reviewer subagent，未参与实现）

**范围**：`git diff origin/master` 全量（89 文件 ~6.1k 行）+ 定向测试复跑（后端 105、前端 105 全过）+ 两次活体复现。

**判定：NEEDS_FIXES**（3×P1 + 4×P2 + 3×P3；ABI 镜像一致性/诚实语义/安全/回归覆盖四类**零发现**）。

### P1（全部修复，commit 9cd37f114）

1. **story 章节场景载荷非法**：keyframe 只带 mode/exaggeration，直发缺
   terrain.source 必 422（2.5d 章节从不生效且静默）；mode=2d 章节整笔替换
   抹掉会话 terrain。→ 修复：`mergeSceneRotary` 纯函数合并（保留
   terrain.source；2d 不销毁配置）+ 失败 toast + 4 项测试。
2. **autoplay 自暂停**：maplibre `pitchstart/rotatestart` 对程序化
   flyTo 也触发（验证于安装的 maplibre dist），章节相机切换即接管误判。
   → 修复：`originalEvent` 门控（map-panel 手势仲裁同款）。
3. **双标签并发首存 500**：SELECT-then-INSERT 竞态撞唯一约束裸
   IntegrityError。→ 修复：捕获回滚重走 UPDATE+1（并发测试锁
   revision 单调 [1,2]）。

### P2（全部修复，同 commit）

4. 滑杆可发无 source 载荷且失败无反馈 → 解析 raster-dem 源 id +
   toast；5. model/migration 约束漂移 → `__table_args__` 同名同形；
6. manifest 阈值硬编码 → import `SCALE_TRUST_PITCH_MAX` 单源 + 边界
   10°/10.1° 测试；7. 后端 NaN view.pitch 静默压制检查 → isfinite 视同
   缺席（与前端对齐）+ 测试。

### P3（#9/#10/#11 修复；#8 记录为 ADR 残余）

披露面 zh-only 文案（ADR-0275 已知边界，en-US 用户看到中文披露卡）——
i18n 键化列为后续批次，不在本 PR 范围。

### 零发现类别（评审员明确核实）

ABI 镜像全链一致（contracts/schema/registry/taxonomy/ABI/矩阵/模板/
前端三镜像/catalog/types.generated）；诚实语义（披露不移除、阈值互锁、
降级收据双端、无虚构高程）；安全（所有权守卫/深度+512KB 预算/ORM 参数/
无新增 SSRF）；回归覆盖（契约扩展带溯源注释、getPitch 为真实 API）；
scene 通道队列/会话守卫；zh/en i18n 键集一致。

## 第二轮

见下文追加。

## 第二轮（2026-10-27，另一独立 reviewer，最终门）

**判定：APPROVE_WITH_RESIDUALS**（R1 修复全部验证正确；无新 P0/P1/P2；门禁全绿：cartography 2050、定向 60、前端定向 192 文件 1882、tsc 双项目 0 错、eslint 清零；并发/UX/会话守卫复查干净）。

R1 修复验证要点：mergeSceneRotary 载荷经后端 `_handle_setscene` 校验合法；originalEvent 门控对照 maplibre-gl dist 验证（程序化 flyTo 无 originalEvent、键盘/滚轮带）；IntegrityError 恢复路径事务正确、并发测试真实覆盖竞态。

### P3 残余（4 项，全部清偿于后续 commit）

1. story 章节合并未解析 raster-dem 源 id 兜底 → 已补（R2-1）；
2. assets.py 竞态分支裸 raise 在 except 帧外（RuntimeError 掩盖原异常）→ 改为持有引用重抛（R2-2）；
3. 接管未绑 zoomstart（滚轮缩放不打断播放，与 map-panel 仲裁面不一致）→ 已补（R2-3）；
4. fallback source 路径无测试 → 已补（R2-4）；P1b 门控为 map-panel 已测模式同款，页级测试留作后续。

### 记录为观察（不改）

- 无会话时场景控制操作显示「未生效」toast（诚实可见优于静默；busy 态必复位）；
- mode=2d 章节不强制关 is3D —— 单向桥的设计行为（用户控制优先，map-panel 注释锚定）。

**最终结论：两轮评审收敛，P0/P1/P2 全清，残余仅文档化的 i18n 键化与页级 E2E 后续项。**
