"""E-4（#895）：.env.example 模板与 Settings 字段守恒契约。

config.py 新增字段而模板未同步的漂移（08-23 的 11 个 CARTO_* 阈值、
CORS_ORIGINS 等 30+ 键）让新配置对使用者不可见——CORS_ORIGINS 在生产
模式是 fail-fast 校验，缺文档等于升级必踩坑。本测试解析 Settings 字段
集合与模板键集合做差集，白名单外的缺失即红。
"""
import re

CONFIG = "app/core/config.py"
TEMPLATE = ".env.example"

# 非 env 覆盖面的常量字段 / compose 专用键 / 仅内部测试键
WHITELIST = {
    "API_V1_STR",       # 路由前缀常量，无环境覆盖需求
    "PROJECT_NAME",     # 展示名常量
}


def _settings_fields():
    src = open(CONFIG, encoding="utf-8").read()
    # 只取 Settings 类体内的 4 空格缩进字段（pydantic-settings 声明面）
    return set(re.findall(r"^    ([A-Z_0-9]+):", src, re.M))


def _template_keys():
    keys = set()
    for line in open(TEMPLATE, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_env_example_covers_all_settings_fields():
    missing = _settings_fields() - _template_keys() - WHITELIST
    assert not missing, (
        f"app/core/config.py 的 Settings 字段缺失于 .env.example（新增配置必须同步模板）："
        f"{sorted(missing)}"
    )


def test_redis_url_carries_password_placeholder():
    """E-5（#896）：dev compose 对 Redis 强制 requirepass，宿主机模板 URL 必须带密码。"""
    content = open(TEMPLATE, encoding="utf-8").read()
    m = re.search(r"^REDIS_URL=(\S+)$", content, re.M)
    assert m, "REDIS_URL 必须出现在 .env.example"
    assert re.search(r"://:[^@/]+@", m.group(1)), (
        f"REDIS_URL 示例必须带密码占位（redis://:<password>@host，compose requirepass 下宿主机进程会 NOAUTH）：{m.group(1)}"
    )
