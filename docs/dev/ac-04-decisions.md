# ac-04 决策日志（本线实现层的裁量记录）

配套 ADR-0153；本文只记 ADR 没展开的实现细节裁量与理由。

## 1. 门禁挂载点：突变分支内 + commit 前（而非 `pre_commit_check` seam 之前）

`apply_mutation` 已有的 `pre_commit_check` 回调（#1070 锁内守卫复检）在 intent
dispatch **之前**执行，此时 `process_layer_ingestion` 尚未运行 —— 看不到
归一化后的 source_entry（inlineData 载体判定、ref 化、raster 转换都发生在
dispatch 中）。门禁放在两个 Upsert 分支内、`mapspec["sources"][...] = ...`
提交之前：拒绝 = 直接 return（候选 spec 未提交，零残留），advisory 写在
即将提交的 processed_layer / source_entry 上。既有突变逻辑零改动。

## 2. verdict：block ⟺ audit blocking 级（error 级不拦截）

任务书验收口径 =「**blocking 类** 100% 拦截或自动修复」。audit 的 error 级
（TOPOLOGY_OVERLAP / DUPLICATE_PRIMARY_KEY / RING_CHECK_FAILED）走 advisory +
修复计划闭环：其一，完全相同的重复要素会同时命中 DUPLICATE_FEATURE(warning)
与 TOPOLOGY_OVERLAP(error)（既有 audit 行为），若 error 拦截则纯重复数据也
被拦，与「重复是可裁决修复的非阻断问题」定位矛盾；其二，error 级全部有
自动修复路径（本线补齐三个新 op 后），拦而不修反而增加一轮往返。

## 3. 重复要素的 audit 双报不消除

同上：DUPLICATE_FEATURE 与 TOPOLOGY_OVERLAP 对 identical 对双报是 audit
既有行为（#322 引入的 overlap 判据 `intersects && area > thr` 对 identical
成立）。本线不改 audit 语义（现有测试钉死），依赖 deduplicate 先行的执行
顺序让重叠修复成为 no-op。

## 4. `crs_transform` 的 no-op 诚实剔除

MISSING_CRS 码映射到 crs_transform 候选，但当推断结果 == 目标 CRS
（最常见：无声明的度域数据推断出 4326）时，重投影是 no-op —— 计划层
把该 op 放进 `skipped_ops` 并说明原因，而不是留一个空转 op 在计划里。

## 5. `profile_outlier_policy` 的规则顺序

head_tail（整数值 + 相异值 ≤12 + max/min>100）优先于 clip_p99 —— 序数
长尾是更具体的结构；有硬 >3σ 点时 clip_p99 优先于 log —— 色带稳定需要
的是「一条裁剪线」而不是「整轴变换」；`suggested_clip` 用 **inlier 质量
的 p99**：原始 p99 在极值占比高时被极值本身吞掉（15+1 例中 p99=85003），
对制图无用。log 只推荐给无硬离群点的平滑重尾（n=9 时经典 3σ 被掩蔽的
场景）。

## 6. GK/UTM 歧义的优先级

带号前推 easting（>16×10⁶）是 CGCS2000 独有惯例 → 直接判 CGCS2000 带
（带号需 lon_hint）；CM 制 GK（easting≈5×10⁵）与 UTM 数值同域不可分 →
中国经度域先验 CGCS2000（显式 UTM/WGS 提示可压过先验）、非中国锚点 →
UTM、无锚点 → low + 不猜带号。

## 7. 跨族几何混合不变形

normalize_geometry_type 做族内归一（Polygon→MultiPolygon 等），**不做**
跨族变形（把 Point 变成面是捏造几何）。「单图层只画一类」的毁图现象由
`geometry_mix.mix_ratio` advisory 提示 03 线拆层解决 —— 诚实披露优先于
强统一。

## 8. 门禁审计预算 = 5000（对齐 #687）

inline 载体本就有 5000 要素硬门（超过必须走 ref:）；门禁逐要素审计用同
一预算，超帽不静默全量审计而是 advisory 如实披露（大载荷的全量审计归
ingest/Celery 路径）。

## 9. `pytest-xdist` 进 requirements-dev.txt

门禁书的里程碑命令带 `-n 2`，但仓库 dev 依赖没有 xdist（venv 装了也跑不
了任务书命令）。加入 dev 依赖（测试工具，不进生产镜像，符合 DEPS-06 拆分
意图）。

## 10. 与并行线（02/03）的协调事实

本 worktree 建立时 02/03 线 worktree 已存在（`webgis-wt-ac-02/03`），其
venv 均为空 —— 未合入任何 profile 契约变更。本线按任务书 §8 拥有
`outlier_policy` / `crs_confidence` / `geometry_mix` 字段定义权，提供
`default_quality_profile()` 默认值兜底；若 03 线先行合入以其 schema 为准
（PR 描述注明协调点）。

## 11. 门禁扩展键不参与 profile_fingerprint

`profile_fingerprint` 由 `process_layer_ingestion` 在门禁钩子**之前**对
基础 profile（bbox/fields/quantiles 等漂移锚）计算；钩子随后把 6 个门禁
键并入同一 profile dict —— fingerprint 覆盖面不含门禁键。这是有意的：
漂移检测比对的是 fingerprint（同一数据的两次门禁评估对扩展键是确定性的，
不构成漂移），扩展键是附加读面。UpsertSource 路径（无 ingestion 画像）在
门禁放行时创建仅含 6 个门禁键的画像 dict，键集自洽、可独立消费。

