# G3D05 决策记录（实施期）

1. **scene_disclosure 内容绑定 spec.scene，options 禁复制场景字段**
  （`validate_scene_disclosure_payload` fail-closed）—— 防 options/spec
  双真相漂移；渲染/导出/矢量孪生三链同源派生。
2. **3D 模板输出仅 interactive+png**：矢量表达由导出管线显式降级（收据），
   模板不声明矢量支持 —— 与 scene_planning 静态媒介不出 3D 同源。
3. **比例尺披露而非移除**：透视时保留比例尺 + 小字注记（缺披露组件
   「不可信」/已披露「仅供参考」）；平面零噪音。阈值 10° 前后端同值
   互锁（契约测试）。
4. **用户场景写通道复用 SetSceneIntent 事务管线**（SetSceneBody 入联合）
   而非新端点 —— origin/幂等/409/503 语义免费继承。
5. **Agent→is3D 单向桥**：spec.scene.mode∈{3d,2.5d} 拉起 is3D；反向不强制
   关闭（用户本地开关不被服务端回拽）—— 修补「Agent 场景从不激活」的
   既有分叉，同时保用户控制优先。
6. **sceneCameraIdentity 并入 terrain 身份**：exaggeration-only 变更也重放
   地形效果（此前只有相机身份触发）。
7. **StoryMap v1.1 只扩两旋钮**（keyframe.scene{mode,exaggeration}）：
   完整场景仍归会话 MapSpec.scene；编译器 scene_enabled 证据门
   （raster-dem 在场才投影弧角色场景档）。
8. **持久化选会话作用域单行 + revision 单调**（migration 0099）而非版本
   快照表：恢复语义（最新可用）足够，审计粒度由导出 bundle 承担；
   损坏行 typed 422 不伪造默认。
9. **profile 复用图表渲染链 + 独立 data-kind="profile"**：语义语料按
   marker 分族，渲染器不重复实现。
10. **前端披露组件 zh 文案模块级常量**（过 no-hardcoded-cjk +
    no-raw-cjk 双门禁）：i18n 键化下一批（PR 残余限制如实披露）。
11. 组件计数契约 20→22、scene quality 词表 +3、诊断词表 +2：均为有意
    扩展，测试同步更新并注明来源。
