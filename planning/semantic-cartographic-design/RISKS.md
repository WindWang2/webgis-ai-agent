# RISKS

| # | 风险 | 缓解 |
|---|---|---|
| R1 | tools.py 是共享中心文件，与 PR #1868（app/tools/__init__.py）rebase 冲突 | 最小 patch；新工具注册若必须触碰 __init__.py 用单行追加；PR 前二次比对 changed filenames |
| R2 | 既有 issue #1779（cartography_runtime UnboundLocalError）在相近路径 | M2 接线不调用该函数；测试若撞上，先核实是否已修，未修则绕开并披露 issue 号 |
| R3 | 语义契约测试计数硬断言（45） | 显式走契约协议：JSON + 断言 + 化解条目三处同步，单测钉死新码 |
| R4 | 候选引擎与 composition_selection 职责混淆（重复建设） | 引擎内部**调用** select_composition_alternatives 取组合面，自己只做表达模型层 + 数据资格门控；grep 断言不复制其评分逻辑 |
| R5 | user-wins pin 与 quality_loop 修复冲突 | 只复用既有守卫（quality_loop.py:759 suppressed_repairs + representation user_pin），不新造；M3 测试锁定 |
| R6 | map_state 客户端可伪造（#1778 纪律） | 设计选择/落选理由只经服务端路径写入白名单键；不信任前端回传的选择状态 |
| R7 | G05 场景 fixture 漂移（scenarios.json pin 纪律） | 按既有生成器协议 pin；不手改 JSON |
| R8 | 资源争抢（其他 worktree agent 并行） | pytest 不加 xdist；前端构建不与后端重测并行；-j2 纪律 |
| R9 | 候选注水（<2 也凑 2） | 契约允许 1 个（如实）；测试锁「不注水」 |
| R10 | heatmap 用户显式要求被静默丢弃（违反 user-wins） | typed_blocked 显式返回 + 披露文案；不静默换方案 |
