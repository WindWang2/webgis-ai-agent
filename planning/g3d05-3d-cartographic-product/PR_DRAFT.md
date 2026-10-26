# feat(g3d05): 三维智能制图产品 —— 组合模板/场景控制器/导出诚实/StoryMap v1.1（ADR-0275）

**基线**：origin/master `1115d683a`（PR 创建前若 master 前移将 rebase 复验）
**分支**：`zcode/g3d05-3d-cartographic-product-v1`（独立 worktree，未触碰其他 G3D worktree）

## 问题证据（缺口，文件级见 planning/BASELINE.md）

组合模板库 10 个 pack 全 2D；`set_map_scene` 写 spec 但 `is3D` 闸门只由用户
开关驱动（Agent 的 3D 场景从不激活）；透视比例尺失真无披露、矢量导出压平
3D 无收据；StoryMap 无场景章节/无持久化/播放与用户争相机。

## 交付（D1–D7，ADR-0275）

- **D1** `composition_packs/scene_3d.py` 三模板（terrain_3d_scene/
  urban_3d_skyline/viewshed_scene_3d；scene_disclosure 必填槽；输出仅
  interactive+png，矢量显式降级不冒充）+ `scene_disclosure`/
  `elevation_profile` 组件全链（contracts→schema→registry→taxonomy→ABI→
  真值矩阵→模板→catalog→前端三镜像；payload 校验禁 options 复制场景字段）
- **D2** scene_quality +3 码（透视比例尺/标注遮挡/矢量降级）+
  set_map_scene 证据回传
- **D3** `SetSceneBody` 用户场景 mutation（复用 SetSceneIntent 事务管线，
  人机同真相零第二状态机）+ 前端 scene-mutation/scene-controller
  （2D/2.5D/3D、夸张证据门、pitch/bearing、垂直基准、reduced-motion）
  + Agent→is3D 单向桥 + terrain 身份进 sceneCameraIdentity
- **D4/D5** 导出诚实：`scale_unreliable_perspective`/`svg_3d_perspective_degrade`
  诊断（阈值 10° 前后端单源互锁）、比例尺披露注记（披露非移除）、矢量
  孪生/前端矢量路径降级收据、manifest scene 溯源块（相机/terrain/scale_trust）
- **D6** StoryMap v1.1（keyframe.scene/visible_layer_ids；编译器证据门）+
  story_map_assets 表（migration 0099）+ 会话资产端点/装载优先/保存按钮 +
  播放器章节场景应用 + 用户接管暂停&恢复
- **D7/M7** 三套产品级样例契约 E2E（城市三维/山地剖面视域/地形+StoryMap，
  合成数据显式声明）

## 验证（本地，详见 planning/EVIDENCE.md）

- pytest cartography gate **2050 passed**；新增 60+ 项（含负例反证）；
  golden corpus 再生成（+10）；storymap 存量 105 全绿
- 前端 vitest 全量 **480 文件 4533 passed**；tsc 双项目 0 错；eslint
  --max-warnings 0 清零
- 独立评审两轮（planning/INDEPENDENT_REVIEW.md）：R1 NEEDS_FIXES
  （3P1+4P2+3P3）→ 全部清偿（9cd37f114）；R2 结论见报告

## 与开放 PR 无重叠

#1771（G3D01 底图/DEM）共享文件仅新增 hunk；#1773（G3D03 量测）零文件
交集，剖面以接口契约（points/ref:profile-*）先行。详见 PR_OVERLAP_MATRIX。

## 残余限制（如实）

- 披露面 zh 文案 i18n 键化下一批（双 CJK 门禁已过；ADR 已知边界）
- Playwright journeys 未跑（以契约级 E2E 替代，PR 披露）
- elevation_profile 的 ref 工件生产端待 G3D03 合并（当前 inline/fixture）
- 本地验证不等线上 CI；不自动合并
