# 07 Reference Validation（参考文献验证汇总）

> 逐域书目核对见 domains/*.md §7。核对方式：审计 agent 逐条比对书目信息
> 与实现语义；存疑条目 web 核验。结论：**未发现虚构文献**；问题集中在
> 「标签-算法归属」与个别题录细节。

## 归属错误（已修/待修）

| 引用 | 问题 | 状态 |
|---|---|---|
| tarboton1997 被标为 D8 出处 | 该文是 D∞ 论文；D8 惯例应引 O'Callaghan & Mark 1984 | ✅ 标签修正 + 新增 ocallaghan_mark1984，terrain.flow/watershed/flow_length 改引 |
| viewshed 无引用且 approximate=False | 扇区视线角扫描是 R3 型近似 | ✅ 补 wang_robinson_white2000（PE&RS 66(1) 87-90，web 核验）+ approximate=True |
| network.pmedian_exact 引 church_revelle1974 | 该文是 MCLP 论文，非 p-median | ⏳（第二批 agent：p-median 改引 Hakimi 1964/1965 等；MCLP 实现时 church_revelle1974 归位） |
| luo_qi2009 venue 错误（实为 Health & Place 15(4):1100-1107）；2SFCA 源头 Luo & Wang 2003 未登记 | ⏳（第二批 agent） |
| reed1990 页码差 2 页 | 光学审计发现（NBR 引文） | ⏳ 小修 |
| tarboton1997 被用于 flow_length/strahler 语境 | 弱关联 | ✅ 已随 D8 修正换引 |

## 缺失引用（已补/待补）

| 算法 | 建议 | 状态 |
|---|---|---|
| interpolation.rbf | duchon1977（薄板样条） | ✅ |
| interpolation.nearest_neighbor | thiessen1911 | ✅ |
| remote.linear_unmixing（新增） | heinz_chang2001 | 🔧（光学 agent） |
| ndwi_gao（拆名新增） | gao1996 | 🔧 |
| terrain.contours | marching squares 出处（或 truth_source 披露对齐 hillshade_multi 风格） | ⏳ |
| LS factor McCool m 表 | mccool1987 | ⏳ |
| SAR 热噪声 | ESA IPF 文档 | ⏳ |
| UK 实践 | Isaaks & Srivastava ch.12 加注 | ⏳ |

## 书目准确性抽样核验（审计一致结论）

- 地形 13 键全部真实准确（steyn1980 SVF=⟨cos²ψ⟩ 引用尤为精准）。
- 地统计 11 键正确；journel_huijbregts1978 建议加注 Almeida & Journel 1994
  （collocated/MM1 现代表述）。
- 光学 19 键逐条核实：tasseled cap 三传感器系数（Crist & Cicone 1984 /
  Baig 2014 / Shi & Xu 2019）与发表值逐位一致。
- SAR 11 键无误：Lee 1980/1981、Frost 1982、Lopes 1990、Gamma-MAP
  MAP 方程经独立求导验证。
- 点格局：Diggle 1995、Mantel 1967、Knox、E阈值公式逐项核对正确。

## 引用基础设施

METHOD_REFERENCES（105+ 键，Wave 修复后）+ validate() 强制
descriptor.method_references 存在性 —— 新增引用必须先登记真实题录。
