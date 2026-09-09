"""ModelOps provider implementations（ADR-0119）。

全部经 :class:`app.services.modelops.providers.base.ProviderRegistry`
注册后使用；引擎只消费 registry 解析实例（R1-C1：无动态加载）。
"""
