# Theme & Palette Catalog

> 由 registry 生成；颜色真值：`palettes.py`（thematic 色带）与前端
> design tokens（chrome）。本目录只登记描述与引用（ADR-0101 D4）。

## Palettes

| id | 语义族 | 色盲安全 | 打印安全 | 灰度最小ΔL |
|---|---|---|---|---|
| Blues | sequential | ✓ | ✓ | 0.1196 |
| Dark2 | qualitative | ✓ | — | 0.0178 |
| Gray | sequential | ✓ | — | 0.0513 |
| Greens | sequential | ✓ | ✓ | 0.1637 |
| Inferno | perceptual_uniform | ✓ | — | 0.0237 |
| Magma | perceptual_uniform | ✓ | — | 0.0243 |
| Oranges | sequential | ✓ | ✓ | 0.126 |
| Pastel1 | qualitative | — | — | 0.0258 |
| Plasma | perceptual_uniform | ✓ | — | 0.0388 |
| PuOr | diverging | ✓ | ✓ | 0.242 |
| Purples | sequential | ✓ | ✓ | 0.1215 |
| RdBu | diverging | ✓ | ✓ | 0.3509 |
| RdYlGn | diverging | — | — | 0.021 |
| Reds | sequential | ✓ | ✓ | 0.0915 |
| Set1 | qualitative | — | — | 0.0188 |
| Set2 | qualitative | ✓ | — | 0.035 |
| Viridis | perceptual_uniform | ✓ | ✓ | 0.0694 |
| YlOrRd | sequential | ✓ | ✓ | 0.108 |
| classic | native_heatmap | — | — | 0.0 |
| magma | native_heatmap | ✓ | — | 0.0 |
| thermal | native_heatmap | — | — | 0.0 |
| viridis | native_heatmap | ✓ | — | 0.0 |

## Themes

### cartographic.dark_interactive（交互暗色主题，profile=dark）
- 输出：interactive, png, pdf, svg；适配版式：minimal, standard
- 排版：text-title/text-caption，标题字重 600
- chrome token 引用：surface=surface-panel, ink=text-primary, chrome-bg=map-chrome-bg
- 推荐：sequential=[]；diverging=['RdBu']；qualitative=['Dark2', 'Set2']；perceptual=['Viridis', 'Magma', 'Inferno', 'Plasma']
- 注：暗背景优先感知均匀族（Viridis 系）—— 若必须用 sequential 色带，应反转使用顺序（高值→低亮度端）并披露
- 注：发散带 RdBu 两端（深红/深蓝）在暗底上端部沉底 —— 暗底发散建议提高端部明度或改用 perceptual_uniform 族
- 注：深色 chrome token（map-chrome-* dark 分支）

### cartographic.government（政务报告主题，profile=light）
- 输出：interactive, png, pdf, svg；适配版式：report, presentation
- 排版：text-title/text-caption，标题字重 700
- chrome token 引用：surface=surface-panel, ink=text-primary, chrome-bg=map-chrome-bg
- 推荐：sequential=['Blues', 'YlOrRd', 'Greens']；diverging=['RdBu']；qualitative=['Pastel1', 'Set2']；perceptual=['Viridis']
- 注：政务场景：大面积柔和填色（Pastel1）+ 高权重标题；地类/区划配色沿用规划惯例

### cartographic.high_contrast（高对比无障碍主题，profile=high_contrast）
- 输出：interactive, png, pdf, svg；适配版式：—
- 排版：text-title/text-caption，标题字重 600
- chrome token 引用：surface=surface-panel, ink=text-primary, chrome-bg=map-chrome-bg
- 推荐：sequential=['YlOrRd', 'Blues']；diverging=['RdBu']；qualitative=['Dark2', 'Set2']；perceptual=['Viridis', 'Inferno']
- 注：WCAG AA：chrome 前景/背景对 ≥4.5:1（前端测试对 globals.css 实测）
- 注：状态不得仅靠颜色表达（图例同步符号形状/标签）

### cartographic.light_interactive（交互亮色主题，profile=light）
- 输出：interactive, png, pdf, svg；适配版式：minimal, standard, dense
- 排版：text-title/text-caption，标题字重 600
- chrome token 引用：surface=surface-panel, ink=text-primary, chrome-bg=map-chrome-bg
- 推荐：sequential=['YlOrRd', 'Blues', 'Greens', 'Oranges']；diverging=['RdBu', 'RdYlGn']；qualitative=['Set1', 'Set2', 'Dark2']；perceptual=['Viridis', 'Magma', 'Inferno', 'Plasma']
- 注：默认工作区主题；chrome 颜色全部引用前端语义 token

### cartographic.presentation_screen（演示投屏主题，profile=dark）
- 输出：interactive, png；适配版式：presentation
- 排版：text-heading/text-caption，标题字重 700
- chrome token 引用：surface=surface-panel, ink=text-primary, chrome-bg=map-chrome-bg
- 推荐：sequential=['YlOrRd', 'Blues']；diverging=['RdBu']；qualitative=['Set1', 'Dark2']；perceptual=['Plasma', 'Viridis']
- 注：远距离可读：标题走 heading 级、chrome 最小 12px

### cartographic.print_paper（纸张印刷主题，profile=print）
- 输出：png, pdf, svg, print；适配版式：academic, report
- 排版：text-title/text-caption，标题字重 700
- chrome token 引用：surface=surface-panel, ink=text-primary, chrome-bg=map-chrome-bg
- 推荐：sequential=['YlOrRd', 'Blues', 'Greens', 'Reds', 'Oranges', 'Purples']；diverging=['RdBu', 'PuOr']；qualitative=[]；perceptual=['Viridis']
- 注：黑白打印安全：推荐清单全部 print_safe（灰度 ΔL 严格可分级）
- 注：红绿色盲不友好的 RdYlGn 不进入推荐清单；发散用 RdBu/PuOr
- 注：qualitative 色带灰度打印均不可分级 —— 类别面黑白输出改用符号形状/填充图案区分（映射由导出侧承担，planned）

### cartographic.publication（出版印刷主题，profile=print）
- 输出：png, pdf, svg, print；适配版式：academic, report
- 排版：text-title/text-caption，标题字重 700
- chrome token 引用：surface=surface-panel, ink=text-primary, chrome-bg=map-chrome-bg
- 推荐：sequential=['YlOrRd', 'Blues', 'Greens', 'Reds', 'Oranges', 'Purples']；diverging=['RdBu', 'PuOr']；qualitative=[]；perceptual=['Viridis']
- 注：正式出版：灰度安全 + 色盲安全双约束；类别面黑白输出改用符号形状/填充图案区分

### cartographic.remote_sensing（遥感影像主题，profile=dark）
- 输出：interactive, png, pdf, svg；适配版式：standard, dense
- 排版：text-title/text-caption，标题字重 600
- chrome token 引用：surface=surface-panel, ink=text-primary, chrome-bg=map-chrome-bg
- 推荐：sequential=[]；diverging=['RdBu']；qualitative=['Dark2']；perceptual=['Viridis', 'Magma', 'Inferno', 'Plasma']
- 注：暗底影像判读：感知均匀族优先；合成影像不参与专题设色语义

### cartographic.risk_communication（风险沟通主题，profile=light）
- 输出：interactive, png, pdf, svg；适配版式：report, presentation
- 排版：text-title/text-caption，标题字重 600
- chrome token 引用：surface=surface-panel, ink=text-primary, chrome-bg=map-chrome-bg
- 推荐：sequential=['Reds', 'YlOrRd', 'Oranges']；diverging=['RdBu', 'PuOr']；qualitative=['Dark2', 'Set2']；perceptual=['Inferno']
- 注：风险语义固定：高值=暖色深端；『无数据』不得画成『低风险』
- 注：色盲安全优先（RdBu/PuOr 发散；禁 RdYlGn 表达风险方向）

### cartographic.scientific（科学分析主题，profile=light）
- 输出：interactive, png, pdf, svg；适配版式：academic, standard, report
- 排版：text-title/text-caption，标题字重 600
- chrome token 引用：surface=surface-panel, ink=text-primary, chrome-bg=map-chrome-bg
- 推荐：sequential=['YlOrRd', 'Blues', 'Greens']；diverging=['RdBu', 'PuOr']；qualitative=['Set2', 'Dark2']；perceptual=['Viridis', 'Magma', 'Inferno', 'Plasma']
- 注：期刊/报告图：优先感知均匀与色盲安全色带；chrome 极简
- 注：色带语义必须与统计口径一致（发散带以中点为中心）

### cartographic.terrain（地形表达主题，profile=light）
- 输出：interactive, png, pdf, svg；适配版式：standard, report, academic
- 排版：text-title/text-caption，标题字重 600
- chrome token 引用：surface=surface-panel, ink=text-primary, chrome-bg=map-chrome-bg
- 推荐：sequential=['Oranges', 'YlOrRd', 'Greens', 'Blues']；diverging=['RdBu']；qualitative=['Dark2']；perceptual=['Magma', 'Viridis']
- 注：地形族：hypsometric 设色以 Oranges 近似；晕渲光源参数必须披露

