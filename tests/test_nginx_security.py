"""Security: Nginx CORS must use allow-list, not echo any Origin."""


def test_nginx_cors_not_wildcard_echo():
    """nginx.conf must NOT use $http_origin directly (echoes any Origin)."""
    with open("deploy/nginx/nginx.conf") as f:
        content = f.read()

    # Must not have: add_header Access-Control-Allow-Origin $http_origin
    assert "$http_origin" not in content or "map $http_origin" in content, (
        "nginx.conf echoes $http_origin directly — any website can make "
        "credentialed cross-origin requests. Use a map with explicit allow-list."
    )


class TestSecureComposeInlineNginxParity:
    """#472: docker-compose.prod.secure.yml 内联分发的 nginx 配置/TLS scaffold
    必须与仓库文件逐字节一致，且 nginx 服务不得再 bind-mount 未随 deploy-prod
    分发的路径。

    deploy-prod 只 scp compose 文件 + deploy/redis*.conf + 镜像 tar +
    .env.Priv —— `./deploy/nginx/nginx.conf` 与 `./deploy/nginx/ssl` 都到不了
    主机，Docker 把缺失的挂载源创建为空目录后 nginx 启动即死。内联 configs
    让部署工件自包含；parity 测试保证内联副本不会与 deploy-config CI 门
    （nginx -t）校验的仓库文件漂移。
    """

    def test_embedded_nginx_conf_matches_repo_conf(self):
        import yaml

        with open("docker-compose.prod.secure.yml") as f:
            compose = yaml.safe_load(f)
        embedded = compose["configs"]["webgis_nginx_conf"]["content"]
        with open("deploy/nginx/nginx.conf") as f:
            repo = f.read()
        # content 内 nginx 变量按 compose 转义规则双写为 $$（渲染时还原为 $）；
        # 未双写的话，docker compose 渲染时会把 $remote_addr 等当作未定义的
        # 插值变量置空 —— nginx 得到一份被抽掉变量的配置。
        assert "$$" not in repo, "repo nginx.conf 自身含 $$，还原比对将歧义"
        assert embedded.replace("$$", "$") == repo, (
            "docker-compose.prod.secure.yml 内联的 webgis_nginx_conf 与 "
            "deploy/nginx/nginx.conf 不一致 —— 修改 nginx.conf 后必须同步更新"
            "内联 configs（deploy-config 的 nginx -t 只校验仓库文件）"
        )

    def test_embedded_ssl_scaffold_is_valid_pem_pair(self):
        import yaml

        with open("docker-compose.prod.secure.yml") as f:
            compose = yaml.safe_load(f)
        crt = compose["configs"]["webgis_ssl_scaffold_cert"]["content"]
        key = compose["configs"]["webgis_ssl_scaffold_key"]["content"]
        # 自签名 scaffold：只需是结构合法的 PEM，让 nginx 能以 TLS 启动
        assert crt.startswith("-----BEGIN CERTIFICATE-----"), "cert 非 PEM"
        assert crt.rstrip().endswith("-----END CERTIFICATE-----"), "cert 未闭合"
        assert key.startswith("-----BEGIN PRIVATE KEY-----"), "key 非 PEM"
        assert key.rstrip().endswith("-----END PRIVATE KEY-----"), "key 未闭合"

    def test_nginx_service_consumes_inline_configs(self):
        import yaml

        with open("docker-compose.prod.secure.yml") as f:
            compose = yaml.safe_load(f)
        nginx = compose["services"]["nginx"]
        sources = {c["source"] for c in nginx.get("configs", [])}
        assert {
            "webgis_nginx_conf",
            "webgis_ssl_scaffold_cert",
            "webgis_ssl_scaffold_key",
        } <= sources, f"nginx 服务未挂载内联 configs: {sources}"
        # 挂载到 nginx.conf 引用的路径（/etc/nginx/nginx.conf 与 /etc/nginx/ssl/）
        targets = {c["target"] for c in nginx["configs"]}
        assert "/etc/nginx/nginx.conf" in targets
        assert "/etc/nginx/ssl/server.crt" in targets
        assert "/etc/nginx/ssl/server.key" in targets
        # 不得再依赖未随 deploy-prod 分发的 bind-mount 源
        for v in nginx.get("volumes", []):
            assert "deploy/" not in str(v), (
                f"nginx 仍 bind-mount 未随部署分发的路径: {v}"
            )
