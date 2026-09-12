# 过时与无效代码清理记录

日期：2026-09-12。分支：`codex/stale-code-cleanup`。
审查基线：`155b401e038591e45fdd9ee51470a3508c015d88`。

本次根据当前仓库的调用关系清理迁移残留，修复配置读取和失效交互，并同步相关测试、依赖清单及文档。开发与验证在独立 Git worktree 中完成，未修改主工作目录里的业务文件。

## 审查项处理

| 编号 | 问题 | 处理结果 |
|---|---|---|
| R01 | 无效 ESLint 抑制注释使零警告检查失败 | 删除失效 directive；同时整理两处无效 Ruff noqa，移除测试中的未使用变量 |
| R02 | 取消／清理超时仍只读取旧环境变量名 | 优先读取 `CLEAR_QUIESCE_TIMEOUT_S`、`CANCEL_WAIT_TIMEOUT_S`，兼容不带 `_S` 的旧名；覆盖默认值、新名、旧名及同时设置的优先级 |
| R03 | 制图 QA 委派被描述为可开启的生产能力，但没有生产调用者 | 保留实验实现，修正模块与 ADR 的启用承诺；明确开关仅门控显式 helper 调用，finalizer 和上下文组装仍未接入 |
| R04 | 扩展版本保留配置没有消费者，远端 registry 配置未实现 | 安装／回滚 CLI 默认读取 `EXTENSIONS_KEEP_VERSIONS`，显式参数优先；移除无效 `EXTENSION_REGISTRY_URLS` 并更新生成 schema |
| R05 | 退场的图层 CRUD 服务和旧 DTO 仅由测试维持 | 删除 `LayerService`、旧 Layer／Task DTO 与其专属测试；保留其他 API 模型、现行任务响应模型和数据库表 |
| R06 | What-if 同步入口的第二个 return 不可达 | 删除重复块；保留同步包装、异步实现及注册入口 |
| R07 | 旧 HUD 历史栈及空 `clearTask` 接口失效 | 删除旧引擎及专属测试，原子移除空接口、订阅、调用与 mock；保留 Workbench 撤销和 Explorer 任务 slice |
| R08 | 地图重构遗留旧底图／专题／高亮模块和空回调 | 清理旧模块及测试，移除 `raiseSelectionHighlight` 空回调；保留现行地图协调、注记重建和抬升逻辑 |
| R09 | 多组前端组件、hook、API 客户端没有产品入口 | R08／R09 合计删除 17 个源文件及其专属测试；混合测试中只移除旧实现的用例 |
| R10 | 无调用者的依赖仍在安装清单 | 移除三个 `@dnd-kit/*` 直接依赖、`tailwind-merge`、对应优化配置及锁文件中的孤立记录 |

额外修复：

- `RuntimeInspector` 未提供 fetcher 时显示不可用状态，避免出现点击后永久加载的无效按钮。组件仍未挂载到宿主面板。
- 真实 `MapActionHandler` 的命令查询增加自有属性检查。`constructor`／`__proto__` 原本会落入对象原型属性并在命令校验处抛错；现在和普通未知命令一样返回失败终态并退出队列。回归用例验证终态、出队及无命令副作用，并覆盖无效参数。
- 删除只读取旧 renderer 源码的 Python 白名单测试，在真实命令处理组件中验证拒绝行为。
- 协同消息测试等待实际 `doc` 响应到达，替换固定 20ms 等待，减少并行调度导致的偶发失败。
- 修正抽屉焦点恢复测试的打开流程，并为防双击用例控制两次点击的时间差；Markdown 渲染次数测试先等待真实模块加载，避免把冷加载调度与稳态渲染行为混为一谈。
- 配置模板测试写入临时文件时显式指定 UTF-8，修复 Windows 默认 GBK 无法编码模板内容的失败。
- 锁续约丢失测试等待实际续约失败信号，替换固定 60ms 睡眠，保留状态与严格模式异常断言。
- 更新 README、技术说明和 CI 注释中的 Next.js／React 版本及 Pi 默认启用状态。没有升级依赖版本。

## 保留边界

没有把静态导入计数为零的文件一概删除：保留候选 planner、视觉评估扩展点、动态工具加载、脚本运行时校验入口、渲染一致性测试 oracle，以及仍被生产引用的兼容 facade 和地图常量。

制图 QA 委派的生产接入仍需单独实现与验证，包括并发 revision 占位、预算扣减、取消收尾和结果披露。本次纠正文档，不声称完成该能力的生产接入。

## 验证

| 检查 | 结果 |
|---|---|
| `ruff check app/ tests/ main.py manage.py --output-format concise` | 通过 |
| 两个 TypeScript 配置（`tsconfig.json`、`tsconfig.test.json`） | 通过；最后的测试夹具修改后再次检查测试配置 |
| `node node_modules/eslint/bin/eslint.js . --max-warnings 0` | 通过；最后修改的两个测试文件再次单独检查通过 |
| `python scripts/gen_config_schema.py --check` | 通过 |
| 锁文件节点删除范围、依赖图和 manifest 一致性 | 通过 |
| `git diff --check` | 通过 |
| 后端定向回归（下列 16 个测试文件） | **181 passed** |
| Vitest：命令处理、运行时面板、协同消息 | **3 文件、78 passed** |
| Vitest：修正后的抽屉与 Markdown 渲染测试 | **2 文件、11 passed** |
| Vitest 全量（`--maxWorkers=4`） | **296 文件、2825 用例通过；2 文件、3 用例失败**，失败文件修正后定向复验 **11/11 通过** |

后端回归命令（仓库根目录，关闭默认 coverage 以保留已有覆盖率数据）：

```powershell
python -m pytest tests/unit/test_chat_timeout_config.py tests/test_chat_engine.py tests/test_runtime_chaos_engine.py tests/unit/test_session_lock.py tests/unit/test_session_lock_resilience.py tests/test_session_locks_leak.py tests/test_sse_legacy_keepalive.py tests/test_config.py tests/unit/test_config_ssrf_validator.py tests/test_env_template_parity.py tests/unit/extensions_platform/test_cli.py tests/unit/extensions_platform/test_marketplace_distribution.py tests/unit/gis_harness/test_delegation_v7.py tests/test_618_backend_p3.py tests/test_correctness_audit_fixes.py tests/test_what_if_simulate.py -o addopts= -q
```

前端命令在 `frontend/` 目录执行，使用本地 Node 直接运行工具。初次全量运行暴露协同消息的固定等待问题；随后并行运行还暴露了抽屉交互及 Markdown 冷加载的测试前提问题。限定 4 workers 的全量任务在这些测试夹具修正前已执行两个失败文件，其最终汇总为 298 文件、2828 用例，耗时 1021.96 秒；修正后另行运行这两个文件，11/11 通过。其他 296 文件通过全量检查，命令处理／运行时面板／协同消息的 78 项定向回归也通过。测试夹具修正后没有再次运行完整前端套件，不将多次运行合称为一次全量全绿。没有为通过测试修改业务超时或点击保护阈值。

锁文件采用最小节点删除：已核对 manifest 与 importer 的依赖名称和版本约束一致，已确认保留的 snapshot 没有引用被移除的五个包。pnpm 包装器在离线锁文件命令中仍发起网络校验，故取消该命令；未重装依赖，前端验证复用本机已有依赖目录。本次不声称验证了全新安装、构建体积改善或安装耗时改善。

本次不是全仓逐行审计，也未执行真实浏览器 E2E、外部服务联调、部署或后端万级全套测试。
