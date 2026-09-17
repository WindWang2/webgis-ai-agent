# ExtDemo Certified Pack（ADR-0199 示例）

分级能力认证（capability certification v2）的最小完整样例：
**manifest 声明 → schema → 实现 → 测试 → 运行时探针 → 生命周期（无孤儿投影）**
六阶段全部可对样例执行。

## 内容

- `manifest.json`：`api_version: 1.3.0`，声明 1 个工具 + 1 个算法 +
  `certification` 节（每个要认证的能力一个探针：`args` / `replay` /
  `expect_key` / `expect_value` / `tolerance`）。
- `main.py`：SDK spec（`ToolExtensionSpec` / `AlgorithmExtensionSpec`，
  算法带数值 smoke cases）+ `activate(ctx)`。

## 认证与激活 gate 演练

```bash
# 分级认证（会真实激活 → 探针 → 停用还原）
EXTENSIONS_DIRS=extensions/examples \
  python -m app.extensions_platform certify extdemo.certified --staged

# 持久化报告（包内 .certification.json；指纹绑定；报告不入指纹）
EXTENSIONS_DIRS=extensions/examples \
  python -m app.extensions_platform certify extdemo.certified --staged --save

# 开启激活 gate（未认证 pack 将被拒绝激活）
EXTENSIONS_REQUIRE_CERTIFIED=true EXTENSIONS_DIRS=extensions/examples \
  python -m app.extensions_platform doctor
```

生产建议 `EXTENSIONS_CERTIFICATION_TRUST=strict` 并配置
`EXTENSIONS_CERTIFICATION_KEY`（HMAC 密钥文件；`--sign-key` 落地签名报告）——
evidence 模式（默认）接受未签名报告，**不防篡改**，仅用于本地开发。

## 与测试的关系

`tests/unit/extensions_platform/` 中的认证管线/gate/catalog 测试用程序化
工厂生成等价 pack；本目录的样例用于人工演练与文档。改样例前先读
`docs/extension-platform/capability-certification.md`。
