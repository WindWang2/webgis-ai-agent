# extdemo-v3-pack — ADR-0119 完成证明示例

V3 生态全链路的示例扩展：**worker 隔离 + capability broker + 流式矢量
provider + 流式 model provider**。

## 能力

| 工具/provider | 说明 |
|---|---|
| `fetch_title` | 经 broker 出网（宿主侧 allowlist + SSRF gate 强制） |
| `compute_index` | 确定性 GIS 安全计算（形状指数演示） |
| `streams` data provider | `streaming_vector` mixin；内存合成网格点（确定性、零网络） |
| `synth` model provider | V3 流帧协议的合成事件流（非真实模型，诚实标注） |

## 完成证明链路（test_v3_completion_proof.py）

1. 示例密钥生成（Ed25519）+ trust store 注册；
2. `sign`（Ed25519）→ `publish` 到本地 registry；
3. `install`（digest/验签/revocation preflight + 安全解包 + 原子换装）；
4. 激活（**bubblewrap 可用时真沙箱**；否则 process 后端 + typed 降级）；
5. broker 消费（fetch_title 经宿主代理出网）；
6. 流式消费（streams provider 真流式 + synth provider 流帧）；
7. 升级 1.1.0（preflight + 原子换装 + host.upgrade）；
8. 回滚 1.0.0（versions/ + `allow_downgrade`）；
9. 吊销 1.0.0（trust store 写回）→ 该版本无法再安装/回滚。

## 签名（仅演示）

示例密钥**不入生产信任根**。生产发布请用运维生成的密钥对：
`python -m app.extensions_platform keygen --publisher <name> --key-id <id> --out-dir <dir>`
