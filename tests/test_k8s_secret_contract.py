"""Issue #561: k8s 所有 secretKeyRef 引用的 key 必须都在文档化 secret 契约里。

修复前 deploy/k8s/05-deps-optional.yaml 引用 DB_USER / DB_PASSWORD / DB_NAME /
REDIS_PASSWORD，而文档化的 secret 创建路径（01-configmap.yaml 的 kubectl 配方、
secret.example.yaml 的 stringData、docs/DEPLOYMENT.md）只定义 URL 型键
（DATABASE_URL / REDIS_URL / ...）—— 交集为空。启用可选的内部 postgres/redis
即 CreateContainerConfigError: secret "webgis-secret" not found key DB_USER。

应用消费的形状（app/core/config.py 直接读 DATABASE_URL / REDIS_URL 完整 URL）
保持为主契约；组件键是 05 里 postgres/redis 镜像初始化所需的补充键，现已加入
契约文档。本文件守住：任何 secretKeyRef.key 都必须在 secret.example.yaml 的
stringData 里声明（模板即契约），且 docs/DEPLOYMENT.md 的 kubectl 配方也覆盖。
"""
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = REPO_ROOT / "deploy" / "k8s"


def _template_declared_keys() -> set:
    """secret.example.yaml stringData 声明的键 = 权威契约键集。"""
    docs = [
        d
        for d in yaml.safe_load_all(
            (K8S_DIR / "secret.example.yaml").read_text(encoding="utf-8")
        )
        if d
    ]
    for doc in docs:
        if doc.get("kind") == "Secret":
            return set(doc.get("stringData", {}).keys())
    raise AssertionError("secret.example.yaml 无 Secret 资源")


def _recipe_keys(text: str) -> set:
    return set(re.findall(r"--from-literal=([A-Z0-9_]+)=", text))


def _referenced_secret_key_refs() -> set:
    """deploy/k8s/ 下所有 secretKeyRef.key（各资源 env 内）。"""
    keys = set()
    for path in sorted(K8S_DIR.glob("*.yaml")):
        docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]
        for doc in docs:
            if doc.get("kind") not in (
                "Deployment",
                "StatefulSet",
                "DaemonSet",
                "Job",
                "CronJob",
            ):
                continue
            spec = doc.get("spec", {}).get("template", {}).get("spec", {})
            containers = spec.get("containers", []) + spec.get("initContainers", [])
            for container in containers:
                for env in container.get("env", []):
                    ref = env.get("valueFrom", {}).get("secretKeyRef")
                    if ref:
                        keys.add(ref["key"])
    return keys


def test_every_secretkeyref_key_is_declared_in_template():
    declared = _template_declared_keys()
    referenced = _referenced_secret_key_refs()
    assert referenced, "deploy/k8s/ 竟然没有 secretKeyRef？"
    undeclared = referenced - declared
    assert not undeclared, (
        f"secretKeyRef 引用了模板未声明的键 {sorted(undeclared)} —— "
        "启用 05-deps-optional.yaml 会 CreateContainerConfigError"
    )


def test_optional_deps_component_keys_are_documented():
    """#561 修复点：05 引用的四个组件键必须出现在权威契约（模板 stringData）。"""
    declared = _template_declared_keys()
    for key in ("DB_USER", "DB_PASSWORD", "DB_NAME", "REDIS_PASSWORD"):
        assert key in declared, (
            f"05-deps-optional.yaml 引用的 {key} 未在 secret.example.yaml 声明"
        )


def test_configmap_recipe_covers_all_referenced_keys():
    """01-configmap.yaml 的 kubectl 配方必须覆盖全部被引用键（部署者照抄配方
    即可，不会缺键）。"""
    recipe = _recipe_keys((K8S_DIR / "01-configmap.yaml").read_text(encoding="utf-8"))
    referenced = _referenced_secret_key_refs()
    missing = referenced - recipe
    assert not missing, (
        f"01-configmap.yaml kubectl 配方缺 {sorted(missing)} —— 照配方建 secret "
        "会缺键"
    )


def test_deployment_docs_recipe_covers_all_referenced_keys():
    """docs/DEPLOYMENT.md 的 k8s secret 配方同样必须覆盖全部被引用键。"""
    doc_text = (REPO_ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    recipe = _recipe_keys(doc_text)
    referenced = _referenced_secret_key_refs()
    missing = referenced - recipe
    assert not missing, (
        f"docs/DEPLOYMENT.md kubectl 配方缺 {sorted(missing)}"
    )


def test_app_consumes_url_shaped_keys():
    """形状契约：应用消费 URL 型键（DATABASE_URL/REDIS_URL 完整 URL），
    组件键仅供可选内部依赖容器初始化 —— URL 键必须保持文档中的主契约地位。"""
    config = (REPO_ROOT / "app" / "core" / "config.py").read_text(encoding="utf-8")
    assert "DATABASE_URL: str" in config
    assert "REDIS_URL: str" in config
    # 应用不得消费组件键（若开始消费，契约文档需同步调整）
    assert "DB_USER" not in config and "REDIS_PASSWORD" not in config
