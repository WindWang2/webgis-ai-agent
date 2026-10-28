"""Security: Nginx CORS must use allow-list, not echo any Origin."""
import re


def test_nginx_cors_not_wildcard_echo():
    """nginx.conf must NOT use $http_origin directly (echoes any Origin)."""
    with open("deploy/nginx/nginx.conf", encoding="utf-8") as f:
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
        import re

        import yaml

        with open("docker-compose.prod.secure.yml", encoding="utf-8") as f:
            compose = yaml.safe_load(f)
        embedded = compose["configs"]["webgis_nginx_conf"]["content"]
        with open("deploy/nginx/nginx.conf", encoding="utf-8") as f:
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

    def test_no_private_key_material_committed(self):
        """audit ISSUE-064（#1349）：scaffold TLS 私钥不得入库——启动期生成。"""
        with open("docker-compose.prod.secure.yml", encoding="utf-8") as f:
            text = f.read()
        assert "BEGIN PRIVATE KEY" not in text, "私钥材料不得提交进版本库"
        assert "BEGIN CERTIFICATE" not in text, "scaffold 证书应启动期生成而非入库"

    def test_nginx_service_consumes_inline_configs(self):
        import yaml

        with open("docker-compose.prod.secure.yml", encoding="utf-8") as f:
            compose = yaml.safe_load(f)
        nginx = compose["services"]["nginx"]
        sources = {c["source"] for c in nginx.get("configs", [])}
        assert "webgis_nginx_conf" in sources, (
            f"nginx 服务未挂载内联 configs: {sources}"
        )
        targets = {c["target"] for c in nginx["configs"]}
        assert "/etc/nginx/nginx.conf" in targets
        # ISSUE-064：证书不再经 config 分发；服务必须在启动期生成 scaffold。
        cmd = str(nginx.get("command", ""))
        assert "openssl req -x509" in cmd, "启动期 scaffold 生成缺失"
        assert "server.key" in cmd and "server.crt" in cmd
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
        with open(self.NGINX, encoding="utf-8") as f:
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
        with open(self.NGINX, encoding="utf-8") as f:
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


class TestCspSingleSource:
    """#1635：CSP 只有一份拷贝 —— frontend/next.config.mjs 的 headers()。

    修复前 deploy/nginx/nginx.conf 与 docker-compose.prod.secure.yml 的内联
    副本在 server 级再注入一份更严的 CSP。CSP3 规定多个 CSP 响应头 = 多条
    策略、浏览器全部强制执行（等效取交集）：应用侧比 nginx 宽松的指令全部
    作废 —— connect-src data:（#1597 maplibre-gl v6 worker 适配所加）、
    img-src blob:、worker-src blob:、font-src data: 在生产 compose 栈被
    完全中和，表现为「dev 生效、上线坏」且无任何报错指向 CSP。nginx 自身
    只生成 /health 文本与 50x 错误页，无 CSP 需求，故边缘侧不再注入。

    同理收敛 X-Frame-Options：server 级注入会被 location / 继承，与上游
    响应自带的应用级 DENY 双头冲突（重复且取值不一致，行为依浏览器而异）；
    nginx 自己生成的 50x 错误页改为在 location = /50x.html 显式保留。
    """

    def test_nginx_conf_server_level_does_not_inject_csp(self):
        """#1635 收敛 + #1732 修正：server/http 级**不得**注入 CSP（CSP 唯一
        来源是 frontend/next.config.mjs —— 边缘再叠一份更严策略会把应用侧
        放宽的指令在生产栈完全中和）；但 location 级（SSE 流式响应）必须
        重复全部安全头 —— location 级 add_header 一出现即替换 server 级
        继承（#1732）。两者由 _SERVER_BLOCKS 区分。"""
        with open("deploy/nginx/nginx.conf", encoding="utf-8") as f:
            nginx = f.read()
        # server 级 = http{}/server{} 直接体（不含 location 块）—— 用
        # location 块剔除后检查剩余部分。
        stripped = re.sub(r"location[^{]*\{[^}]*\}", "", nginx)
        assert "add_header Content-Security-Policy" not in stripped, (
            "nginx server 级不得注入 CSP —— CSP 唯一来源是 "
            "frontend/next.config.mjs（#1635）"
        )

    def test_secure_compose_inline_does_not_inject_csp(self):
        import yaml

        with open("docker-compose.prod.secure.yml", encoding="utf-8") as f:
            compose = yaml.safe_load(f)
        embedded = compose["configs"]["webgis_nginx_conf"]["content"]
        stripped = re.sub(r"location[^{]*\{[^}]*\}", "", embedded)
        assert "add_header Content-Security-Policy" not in stripped, (
            "compose 内联 nginx 副本 server 级不得注入 CSP"
            "（与 deploy/nginx/nginx.conf 同一不变量）"
        )

    def test_next_config_is_the_single_csp_source(self):
        """应用侧必须恰好定义一份 CSP，且保留运行时依赖的放宽指令。"""
        import re

        with open("frontend/next.config.mjs", encoding="utf-8") as f:
            config = f.read()
        assert config.count('key: "Content-Security-Policy"') == 1, (
            "next.config.mjs 必须恰好定义一份 CSP（唯一来源）"
        )
        m = re.search(
            r'key:\s*"Content-Security-Policy",\s*value:\s*\[(.*?)\]\.join',
            config,
            re.DOTALL,
        )
        assert m, "无法解析 next.config.mjs 的 CSP value 数组"
        directives = [
            d.strip().rstrip(",").strip().strip('"') for d in m.group(1).splitlines()
        ]
        directives = [d for d in directives if d]
        for required in (
            "default-src 'self'",
            # #1597：maplibre-gl v6 worker 适配依赖 data: URI 的 connect
            "connect-src 'self' data: http: https: ws: wss:",
            "img-src 'self' data: blob: http: https:",
            "worker-src 'self' blob:",
            "font-src 'self' data:",
            "frame-ancestors 'none'",
        ):
            assert required in directives, (
                f"next.config.mjs CSP 缺少指令 {required!r} —— 生产栈唯一下发"
                "的 CSP，缺指令会被浏览器直接阻断对应能力"
            )

    def test_nginx_server_level_does_not_inject_x_frame_options(self):
        """server 级 add_header（8 空格缩进）不得含 X-Frame-Options —— 它会被
        location / 继承，与上游应用响应自带的 DENY 双头冲突。location 级
        （12 空格缩进：/api/、/health、/50x.html）的显式注入不在此列。"""
        import re

        with open("deploy/nginx/nginx.conf", encoding="utf-8") as f:
            nginx = f.read()
        assert not re.findall(r"^        add_header X-Frame-Options", nginx, re.M), (
            "server 级仍在注入 X-Frame-Options —— location / 的上游响应已带"
            "应用级 DENY，双头且取值冲突（#1635）"
        )

    def test_app_sets_deny_x_frame_options(self):
        """前端响应的 XFO 唯一来源是应用的 DENY（与 frame-ancestors 'none'
        双保险）。"""
        with open("frontend/next.config.mjs", encoding="utf-8") as f:
            config = f.read()
        assert 'key: "X-Frame-Options", value: "DENY"' in config, (
            "next.config.mjs 必须设置 X-Frame-Options: DENY（nginx 侧已不再注入）"
        )
