# G3D03 RISKS

| 风险 | 等级 | 缓解/披露 |
| --- | --- | --- |
| 垂直基准声明靠调用方（GeoTIFF 无 z 元数据），错报 datum 无法检测 | 中 | 契约词表 + unknown 诚实缺省 + quality_flags 披露；跨 datum 混算硬拒。ADR-0274 明示不内置 geoid 换算 |
| 地表距离/面积为采样近似，无解析误差界 | 中 | 半步收敛指示 delta_rel（经验量级）+ descriptor limitations；平面精确 oracle 锚定 |
| LOS 遮挡点定位精度受采样步长限制 | 低 | 默认 ~1 像元步长 + meta 披露；步长参数可收紧（预算护栏防爆炸） |
| 折射 k=0.1307 为标准大气常数，不随气象变化 | 低 | curvature 模型元数据全披露（model/k/R_eff/公式）；默认 none |
| 地理 CRS 水平距离用 cos(lat) 网格近似（带向不修正） | 低 | 与全库既有政策一致（band_math/viewshed 同款）；椭球测地距离作为独立参考量并列输出 |
| 新增 4 算法/4 工具改变注册表投影基数 | 低 | g02 O12 计数钉扎 237→241、execution catalog/tool 计数、benchmark/quality 生成物全部 regen 并过 drift 门 |
| 未知垂直基准 + 绝对高程覆盖值混用（两个 unknown 来源） | 中 | 覆盖值缺省继承 DEM 声明（单源语义）；显式 override_vertical_datum 不同即拒；PR 描述披露残余情形（同报 unknown 的两个独立来源无法机器区分） |
| 前端 3D 消费（G3D02 高亮/剖面折线）尚未接 | 中 | 本 PR 只交付 typed GeoJSON/MapSpec 通道（schema 校验过）；G3D02 方向消费属其边界，PR 明示为可选后续 |
| 建筑高度/体积、机场净空扫描未实现 | 中 | 工作单 M4 可选项；无实证数据源与专业规则参数时不虚构（ADR-0274 §5 诚实边界） |
