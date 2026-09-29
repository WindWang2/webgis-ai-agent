# extdemo-ml-pack

GIS Extension Platform **V2** `model_provider` 示例包（ADR-0105）。

- 1 个确定性离线词频模型 provider（流式事件 + final 聚合）
- 演示 `credentials_ref` 供给即授权（`EXTENSION_SECRETS_JSON`）
- 投影为类型化调用工具 `extdemoml_wordfreq_invoke`

全部离线、无网络、确定性——同时是 `tests/unit/extensions_platform/`
的集成测试夹具与认证 harness 的演示对象。
