# Pi bridge vs ChatEngine 同任务集基准（P6 · WAYFINDER 票 2）

> 线：foundation/quality-e2e-v9 · ADR-0146 · Harness：`scripts/perf/pi_vs_chatengine.py`

## 设计

同一任务集经 HTTP 驱动两个后端，差值隔离**编排层**：

| 臂 | 启动 | 被测层 |
|---|---|---|
| ChatEngine | `USE_NEW_AGENT=false` | engine / tool pipeline / SSE 组装 |
| Pi bridge | `USE_NEW_AGENT=true` | RPC bridge / turn 机制 / pool 亲和 / SSE 组装 |

两臂共享**同一** LLM provider（`LLM_BASE_URL` + `LLM_API_KEY` 相同）——模型
推理项恒定，不参与对比。vendor/pi 为 git submodule（CI build job 以
`submodules: recursive` 检出；本地跑 Pi 臂前必须 `git submodule update --init`）。

## 任务集（确定性意图 × 3 轮）

| 任务 | 期望 |
|---|---|
| `simple-qa` | 纯文本问答（不期望工具调用） |
| `buffer-analysis` | 缓冲区分析（期望工具调用） |
| `hotspot-analysis` | ref 输入热点分析（期望工具调用） |

指标：首 token 中位（用户感知 TTFT）、总时长中位、tool_call 命中率、完成率。

## 复跑（有真实 key 的环境）

```bash
git submodule update --init vendor/pi
USE_NEW_AGENT=false LLM_API_KEY=<key> uvicorn app.main:app --port 8001 &
python scripts/perf/pi_vs_chatengine.py --base http://localhost:8001 --collect chat
USE_NEW_AGENT=true  LLM_API_KEY=<key> uvicorn app.main:app --port 8002 &
python scripts/perf/pi_vs_chatengine.py --base http://localhost:8002 --collect pi
python scripts/perf/pi_vs_chatengine.py --compose --write   # 回填本文件表格
```

接线自检（无需 key）：`python scripts/perf/pi_vs_chatengine.py --check --base http://localhost:8001`

## 测量结果

**本轮（quality-e2e-v9 交付环境）无真实 key、vendor/pi submodule 未检出，
未产出数值 —— 不虚构数字。** 表格由 `--compose --write` 在有 key 环境回填；
CI 侧经 `quality-e2e.yml` 的 `workflow_dispatch`（pi-real-e2e 输入 + secrets）
可在 nightly 产出并落库。

| 任务 | 臂 | 首 token 中位 (ms) | 总时长中位 (ms) | tool_call 命中率 | 完成率 |
|---|---|---|---|---|---|
| （待有 key 环境回填） | | | | | |

## 关联

- 真实 LLM E2E 三条冒烟旅程：`tests/integration/test_pi_real_llm_e2e.py`
  （`heavy` + `PI_REAL_E2E=1` + 真实 key 门控；无 key 显式 skip）
- WAYFINDER_MAP.md 两张开放票随本文件与上述 E2E 勾销
