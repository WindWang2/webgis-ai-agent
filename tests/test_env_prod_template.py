"""Deploy: .env.prod.example 模板必须覆盖 docker-compose.prod.yml 的全部
:? 强制键，且示例值指向本栈自带服务（#617）。

#500 后 compose 演进加入了 :? 强制键（DB_PWD / REDIS_PASSWORD / GRAFANA_PWD），
模板未同步 → 按模板复制填写的部署在 `up` 第一步就因 "required variable
DB_PWD is missing a value" 拒绝启动；且模板曾把注释键名写成 DB_PASSWORD
（与 compose 要求的 DB_PWD 不符）、DATABASE_URL 指向栈外 db-prod-host。
"""
import re

ENV_TEMPLATE = ".env.prod.example"
COMPOSE_PROD = "docker-compose.prod.yml"

# compose 内 `${KEY:?...}` 形式的强制键
_REQ_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+):\?")


def _template_keys():
    with open(ENV_TEMPLATE, encoding="utf-8") as f:
        content = f.read()
    keys = set()
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def _compose_required_vars():
    with open(COMPOSE_PROD, encoding="utf-8") as f:
        content = f.read()
    return set(_REQ_VAR_PATTERN.findall(content))


def test_template_covers_all_compose_required_vars():
    """compose 的每个 ${KEY:?} 强制键都必须在 .env.prod.example 中出现。"""
    required = _compose_required_vars()
    assert required, "未在 docker-compose.prod.yml 中找到 :? 强制键（测试假设失效）"
    missing = required - _template_keys()
    assert not missing, (
        f"docker-compose.prod.yml 要求以下键但 .env.prod.example 缺失: "
        f"{sorted(missing)} —— 按模板复制填写必然 'required variable ... not set'"
    )


def test_required_keys_have_nonempty_placeholders():
    """三个强制键不能为空（允许 CHANGE_ME 等占位符，用户复制后填写）。"""
    with open(ENV_TEMPLATE, encoding="utf-8") as f:
        content = f.read()
    for key in ("DB_PWD", "REDIS_PASSWORD", "GRAFANA_PWD"):
        m = re.search(rf"^{key}=(.+)$", content, re.MULTILINE)
        assert m, f"{ENV_TEMPLATE} 缺失强制键 {key}"
        assert m.group(1).strip(), f"{key} 为空值"
        assert "#" not in m.group(1), f"{key} 整行被注释掉: {m.group(1)}"


def test_no_wrong_key_name_db_password():
    """模板不得再出现与 compose 键名不符的 DB_PASSWORD 键（#617 键名漂移）。"""
    with open(ENV_TEMPLATE, encoding="utf-8") as f:
        content = f.read()
    for line in content.splitlines():
        if line.lstrip().startswith("DB_PASSWORD="):
            raise AssertionError(
                "模板含 DB_PASSWORD 键，compose 要求的是 DB_PWD —— 用户按提示"
                "补键会补错名字，部署仍失败"
            )


def test_database_url_points_to_in_stack_db():
    """模板 DATABASE_URL 必须指向本栈 db 服务（db:5432），而非栈外主机。"""
    with open(ENV_TEMPLATE, encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"^DATABASE_URL=(.+)$", content, re.MULTILINE)
    assert m, "模板缺失 DATABASE_URL"
    url = m.group(1).strip()
    assert "db-prod-host" not in url, f"DATABASE_URL 仍指向栈外主机: {url}"
    # 与 compose db 服务默认用户/库名（DB_USER/DB_NAME 的默认值 webgis_prod）一致
    assert url.startswith("postgresql://webgis_prod:"), f"DATABASE_URL 用户与 db 服务默认值不一致: {url}"
    assert "@db:5432/webgis_prod" in url, f"DATABASE_URL 未指向本栈 db 服务: {url}"