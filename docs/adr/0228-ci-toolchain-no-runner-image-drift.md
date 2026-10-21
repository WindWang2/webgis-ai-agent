# ADR-0228: CI 工具链不得依赖 runner 镜像预装版本（#1596 方向 1 留档）

- 状态：Accepted
- 日期：2026-10-21
- 方向：CI 确定性 —— #1596（Backend Tests 环境损坏）方向 1 的根因留档
- 基线：master `0415ad8d`
- 前置：ADR-0118（确定性/无时间戳纪律）、#1599（pnpm v11 顶掉 version pin 的修复）、TC-03/TC-04/TC-05/TC-10/TC-12（#1589 的安装面/隔离/并行化修复簇）

## 背景与问题

#1596 记录的 224 failed + 8 errors 里，最不可归因的一簇是「CI 执行代码 ≠ 仓库代码」：

1. kriging oracle 在 CI 报错误码字符串 `KRIGING_INPUT_ERROR`，而仓库侧 `app/lib/geo_analysis/kriging.py` 是 `raise KrigingInputError(...)` —— 同一提交、两种行为，只能归因于 runner 上存在陈旧安装遮蔽仓库代码；
2. 10-07 实锤的第一个受害点是**被 pin 住的** pnpm：runner 镜像 `ubuntu-24.04 20260927.320.1` 自带 pnpm v11，`pnpm/action-setup` 的 `version: 10` 被镜像 `PNPM_HOME` 的 v11 shim 顶掉，`pnpm add --no-save` 被 v11 拒绝 → nightly Playwright Runtime Validator 47s 即死（#1599）。

共同形态：**workflow 声明的工具版本被 runner 镜像的预装版本静默遮蔽**。GitHub-hosted runner 的基础镜像不受仓库控制、其预装面随时间漂移；仓库侧无法钉镜像，唯一可控的是「不让正确性依赖它」。本 ADR 把该教训定为纪律，并把「镜像漂移只可缓解」的判断留档。

## 决策

### C1 纪律：CI 工具链一律显式安装，不继承镜像预装版本

每一步使用的工具链版本由 workflow 自己装（`.github/workflows/production.yml` 内的
`pip install ruff==0.16.8` / `npm install -g pnpm@10` / `npm install -g tsx@…`），
或由带 SHA pin 的官方 action 提供。镜像预装只作为「没有显式安装」时的兜底，
不得进入正确性推理；`version:` pin 必须落在实际生效的解析路径上（pnpm 的教训：
pin 写在 action 入参、被镜像 shim 顶掉 = 没 pin）。

### C2 安装面确定性：Python 依赖一律 lock 约束

- 所有 `pip install -r requirements*.txt` 带 `-c requirements.lock`，由
  `tests/test_ci_locked_install_contract.py` 逐行断言（漏带的安装命令即红）；
- `scripts/check_dependency_bounds.py` 常驻 lint job，把 pandas<3.0 /
  sqlalchemy<2.1 等安全上界钉成门禁（#1512 回归形态：解析器选中 3.x 即红）。

### C3 缓解而非根除：runner 镜像漂移只可缓解

镜像漂移仓库侧不可修，可做三件事：(a) 显式安装 + pin；(b) 与镜像版本解耦的写法
（tsx 走 `npm install -g` 而非 `pnpm add --no-save`，TC-12）；(c) 安装后版本断言 /
防遮蔽探针作为可选加强。

「site-packages 陈旧 `app` 包遮蔽仓库代码」子项按此降级为缓解 + 本 ADR 留档：
仓库当前无任何 lane `pip install -e .`（即无仓库侧产生陈旧 app 包的机制），
因此不额外加探针；**若未来引入自安装，必须同时加 import 路径断言**
（`app.__file__` 落在工作树内），否则本 ADR 的 C1 纪律被自己违反。

### C4 超时阈值按实测 P95 校准，且必须与 build cache 成对

只放阈值会把「挂起」误判成「慢」。因此：

- job 预算与 `next build` 步骤级超时按实测放宽（Backend 110m / Frontend 75m /
  build 步骤 50m，实测单步 41m43s）；
- `frontend/.next/cache` 进 GHA cache（exact key = lockfile+package.json hash +
  commit sha 才写回，前缀命中只恢复不写），压缩 build 时长方差 —— 见
  `.github/workflows/production.yml` 的 `Cache Next.js build cache` 步骤。

### C5 测试环境隔离是环境修复的一部分

Backend lane 不再导出 `DATABASE_URL`，`tests/conftest.py` 为每个 xdist worker
独享临时 SQLite；真实 Postgres/Redis 覆盖归 db-migrations 与 real-services
lane。#1596 的 Postgres 服务认证失败（`admin` 用户）即「共享 service 容器 +
顺序依赖」形态，隔离后该类签名不复现。

## 后果

- 新增 workflow 步骤若引入工具，评审必须回答「版本从哪来」；答「镜像自带」即打回；
- runner 镜像升级可能仍打破未显式安装的工具 —— 届时按 #1599 的模式修（显式装 +
  与镜像解耦），不追加重试型 workaround；
- 本 ADR 不覆盖前端依赖漂移（node_modules 版本）—— 那属 #1597 的动态导入/模块
  解析域，签名独立。
