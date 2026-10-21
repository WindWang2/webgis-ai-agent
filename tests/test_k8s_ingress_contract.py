"""Issue #1688: k8s Ingress 的 TLS 链路契约 —— host 非占位 + issuer 清单存在。

修复前 ``deploy/k8s/04-ingress.yaml``：
  * L33 ``cert-manager.io/cluster-issuer: "letsencrypt-prod"`` 在仓库内没有
    对应清单（全仓 grep ClusterIssuer/Issuer/cert-manager 只命中这一行）——
    cert-manager 发 ``IssuerNotFound`` event，``spec.tls.secretName:
    webgis-tls`` 永远不会被创建；
  * L37/L40 host 是占位符 ``webgis.your-domain.com`` —— Let's Encrypt 的
    HTTP-01 challenge 对该域名必然失败；
  * L8 ``ssl-redirect: "true"`` —— 所有 HTTP 请求 308 到一个没有证书的 HTTPS
    端点（HTTPS 永不就绪）。

本文件守住修复后的形状：
  1. Ingress 注解引用的 issuer 在仓库内有对应 ClusterIssuer/Issuer 清单；
  2. host 段不是占位符（且是可签发证书的合法 FQDN）；
  3. host 可外部覆盖：单一事实来源是 01-configmap.yaml 的 INGRESS_HOST，
     kustomization.yaml 的 replacements 在 build 时注入（清单字面值不会进
     apply 产物）；
  4. issuer 清单本身 cluster-scoped（不带 namespace）且**不在** kustomize
     resources 里 —— kustomize 的 namespace 转换器会给资源树里每个资源注入
     metadata.namespace，apiserver 会以 "not allowed when resource is not
     namespaced" 拒绝整个 apply；
  5. TLS 不被"关掉 ssl-redirect"式地规避，webgis-tls 也不被手写成静态 Secret
     （证书由 cert-manager 签发并维护）。
"""
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = REPO_ROOT / "deploy" / "k8s"

INGRESS_FILE = K8S_DIR / "04-ingress.yaml"
KUSTOMIZATION_FILE = K8S_DIR / "kustomization.yaml"
CONFIGMAP_FILE = K8S_DIR / "01-configmap.yaml"

ISSUER_ANNOTATION = "cert-manager.io/cluster-issuer"

# host 段出现即说明证书不可能签发：历史占位符 / 保留示例域名 / 环回地址
_PLACEHOLDER_MARKERS = (
    "your-domain",
    "your_domain",
    "example.com",
    "example.org",
    "example.net",
    "changeme",
    "change-me",
    "replace-me",
    "todo",
    "placeholder",
    "localhost",
    "127.0.0.1",
)
# k8s spec.rules[].host / spec.tls[].hosts 的合法形状：DNS subdomain
_FQDN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
)


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_all(path: Path):
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _ingress() -> dict:
    docs = [d for d in _load_all(INGRESS_FILE) if d.get("kind") == "Ingress"]
    assert len(docs) == 1, f"{INGRESS_FILE.name} 应只有一个 Ingress 资源"
    return docs[0]


def _issuer_manifests():
    """仓库内所有 ClusterIssuer/Issuer 清单（(文件名, 文档) 对）。"""
    out = []
    for path in sorted(K8S_DIR.glob("*.yaml")):
        for doc in _load_all(path):
            if doc.get("kind") in ("ClusterIssuer", "Issuer"):
                out.append((path.name, doc))
    return out


def _assert_host_not_placeholder(host: str) -> None:
    assert host, "host 不得为空"
    lowered = host.lower()
    bad = [m for m in _PLACEHOLDER_MARKERS if m in lowered]
    assert not bad, (
        f"ingress host {host!r} 仍是占位符（命中 {bad}）—— TLS 证书无法签发，"
        "HTTPS 永不就绪。改 deploy/k8s/01-configmap.yaml 的 INGRESS_HOST"
    )
    assert _FQDN_RE.match(lowered), (
        f"ingress host {host!r} 不是合法 DNS subdomain —— apiserver 会拒绝该 "
        "Ingress"
    )
    assert len(host.split(".")) >= 2, f"ingress host {host!r} 不是 FQDN"


def test_ingress_annotation_issuer_has_manifest_in_repo():
    """#1688 主修复点：注解引用的 issuer 必须在仓库内有对应清单。"""
    ing = _ingress()
    issuer = ing["metadata"]["annotations"][ISSUER_ANNOTATION]
    matches = [(name, doc) for name, doc in _issuer_manifests()
               if doc["metadata"]["name"] == issuer]
    assert matches, (
        f"issuer {issuer!r}（04-ingress.yaml 的 {ISSUER_ANNOTATION} 注解）在仓库内"
        "无对应清单 —— cert-manager 会发 IssuerNotFound event，webgis-tls 永不"
        "被创建。请新增 deploy/k8s/04b-clusterissuer.yaml 并单独 apply"
    )


def test_ingress_hosts_are_not_placeholders():
    """rules[].host 与 tls[].hosts 都不得是占位符，且必须是合法 FQDN。"""
    ing = _ingress()
    hosts = [r["host"] for r in ing["spec"]["rules"]]
    assert hosts, "ingress 没有 rule host？"
    for tls in ing["spec"].get("tls", []):
        hosts.extend(tls.get("hosts", []))
    assert hosts, "ingress 既没有 rule host 也没有 tls hosts？"
    for host in hosts:
        _assert_host_not_placeholder(host)


def test_ingress_tls_hosts_cover_rule_hosts():
    """证书覆盖的域名必须等于实际服务的域名（否则签了证书也不生效）。"""
    ing = _ingress()
    rule_hosts = {r["host"] for r in ing["spec"]["rules"]}
    tls_hosts = {h for tls in ing["spec"].get("tls", [])
                 for h in tls.get("hosts", [])}
    assert tls_hosts == rule_hosts, (
        f"tls hosts {sorted(tls_hosts)} 与 rule hosts {sorted(rule_hosts)} 不一致"
    )


def test_ingress_host_is_overridable_from_configmap():
    """#1688：host 不再写死在 Ingress 里 —— 单一事实来源是 ConfigMap 的
    INGRESS_HOST，由 kustomize replacements 在 build 时注入两处 host 字段。"""
    kust = _load(KUSTOMIZATION_FILE)
    replacements = kust.get("replacements") or []
    ing = _ingress()
    ingress_ref = {
        "kind": "Ingress",
        "name": ing["metadata"]["name"],
    }
    wired = [
        r for r in replacements
        if (r.get("targets") or [{}])[0].get("select", {}) == ingress_ref
    ]
    assert wired, (
        "kustomization.yaml 缺 replacements —— 04-ingress.yaml 的 host 无法从 "
        "01-configmap.yaml 的 INGRESS_HOST 覆盖，部署方只能改清单字面值"
    )
    rep = wired[0]
    source = rep.get("source") or {}
    assert source.get("kind") == "ConfigMap" and source.get("name") == "webgis-config", (
        f"replacement source={source} 不是 ConfigMap webgis-config"
    )
    assert source.get("fieldPath") == "data.INGRESS_HOST", (
        f"replacement source fieldPath={source.get('fieldPath')!r} 应为 "
        "'data.INGRESS_HOST'"
    )
    field_paths = set((rep["targets"][0].get("fieldPaths") or []))
    assert {"spec.tls.0.hosts.0", "spec.rules.0.host"} <= field_paths, (
        f"replacement targets fieldPaths={sorted(field_paths)} 未覆盖 "
        "spec.tls[0].hosts[0] 与 spec.rules[0].host —— 证书域名与服务域名会脱节"
    )
    # 覆盖默认值必须与清单字面值一致（raw-apply 与 kustomize build 同结果）
    configmap = [d for d in _load_all(CONFIGMAP_FILE) if d.get("kind") == "ConfigMap"][0]
    override = configmap["data"]["INGRESS_HOST"]
    _assert_host_not_placeholder(override)
    raw_hosts = {r["host"] for r in ing["spec"]["rules"]}
    assert raw_hosts == {override}, (
        f"01-configmap.yaml 的 INGRESS_HOST={override!r} 与 04-ingress.yaml 的 "
        f"host {sorted(raw_hosts)} 不一致 —— 只改一处会让另一处退化成占位值"
    )


def test_cluster_issuer_is_cluster_scoped():
    """issuer 清单不得带 namespace：apiserver 对 cluster-scoped 资源会以
    "not allowed when resource is not namespaced" 拒绝。"""
    ing = _ingress()
    issuer = ing["metadata"]["annotations"][ISSUER_ANNOTATION]
    for name, doc in _issuer_manifests():
        if doc["metadata"]["name"] != issuer:
            continue
        assert not doc["metadata"].get("namespace"), (
            f"{name}: ClusterIssuer/Issuer 是 cluster-scoped，写 namespace 会被 "
            "apiserver 拒绝"
        )


def test_cluster_issuer_is_not_in_kustomize_resources():
    """kustomization 有 `namespace: webgis-prod`，kustomize 的 namespace
    转换器会给资源树里每个资源注入 metadata.namespace —— 把 cluster-scoped 的
    issuer 列进 resources 会让整个 `kubectl apply -k .` 失败。"""
    kust = _load(KUSTOMIZATION_FILE)
    listed = {Path(r).name for r in kust.get("resources") or []}
    issuer_files = {name for name, _ in _issuer_manifests()}
    overlap = listed & issuer_files
    assert not overlap, (
        f"{sorted(overlap)} 是 cluster-scoped 的 issuer 清单，不能进 resources"
        "（会被注入 metadata.namespace 并被 apiserver 拒绝）—— 需单独 "
        "`kubectl apply -f deploy/k8s/<issuer>.yaml`"
    )


def test_cluster_issuer_acme_config_is_usable():
    """issuer 必须能真正签发：LE ACME 目录 + HTTP-01 solver（与 ingress-nginx
    的 class 对齐）+ 账号私钥 SecretRef + 非占位的注册邮箱。"""
    ing = _ingress()
    issuer = ing["metadata"]["annotations"][ISSUER_ANNOTATION]
    for _, doc in _issuer_manifests():
        if doc["metadata"]["name"] != issuer:
            continue
        acme = doc["spec"]["acme"]
        assert acme["server"].startswith("https://acme"), (
            f"ACME server={acme['server']!r} 不是 ACME 目录地址"
        )
        assert acme["privateKeySecretRef"]["name"], "issuer 缺 privateKeySecretRef"
        email = acme.get("email", "")
        assert email, "issuer 缺 ACME 注册邮箱（占位也不行 —— ACME 注册即失败）"
        assert not email.lower().startswith(("admin@example", "user@example")), (
            f"ACME email={email!r} 是常见假邮箱 —— 会静默注册到别人的账号上"
        )
        solvers = acme.get("solvers") or []
        assert solvers, "issuer 没有 solvers —— HTTP-01/DNS-01 challenge 无法完成"
        http01 = solvers[0].get("http01")
        assert http01, "issuer 第一个 solver 不是 http01"
        assert http01["ingress"]["class"] == "nginx", (
            "http01 solver 的 ingress class 必须与 ingress-nginx 一致（nginx）"
        )


def test_ssl_redirect_stays_enabled():
    """修好证书链路后 ssl-redirect 必须保持开启 —— 不得用"关掉重定向"的方式
    规避无证书问题（那会让 JWT bearer / X-Session-Token 明文过网）。"""
    ing = _ingress()
    value = ing["metadata"]["annotations"].get(
        "nginx.ingress.kubernetes.io/ssl-redirect"
    )
    assert value == "true", (
        f"ssl-redirect={value!r} —— 证书链路已修好（04b-clusterissuer.yaml），"
        "关掉重定向等于把凭证暴露在明文 HTTP 上"
    )


def test_tls_secret_is_not_committed():
    """webgis-tls 由 cert-manager 创建并轮换；仓库里不得手写该 Secret
    （否则要么与 cert-manager 抢所有权，要么是一张永不过期的静态证书）。"""
    ing = _ingress()
    secret_names = {t["secretName"] for t in ing["spec"].get("tls", [])}
    committed = set()
    for path in sorted(K8S_DIR.glob("*.yaml")):
        for doc in _load_all(path):
            if doc.get("kind") == "Secret":
                committed.add(doc["metadata"]["name"])
    overlap = secret_names & committed
    assert not overlap, (
        f"{sorted(overlap)} 被手写成静态 Secret —— 证书必须由 cert-manager 依据 "
        "04b-clusterissuer.yaml 签发"
    )
