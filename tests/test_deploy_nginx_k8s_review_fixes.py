"""2026-10-19 swarm 深审部署链路三项修复的回归契约（#1729 / #1732 / #1711）：

- #1729-1 k8s 只读 rootfs：api Deployment 必须把技能目录（应用侧写相对路径
  "app/skills"，Dockerfile.prod WORKDIR /app/backend 下解析为
  /app/backend/app/skills）与 HOME 缓存 /app/.cache 挂成可写卷，且镜像内对应
  路径已交给 appuser（否则挂载点属主是 root，uid 1001 仍写不进去），seed
  initContainer 保证镜像自带技能不被空挂载遮没；
- #1729-2 nginx /health：不得硬编码 `return 200 "healthy"` 恒绿，必须
  proxy_pass 上游真实健康端点 /api/v1/ready（依赖不可达 → 503，上游不可达
  → nginx 502）；
- #1711-3 CORS 预检：不得被 nginx `return 204` 短路，nginx 不得自持
  Access-Control-* 头 —— 单一事实源是应用层 CORSMiddleware
  （app/main.py allow_headers 的 8 个必需头）；
- #1732-1 CORS 白名单：不得含 localhost 端口段（配合 credentials 即把任意
  本机页面当可信源）；
- #1732-2 SSE location：location 级 add_header 一出现即替换 server 级继承，
  两个流式 location 必须重复 5 个安全响应头。

同一批契约对两份工件生效：deploy/nginx/nginx.conf 与
docker-compose.prod.secure.yml 内联分发的副本（逐字节 parity 由
tests/test_nginx_security.py 锁定，这里直接对副本断言语义）。
"""
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
NGINX_CONF = REPO / "deploy" / "nginx" / "nginx.conf"
SECURE_COMPOSE = REPO / "docker-compose.prod.secure.yml"
K8S_API = REPO / "deploy" / "k8s" / "02-api-deployment.yaml"
DOCKERFILE_PROD = REPO / "Dockerfile.prod"
MAIN_PY = REPO / "app" / "main.py"
CONFIG_PY = REPO / "app" / "api" / "routes" / "config.py"
HEALTH_PY = REPO / "app" / "api" / "routes" / "health.py"

#: server 级安全响应头（nginx.conf add_header，location 出现 add_header 即
#: 替换继承，必须逐 location 重复）
SECURITY_HEADERS = (
    ("Strict-Transport-Security", "max-age=63072000; includeSubDomains"),
    ("Content-Security-Policy", "default-src 'self'"),
    ("X-Frame-Options", "SAMEORIGIN"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
)

SSE_LOCATIONS = (
    "location = /api/v1/chat/stream {",
    "location ~ ^/api/v1/explorer/stream/ {",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code_only(conf: str) -> str:
    """去掉 # 行注释（断言指令语义时避免被注释里的历史片段误伤）。"""
    return "\n".join(re.sub(r"#.*$", "", line) for line in conf.split("\n"))


def _location_block(conf: str, header: str) -> str:
    """取 location 块（含首尾行）—— 在去注释文本上按括号配平切分。"""
    lines = _code_only(conf).split("\n")
    start = next(i for i, line in enumerate(lines) if line.strip() == header)
    depth = 0
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth <= 0:
            return "\n".join(lines[start:i + 1])
    raise AssertionError(f"{header}: location 块括号不配平")


def _embedded_nginx_conf() -> str:
    """docker-compose.prod.secure.yml 内联分发的 nginx 配置（$$→$ 还原）。"""
    compose = yaml.safe_load(_read(SECURE_COMPOSE))
    embedded = compose["configs"]["webgis_nginx_conf"]["content"]
    return embedded.replace("$$", "$")


def _nginx_confs() -> tuple:
    return (_read(NGINX_CONF), _embedded_nginx_conf())


# ── k8s 侧（#1729-1）──────────────────────────────────────────────────────


def _api_pod_spec() -> dict:
    docs = [d for d in yaml.safe_load_all(_read(K8S_API)) if d]
    dep = next(d for d in docs if d.get("kind") == "Deployment")
    return dep["spec"]["template"]["spec"]


def _container_mount_paths(pod: dict) -> list:
    return [m["mountPath"] for c in pod["containers"] for m in c["volumeMounts"]]


def _mount_source(pod: dict, mount_path: str) -> str:
    volumes = {v["name"]: v for v in pod.get("volumes", [])}
    for c in pod["containers"]:
        for m in c.get("volumeMounts", []):
            if m.get("mountPath") == mount_path:
                v = volumes[m["name"]]
                return "emptyDir" if "emptyDir" in v else str(v)
    raise AssertionError(f"api 容器未挂载 {mount_path}")


def _final_workdir() -> str:
    """Dockerfile.prod 的最终 WORKDIR —— 容器 CWD，相对路径 skills_dir 的解析基准。"""
    workdirs = re.findall(r"^WORKDIR (\S+)", _read(DOCKERFILE_PROD), re.M)
    assert workdirs, "Dockerfile.prod 无 WORKDIR"
    return workdirs[-1].rstrip("/")


def _image_writable_app_paths() -> set:
    """Dockerfile.prod 显式交给 appuser 的 /app 下可写路径（mkdir -p / chown 目标）。"""
    text = _read(DOCKERFILE_PROD).replace("\\\n", " ")
    paths: set = set()
    m = re.search(r"mkdir -p ((?:/app/\S+[ \t]*)+)", text)
    assert m, "Dockerfile.prod 找不到 mkdir -p /app/... 可写路径声明"
    paths |= set(m.group(1).split())
    m = re.search(r"chown -R \S+ ((?:/app/\S+[ \t]*)+)", text)
    assert m, "Dockerfile.prod 找不到 chown 可写路径声明"
    paths |= set(m.group(1).split())
    return paths


def _expected_skills_mount() -> str:
    return _final_workdir() + "/app/skills"


class TestK8sReadOnlyRootFilesystemWritablePaths:
    """#1729-1: readOnlyRootFilesystem: true 缺 app/skills 与 /app/.cache 可写路径。"""

    def test_skills_dir_mounted_writable_at_resolved_path(self):
        """上传路由与 agent 自建技能写相对路径 "app/skills"（config.py:205,238；
        skill_creator.py 默认同值），readOnlyRootFilesystem 下必须挂可写卷。"""
        assert 'skills_dir = "app/skills"' in _read(CONFIG_PY), (
            "应用侧技能目录不再是相对 app/skills —— 部署挂载路径需同步 review"
        )
        expected = _expected_skills_mount()
        pod = _api_pod_spec()
        assert expected in _container_mount_paths(pod), (
            f"技能目录 {expected}（WORKDIR {_final_workdir()} + 相对 app/skills）"
            "未挂载 —— 技能上传 / agent 自建技能永久 EROFS"
        )
        assert _mount_source(pod, expected) == "emptyDir", (
            f"{expected} 必须挂可写卷（emptyDir/PVC）"
        )

    def test_skills_mount_path_is_writable_in_image(self):
        """挂载点路径必须在镜像内已归 appuser —— 否则 uid 1001 仍写不进去。"""
        expected = _expected_skills_mount()
        assert expected in _image_writable_app_paths(), (
            f"{expected} 未在 Dockerfile.prod 交给 appuser（mkdir/chown 目标）"
        )

    def test_home_cache_mounted_writable(self):
        """HOME=/app（useradd --home /app）→ fontconfig/rasterio/HF 等写
        /app/.cache，只读 rootfs 下同样 EROFS。"""
        pod = _api_pod_spec()
        assert "/app/.cache" in _container_mount_paths(pod), (
            "/app/.cache 未挂载 —— 运行期缓存写入 EROFS"
        )
        assert _mount_source(pod, "/app/.cache") == "emptyDir"
        assert "/app/.cache" in _image_writable_app_paths()

    def test_seed_initcontainer_populates_skills_volume(self):
        """空 emptyDir 盖住技能目录会把镜像自带的 .md prompt 技能遮没
        （load_skills 启动即扫，app/tools/__init__.py:107）—— 必须有
        initContainer 把镜像内容播种进同一个卷。"""
        pod = _api_pod_spec()
        seeders = [
            c for c in pod.get("initContainers", [])
            if any(m.get("name") == "skills-volume" for m in c.get("volumeMounts", []))
        ]
        assert seeders, (
            "没有 initContainer 播种 skills-volume —— 镜像自带技能被空挂载遮蔽"
        )
        cmd = " ".join(seeders[0].get("command", []) + seeders[0].get("args", []))
        assert _expected_skills_mount() in cmd, "播种命令未从镜像技能目录拷贝"


# ── nginx /health（#1729-2）───────────────────────────────────────────────


class TestNginxHealthProxiesUpstream:
    """#1729-2: nginx /health 不得硬编码 200 恒绿，必须 proxy_pass 上游真实健康端点。"""

    def test_health_location_proxies_api_ready(self):
        for conf in _nginx_confs():
            code = _location_block(conf, "location = /health {")
            assert "return 200" not in code and "return 204" not in code, (
                "/health 仍硬编码 return —— 上游 API/依赖全挂也恒绿（#1729）"
            )
            assert "proxy_pass http://api_backend/api/v1/ready;" in code, (
                "/health 未 proxy_pass 上游 /api/v1/ready（无 URI 的 proxy_pass "
                "会把 /health 原样透传给没有根级路由的应用 → 404）"
            )
            for name, value in SECURITY_HEADERS:
                assert f'add_header {name} "{value}' in code, (
                    f"/health 缺安全响应头 {name}（location 级 add_header 替换继承）"
                )

    def test_healthz_still_reaches_the_app(self):
        """/healthz 是应用根级 liveness 路由（main.py:758）。此前被前缀
        `location /health` 顺手接走恒返 200；/health exact 化后必须显式透传，
        否则落 location / 前端 404。"""
        assert '@app.get("/healthz"' in _read(MAIN_PY)
        for conf in _nginx_confs():
            code = _location_block(conf, "location = /healthz {")
            assert "proxy_pass http://api_backend;" in code, (
                "/healthz 未透传应用（无 URI proxy_pass = 原样转发 /healthz）"
            )
            assert "return 200" not in code
            for name, value in SECURITY_HEADERS:
                assert f'add_header {name} "{value}' in code, (
                    f"/healthz 缺安全响应头 {name}"
                )

    def test_ready_endpoint_exists_in_app(self):
        """proxy 目标必须是真实路由：health router 挂在 /api/v1 前缀下。"""
        assert '@router.get("/ready"' in _read(HEALTH_PY)
        assert 'include_router(health.router, prefix="/api/v1"' in _read(MAIN_PY)


# ── nginx SSE 安全头（#1732-2）────────────────────────────────────────────


class TestNginxSseSecurityHeaders:
    """#1732-2: location 级 add_header 一出现即替换 server 级继承，两个 SSE
    location 必须重复 5 个安全响应头。"""

    def test_sse_locations_repeat_all_five_security_headers(self):
        for conf in _nginx_confs():
            for header in SSE_LOCATIONS:
                block = _location_block(conf, header)
                for name, value in SECURITY_HEADERS:
                    assert f'add_header {name} "{value}' in block, (
                        f"{header} 缺安全响应头 {name} —— location 级 add_header "
                        "已替换 server 级继承，流式响应丢安全头（#1732）"
                    )


# ── nginx CORS（#1732-1 / #1711-3）────────────────────────────────────────


class TestNginxCorsSingleSourceOfTruth:
    """#1732-1 / #1711-3: CORS 白名单不得含 localhost 端口段；预检不得被 nginx
    return 204 短路 —— 单一事实源是应用层 CORSMiddleware（main.py:957-985）。"""

    def test_no_localhost_in_cors_allowlist(self):
        for conf in _nginx_confs():
            code = _code_only(conf)
            assert "localhost" not in code, (
                "nginx 配置仍把 localhost 列为可信源（#1732：整个端口段 + credentials）"
            )
            assert "map $http_origin" not in code, (
                "nginx 仍自持 CORS allow-list map —— 应与应用层单一事实源对齐"
            )

    def test_options_preflight_not_short_circuited(self):
        for conf in _nginx_confs():
            code = _code_only(conf)
            assert "return 204" not in code, "预检仍被 return 204 短路（#1711）"
            assert "OPTIONS" not in code, (
                "仍有 OPTIONS 短路逻辑 —— 预检应透传应用层 CORSMiddleware"
            )

    def test_nginx_adds_no_access_control_headers(self):
        """nginx 不再 add_header Access-Control-*：与应用层重复下发同一头，
        浏览器按多值 ACAO 判失败；放行清单以 main.py 为单一事实源。"""
        for conf in _nginx_confs():
            assert "add_header Access-Control-" not in _code_only(conf)

    def test_app_allow_headers_are_the_eight_required(self):
        """#1711 的依赖面：nginx 透传后，预检放行清单完全由 main.py 的
        allow_headers 决定 —— 锁定 8 个必需头，删一个即跨域预检失败。"""
        m = re.search(r"allow_headers=\[(.*?)\]", _read(MAIN_PY), re.DOTALL)
        assert m, "app/main.py 找不到 allow_headers 声明"
        headers = re.findall(r'"([^"]+)"', m.group(1))
        assert headers == [
            "Authorization",
            "Content-Type",
            "Accept",
            "X-Session-Token",
            "X-Request-ID",
            "Last-Event-ID",
            "traceparent",
            "tracestate",
        ], f"main.py allow_headers 漂移: {headers}"
