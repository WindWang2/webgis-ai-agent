# Pi vendor 升级 / 降级 Runbook（ADR-0301）

> 适用范围：`vendor/pi` 子模块的升级、降级、构建与供应链故障排查。
> 契约依据：`docs/PI_UPSTREAM_COMPAT_MATRIX.md`（C1–C24）、`docs/adr/0301-pi-v161-native-gis-host.md`。
> 纪律：不擅自升级——每个 pin 移动必须附矩阵裁决 + 契约车道证据。

## 0. 术语

| 术语 | 含义 | 判定位置 |
|---|---|---|
| **pin** | 超项目 gitlink 记录的 vendor/pi commit（当前 `ef4a1cfc` = tag v1.6.1） | `git ls-tree HEAD vendor/pi`（type 必须是 `commit`，mode 必须 `160000`） |
| **floor** | 本仓桥协议可接受的最低上游版本（`PI_MIN_VENDOR_TAG=v1.5.1`） | `app/services/chat/pi_compat.py`（pin/floor 解耦，floor 不盲随 pin） |
| **manifest** | `.build-manifest.json`（构建事实：pin/node/lock/entry 指纹），子模块工作树内、不入超项目 git | `scripts/build-vendor-pi.sh` / Dockerfile pi-builder |
| **能力快照** | typed feature→supported 判定（14 项封闭词表），按 feature 不按版本号分支 | `pi_compat.pi_capability_snapshot()` |

## 1. 升级（pin X → Y）

```bash
# 0) 前置核查（不可跳过）
gh api repos/earendil-works/pi/releases/latest --jq '{tag:.tag_name,published:.published_at}'
git ls-remote https://github.com/earendil-works/pi.git "vX.Y.Z" "vX.Y.Z^{}"   # lightweight tag → commit SHA
# 若 tag 是 annotated，取 ^{} 行的 commit SHA；禁止用 stable/main 分支头冒充 tag SHA。

# 1) 记录新 pin（gitlink），保持 mode 160000
git -C vendor/pi fetch --tags origin
git -C vendor/pi checkout vX.Y.Z
git add vendor/pi && git status   # 应显示 mode 160000 的 SHA 变更，无 120000

# 2) 重建 + 清单
bash scripts/build-vendor-pi.sh --reinstall       # npm ci（root lockfile 可复现）+ build + manifest

# 3) 本地门禁（全部绿才算候选）
python scripts/pi_doctor.py                        # 六域全 PASS；vendor 域含版本探针/manifest/能力快照
python -m pytest tests/integration/test_pi_vendor_contract.py \
                  tests/integration/test_pi_schema_coercion_golden.py
PI_JOURNEY_E2E=1 python -m pytest tests/integration/test_pi_stub_llm_journeys.py

# 4) 协议文档 diff（C1–C16 漂移判定）
git -C vendor/pi diff <old-tag> <new-tag> -- packages/coding-agent/docs/json.md \
    packages/coding-agent/docs/rpc.md packages/coding-agent/docs/rpc-extension-ui.md
# → 逐字节一致 = 消费面零漂移；有 diff 则逐条对照矩阵 §3 清单 5 复核。

# 5) 更新 docs/PI_UPSTREAM_COMPAT_MATRIX.md（差异裁决四栏 + 新 C 项）+ ADR +
#    PI_MIN_VENDOR_TAG（仅当确需提高下限；floor 移动需要 ADR）+ 本 runbook 的 pin 行。
```

## 2. 降级 / 回退

```bash
# pin 回退（例：v1.6.1 → v1.5.2 b4afdddf）
git -C vendor/pi checkout b4afdddf...
git add vendor/pi
bash scripts/build-vendor-pi.sh --reinstall
python scripts/pi_doctor.py                        # floor 仍 v1.5.1 → v1.5.2 满足，应全绿
python -m pytest tests/integration/test_pi_vendor_contract.py
```

- floor **不需要**跟着回退（floor 只声明「低于 v1.5.1 不支持」；v1.5.2 满足）。
- 只有回退到 **v1.5.0 及以下** 才必须同步回退 floor + 矩阵（历史先例见矩阵 §4 回退段）。
- ADR-0301 的新增行为全部加性或有 kill-switch：能力快照/manifest/诊断码是纯加性观测；
  `PI_NATIVE_DISCOVERY=0`、`PI_SURFACE_SCOPE_ENFORCE=0` 沿用既有开关。

## 3. 供应链故障排查（symptom → 处置）

| 症状（doctor/identity 词表） | 根因 | 处置 |
|---|---|---|
| `vendor/pi tree entry is mode 120000, not a gitlink`（vendor 域 FAIL） | #2169 回归类：merge 冲突把本机 symlink 提交进库；fresh clone 全链失效 | `git rm --cached vendor/pi && git update-index --add --cacheinfo 160000,<pin>,vendor/pi && git submodule update --init vendor/pi`（历史先例 ADR-0301） |
| `build manifest disagreement (pin_mismatch)`（FAIL） | 清单 pin ≠ 子模块 checkout（checkout 漂移/镜像串版） | `bash scripts/build-vendor-pi.sh --reinstall` |
| `entry_drift`（WARN，不 FAIL） | 上游 esbuild 产物非字节级可复现（chunk hash 随构建变化）或入口被替换 | rebuild 刷新清单即可；若频繁出现且伴随行为异常再查篡改 |
| `manifest absent`（WARN，git 在场时） | 旧构建（v1.6.1 前脚本）/ 未走脚本构建 | rebuild |
| `manifest_only`（PASS，无 git 环境） | 镜像内无 .git，清单是唯一 pin 证据 | 核对部署流水线传了 `PI_VENDOR_PIN`（production.yml 自动传 gitlink SHA） |
| `version (unknown)` | node 缺失/入口未构建/探针超时 | 看 build 域；修复后能力快照自动恢复 14/14 |
| capabilities `unsupported` 非空 | 版本低于 feature 引入点（如 v1.5.2 无 v1.6.0 组） | 消费方按 feature 降级（显式 unavailable，不伪称） |

## 4. 开发 override（仅本地、opt-in）

```bash
# 把 vendor 指向另一 checkout（调试上游 PR/本地实验）。生产镜像与 CI 不设置。
PI_VENDOR_DIR=/path/to/pi-checkout python scripts/pi_doctor.py
# identity/doctor 会把 dir_source 标为 env_override；漂移照常 WARN；非法路径
# 自动忽略并回退 canonical vendor/pi（WARN 日志）。
```

## 5. 清洁环境复现（clean-clone 验收）

```bash
git clone https://github.com/WindWang2/webgis-ai-agent.git /tmp/wt-clean && cd /tmp/wt-clean
git checkout <本分支>
git submodule update --init vendor/pi            # github 拉取，pin 即 gitlink SHA
bash scripts/build-vendor-pi.sh                  # npm ci + build + manifest
python scripts/pi_doctor.py                      # 期望：六域 PASS，version=1.6.1(entry_probe)
```

任一步失败 → 按第 3 节词表处置；不要跳过 doctor 直接接流量。
