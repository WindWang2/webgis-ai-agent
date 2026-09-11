# 主 Agent Phase A Findings（自有审查；与 A/B/C/D agents 的 findings 合并）

### [M-1] P2 integration/correctness — SSRF 启动校验的 fake-IP 豁免清单缺 overpass 域名，测试套件在 fake-IP DNS 开发机全量失败
- Files: app/core/config.py:513
- Category: integration / correctness
- Current behavior: `_validate_no_ssrf` 对解析到 private/reserved IP 的域名直接
  ValueError 拒绝启动，唯一豁免是硬编码 5 主机清单
  {nominatim.openstreetmap.org, tile.openstreetmap.org, data.beijing.gov.cn,
  data.sh.gov.cn, gddata.gd.gov.cn}。tests/conftest.py 基线钉
  OVERPASS_API_URL=https://overpass-api.de/api/interpreter；Settings 默认
  https://overpass.openstreetmap.fr/api/interpreter。两者都不在豁免清单。
  在 fake-IP DNS（VPN，所有域名→198.18.0.0/15 保留段）开发机上，
  socket.gaierror 逃生口永不触发，Settings 构造失败 → 所有 import app 的
  测试 ERROR（已实测：tests/unit/test_env_hygiene.py setup ValidationError）。
- Expected behavior: conftest 基线值与 Settings 默认值都应能在该清单下启动
  （nominatim 已在清单内，说明该清单本就是为了此类环境）。
- Root cause: 豁免清单加 nominatim/tile 时遗漏 overpass 两域。
- Reproduction/proof: 本机 getaddrinfo('overpass-api.de')=198.18.0.85（保留段）；
  pytest tests/unit/test_env_hygiene.py → setup ERROR ValidationError。
- Impact: 本地验证环境全阻（所有 lane）；仅 fake-IP DNS 机器受影响，生产不受影响。
- Recommended fix: 清单加入 "overpass-api.de"、"overpass.openstreetmap.fr"（跟随现有先例）。
- Regression test: tests/unit/ 已有 test_env_hygiene 的 import 钉扎；可加一条
  单测直接构造 Settings（monkeypatch DNS 返回保留 IP 时 overpass 域放行）。
- Cross-module implications: 无行为变化（仅启动期校验）。

### [M-2] P2 integration — master 上 16 个生成物 stale（preflight RED）
- Files: docs/integration/RELEASE_READINESS.*, frontend-behavior.json,
  docs/quality/*（CONTRACT_DRIFT_REPORT、QUALITY_MANIFEST/REPORT、7 certifications、
  contract-drift-report.json、quality-manifest.json、quality-report.json）、
  docs/science/BENCHMARK_MANIFEST.md、tests/quality/snapshots/realtime-contract.json
- Category: integration / documentation-contract
- Current behavior: check_integration_preflight FAIL（generated_staleness RED，16 项）。
  最近提交 2aabdc43 试图刷新 drift report 指纹但未覆盖全部生成物。
- Expected behavior: preflight 全绿（生成物与输入指纹一致）。
- Root cause: 输入变化后未跑完整再生成（各 gen_*.py）。
- Impact: 合并 gate 红；后续分支 preflight 噪声。
- Recommended fix: 按既有 generator 逐个再生成并提交（Phase B 统一处理，
  在 V7/V8 分支合并后再生成一次，避免重复劳动）。
- Regression test: preflight 本身。
- Cross-module implications: 无。

### [M-3] P3 documentation-contract — ADR watermark 以下存在 10 组重复编号（0104×5、0118×7 等）
- Files: docs/adr/（0077×2、0088×3、0094×2、0096×2、0099×2、0101×4、0103×3、0104×5、0105×2、0118×7）
- Current behavior: watermark 机制（ownership.json adr_watermark=0129）只防新撞号；
  历史重复编号被 preflight 容忍。"ADR-0104" 等引用存在歧义（5 个候选文件）。
- Expected behavior: 引用无歧义（或文档声明编号仅是文件名前缀，引用以 slug 为准）。
- Impact: 低（文档检索歧义）。
- Recommended fix: P3——在 docs/adr/README 或 integration 文档声明编号歧义以 slug
  消歧；或一次性把重复 ADR 重编号并更新引用（工作量大，收益低，建议声明式修复）。
- Regression test: allocate_adr.py 已防新增。
- Cross-module implications: 无。

### [M-4] P2 correctness/platform — 顶层裸 `import fcntl`（rag/faiss_store.py、core/bridge_secret.py）使 `import app.main` 在 Windows 上失败
- Files: app/services/rag/faiss_store.py:5；app/core/bridge_secret.py:10
- Category: correctness（平台兼容）
- Current behavior: 两处模块顶层 `import fcntl`（POSIX-only）。Windows 上
  `import app.main` 直接 ModuleNotFoundError（实测 tests/unit/test_env_hygiene.py
  ::test_import_app_main_does_not_mutate_os_environ FAILED）。另有
  explorer_tools / spatial_decision_tools 注册失败（loader 捕获日志，表面降级）。
  仓库既有 Windows 兼容先例：trace_store.py:53-59 try/except 降级 + conftest
  fcntl 警告——但这两处漏守卫。
- Expected behavior: Windows dev 机 import app.main 成功（锁降级为进程内锁，
  诚实披露），与 trace_store 同模式。
- Root cause: 裸 import 无平台守卫。
- Reproduction/proof: 本机 pytest env_hygiene 输出（faiss_store.py:5 →
  ModuleNotFoundError: No module named 'fcntl'）。
- Impact: Windows 开发/测试环境全阻（无法起服务、大量测试失败）；Linux 生产不受影响。
- Recommended fix: 两处改为 try/except ImportError + msvcrt 或进程内 threading.Lock
  降级（跟随 trace_store 模式）；explorer_tools/spatial_decision_tools 的 fcntl
  同步处理。
- Regression test: test_import_app_main_does_not_mutate_os_environ 在 Windows 上
  转绿即回归证据。
- Cross-module implications: 无行为变化（POSIX 路径保持 fcntl）。

### [M-5] P3 platform — explorer_tools / spatial_decision_tools 在 Windows 上注册失败（工具面 309→307）
- Files: app/tools/explorer_tools.py、app/tools/spatial_decision_tools.py（fcntl 依赖）
- Current behavior: ToolInit loader 捕获并日志（"[ToolInit] Failed to load ...
  No module named 'fcntl'"），表面静默降级 2 个工具。
- Expected behavior: 平台守卫后 Windows 可注册；或 loader 警告含补救指引。
- Recommended fix: 随 M-4 一并处理（fcntl 守卫下沉到共同 util）。
- Impact: 仅 Windows dev。

## 环境基线（后续 master baseline comparison 依据）

- 本机 fake-IP DNS：所有外部域名→198.18.0.0/15；DNS 失败逃生口永不触发。
- 修复 M-1 前任何 import app 的测试在本机均 ERROR —— 该集合记为环境基线，
  与分支后状态对比判定（预期修复 M-1 后恢复绿）。
