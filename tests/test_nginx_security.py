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


class TestNginxApiRoutingAndBodySizeGuards:
    """#585/#586：静态资源 regex location 不得劫持 /api/ 路由；上传体积上限
    需在 server 级设全局基线（不能只设在 /api/v1/upload）。"""

    NGINX = "deploy/nginx/nginx.conf"

    def _read(self):
        with open(self.NGINX) as f:
            return f.read()

    def test_static_regex_locations_exclude_api_prefix(self):
        """regex location 必须用 ^(?!/api/) 把 /api/ 前缀排除在静态缓存外。

        nginx 的 regex location 匹配优先级高于普通前缀 location /api/（/api/ 无
        ^~ 修饰）——导出的 /api/v1/export/download/*.png 与 /api/v1/static/* 若
        命中静态 regex 会被改发到 frontend 而 404（#585）。
        """
        content = self._read()
        assert "^(?!/api/).*\\.(js|css)$" in content, (
            "JS/CSS 静态 regex 未排除 /api/ 前缀 —— /api/v1/static/*.js|css 会被劫持到 frontend"
        )
        assert "^(?!/api/).*\\.(png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$" in content, (
            "图片静态 regex 未排除 /api/ 前缀 —— /api/v1/export/download/*.png、"
            "/api/v1/static/* 会被劫持到 frontend 404（#585）"
        )
        assert "location ~* \\.(png|jpg|jpeg" not in content, (
            "图片静态 regex 仍是旧的无 /api/ 排除形式（#585 回归）"
        )

    def test_client_max_body_size_set_at_server_level(self):
        """server 级必须有全局 client_max_body_size，不能只设在 /api/v1/upload。

        否则 /api/v1/export（应用侧上限 50MB）、/api/v1/export/pdf、
        /api/v1/skills/upload 落入 nginx 内置默认 1M 上限（#586）。
        """
        content = self._read()
        # 至少两处：server 级全局基线（100M）+ location /api/v1/upload 冗余显式值
        assert content.count("client_max_body_size 100M;") >= 2, (
            "client_max_body_size 100M 应有 server 级全局基线 + /upload 冗余两处"
        )


class TestNginxWebSocketRouting:
    """#924: WebSocket 必须走 canonical /api/v1/ws/，且不再有 broken /ws/。"""

    NGINX = "deploy/nginx/nginx.conf"

    def _read(self):
        with open(self.NGINX) as f:
            return f.read()

    def test_canonical_ws_location_exists_with_upgrade_headers(self):
        content = self._read()
        assert "location /api/v1/ws/" in content, (
            "缺失 canonical WS location /api/v1/ws/ — "
            "FastAPI WS 路由为 /api/v1/ws/{session_id}，必须有专用 location 透传 Upgrade"
        )
        import re
        m = re.search(r"location /api/v1/ws/ \{(.*?)\n        \}", content, re.DOTALL)
        assert m, "location /api/v1/ws/ 块无法解析"
        block = m.group(1)
        assert "proxy_set_header Upgrade $http_upgrade;" in block, (
            "canonical WS location 必须透传 Upgrade 头"
        )
        assert "proxy_set_header Connection $connection_upgrade;" in block, (
            "canonical WS location 必须使用 $connection_upgrade（map $http_upgrade），"
            "不能硬编码 upgrade 或清空 Connection"
        )
        assert "ws_no_query" in block, "canonical WS 应使用 ws_no_query 避免 JWT 落盘"
        assert "proxy_read_timeout 86400s;" in block, "WS 长连接超时应为 86400s"

    def test_no_standalone_ws_location(self):
        content = self._read()
        assert "location /ws/ {" not in content, (
            "旧的 location /ws/ 会把 /ws/... 直接 proxy 到 api_backend 导致 404 "
            "(FastAPI 实际路由为 /api/v1/ws/)；应已移除，WS 只走 canonical /api/v1/ws/"
        )

    def test_ws_connection_upgrade_map_exists(self):
        content = self._read()
        assert "map $http_upgrade $connection_upgrade" in content, (
            "需要 map $http_upgrade $connection_upgrade 以在非 WS 请求上复用 keepalive"
        )
