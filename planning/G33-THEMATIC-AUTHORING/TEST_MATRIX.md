# G33 TEST_MATRIX（随实现滚动更新状态：planned / written / passed / blocked）

## 后端单测（tests/unit/thematic_authoring/）
| ID | 覆盖 | 验收场景 | 状态 |
|---|---|---|---|
| T-B1 | stats: 无效值分型/零有效/常数/缺失 | 6 | passed |
| T-B2 | assign_class 断点边界（恰等、左右邻、under/over、大整数、负值） | 2,7,8 | passed |
| T-B3 | 断点校验（相等/倒序/重复/非有限/超帽）拒绝且指出索引 | 3 | passed |
| T-B4 | 常数字段单级退化 [v,v] | 4 | passed |
| T-B5 | quantile 重复值 k 退化 + requested vs actual | 5 | passed |
| T-B6 | 类别 1/"1"/""/缺失语义 + other 计数守恒 | 9,10 | passed |
| T-B7 | 高基数分页+other 策略 | 10 | passed |
| T-B8 | divergent 中心资格（全正拒绝/跨0通过） | 12 | passed |
| T-B9 | size_channel 面积∝数值精确性 + 样本同函数 | 13 | passed |
| T-B10 | inspect/preview/commit 服务链（真 mapspec 引擎+临时 DATA_DIR） | 1,19,20 | passed |
| T-B11 | CAS 冲突/源 revision 变化 409 | 15 | passed |
| T-B12 | undo 恢复完整表达（不只颜色） | 19 | passed |
| T-B13 | 一致性 oracle：只改 legend 或只改 paint 必须被检出 | 22 | passed |
| T-B14 | 路由鉴权/404/409/回执形状 | — | passed |
| T-B15 | 工具注册+dispatch 真实执行 | 20 | passed |
| T-B16 | SVG 导出 oracle：边界值同色、nodata、大小/线宽 | 21 | passed |
| T-B17 | 统计性能与缓存（10 万级行数/时间/内存/命中） | 14,§六 | passed |
| T-B18 | MVT/无全源层 honest degraded（不从视窗推断） | 14 | passed |
| T-B19 | 迟到统计请求/切层 generation 保护（前端） | 18 | passed |
| T-B20 | UI 与 Pi 同参数等价回执 | 20 | passed |

## 前端测试（vitest，frontend/）
| ID | 覆盖 | 状态 |
|---|---|---|
| T-F1 | contracts.ts assignClass/sizeForValue 与 Python 孪生同值（共享 fixture 断言文件） | passed |
| T-F2 | 面板渲染/断点编辑/错误定位/恢复自动 smoke | passed |
| T-F3 | thematic-paint categorical default 镜像 | passed |

## 旅程（真实 Pi 子进程 + stub LLM，经生产 dispatch）
| ID | 覆盖 | 状态 |
|---|---|---|
| T-J1 | 查看表达→人工断点→检查图例→恢复自动（webgis_thematic_style_*） | passed |

## 导出闭环
| ID | 覆盖 | 状态 |
|---|---|---|
| T-E1 | SVG 编译（后端 compile_mapspec_to_svg）+ 受权下载 + 独立回读断言 | passed |
| T-E2 | PublicationIR/PDF 路径对 size/线宽通道的支持核验（不支持则能力表披露+入口拒绝） | passed |
