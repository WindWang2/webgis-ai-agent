"""Security: docker-compose files must follow secure defaults."""
import os
import subprocess
import yaml


DOCKER_COMPOSE = "docker-compose.yml"
DOCKER_COMPOSE_PROD = "docker-compose.prod.yml"
DOCKER_COMPOSE_PROD_SECURE = "docker-compose.prod.secure.yml"


def _load_compose(path):
    with open(path) as f:
        return yaml.safe_load(f)


class TestDevComposeSecurity:
    def test_db_password_not_default_postgres(self):
        """DB_PASSWORD should not default to well-known 'postgres'."""
        compose = _load_compose(DOCKER_COMPOSE)
        env = compose["services"]["db"]["environment"]
        pw = env.get("POSTGRES_PASSWORD", "")
        # Should use :? (required) or at least not 'postgres'
        assert ":?" in pw or ":-postgres" not in pw, (
            f"DB password defaults to 'postgres': {pw}"
        )

    def test_redis_has_requirepass(self):
        """Redis command must include --requirepass."""
        compose = _load_compose(DOCKER_COMPOSE)
        cmd = compose["services"]["redis"].get("command", "")
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        assert "--requirepass" in cmd, (
            f"Redis has no authentication. Command: {cmd}"
        )

    def test_api_and_celery_use_compose_postgis_not_sqlite(self):
        """#618-32: Settings only reads DATABASE_URL. compose must pin
        api/celery to the PostGIS `db` service — interpolating host .env
        sqlite would let two containers write one SQLite file.
        """
        compose = _load_compose(DOCKER_COMPOSE)
        for svc in ("api", "celery-worker"):
            env = compose["services"][svc].get("environment") or []
            if isinstance(env, dict):
                mapping = env
            else:
                mapping = {}
                for e in env:
                    if isinstance(e, str) and "=" in e:
                        k, v = e.split("=", 1)
                        mapping[k] = v
            url = mapping.get("DATABASE_URL", "")
            assert url.startswith("postgresql://"), (
                f"docker-compose.yml {svc} DATABASE_URL must be PostGIS, got {url!r}"
            )
            assert "@db:5432/" in url, (
                f"docker-compose.yml {svc} DATABASE_URL must target service db, got {url!r}"
            )
            assert "DB_HOST" not in mapping, (
                f"docker-compose.yml {svc} still injects dead DB_HOST "
                "(Settings has no such field)"
            )

    def test_env_example_keeps_sqlite_for_pytest_and_compose_secrets(self):
        """#618-32: .env.example sqlite is for local pytest; compose secrets exist."""
        with open(".env.example", encoding="utf-8") as f:
            text = f.read()
        assert "DATABASE_URL=sqlite:///./data/webgis.db" in text
        assert "DB_PASSWORD=" in text
        assert "REDIS_PASSWORD=" in text
        live_keys = [
            line.split("=", 1)[0].strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#") and "=" in line
        ]
        assert "DB_HOST" not in live_keys, (
            ".env.example must not declare live DB_HOST (Settings ignores it)"
        )


class TestProdComposeSecurity:
    def test_db_port_binds_localhost(self):
        """Production compose DB port must bind to 127.0.0.1 only."""
        compose = _load_compose(DOCKER_COMPOSE_PROD)
        db_ports = compose["services"].get("db", {}).get("ports", [])
        for p in db_ports:
            assert str(p).startswith("127.0.0.1:"), (
                f"DB port exposed to network: {p}"
            )

    def test_redis_port_binds_localhost(self):
        """Production compose Redis port must bind to 127.0.0.1 only."""
        compose = _load_compose(DOCKER_COMPOSE_PROD)
        redis_ports = compose["services"].get("redis", {}).get("ports", [])
        for p in redis_ports:
            assert str(p).startswith("127.0.0.1:"), (
                f"Redis port exposed to network: {p}"
            )

    def test_prometheus_port_binds_localhost(self):
        """Prometheus port in secure compose must bind to 127.0.0.1 only."""
        compose = _load_compose("docker-compose.prod.secure.yml")
        prom_ports = compose["services"].get("prometheus", {}).get("ports", [])
        for p in prom_ports:
            assert str(p).startswith("127.0.0.1:"), (
                f"Prometheus port exposed to network: {p}"
            )


def _named_volume_mounts(service, volume_name):
    """The `<volume_name>:` named-volume mounts (target paths) of a compose service."""
    return [
        v
        for v in service.get("volumes", [])
        if isinstance(v, str) and v.startswith(f"{volume_name}:")
    ]


class TestComposeDataDirVolumeParity:
    """#471 + #519：每个 compose 栈里 celery-worker 必须与 api 挂同一个共享卷
    覆盖 DATA_DIR（=/app/data）。

    #471 原契约（`uploads` 命名卷挂 /app/uploads）被 #519 的共享数据卷取代：
    应用把所有文件解析到 DATA_DIR 下（get_upload_dir → DATA_DIR/uploads，
    spatial_tasks → DATA_DIR/analysis_results，monitoring_report →
    DATA_DIR/monitoring_reports，map → DATA_DIR/exports）。共享整个 DATA_DIR
    子树（而非只共享 uploads）才是 api/celery 都能看到对方文件的正确做法 ——
    worker 在独立容器文件系统层里，不挂同一个卷，上传成功的 raster/shapefile
    分析任务在 worker 侧必然 "No such file"，worker 登记的产物 api 侧 404。
    """

    CASES = [
        ("docker-compose.prod.yml", "/app/data", "webgis_data"),
        ("docker-compose.prod.secure.yml", "/app/data", "webgis_data"),
    ]

    def test_celery_mounts_same_data_dir_volume_as_api(self):
        for path, target, volume in self.CASES:
            compose = _load_compose(path)
            services = compose["services"]
            assert "celery-worker" in services, f"{path}: no celery-worker service"

            api_mounts = _named_volume_mounts(services["api"], volume)
            celery_mounts = _named_volume_mounts(services["celery-worker"], volume)
            assert api_mounts, f"{path}: api mounts no {volume} volume"
            assert celery_mounts, (
                f"{path}: celery-worker mounts no {volume} volume — celery tasks "
                "open uploaded files by local path in a separate container "
                "filesystem; unsized results/uploaded files would be invisible"
            )
            assert celery_mounts == api_mounts, (
                f"{path}: celery-worker {volume} mount {celery_mounts} != api "
                f"{api_mounts} — the worker would not see the api's data"
            )
            assert celery_mounts == [f"{volume}:{target}"], (
                f"{path}: expected {volume} volume at {target} "
                f"(the stack's DATA_DIR path), got {celery_mounts}"
            )

    def test_data_dir_env_matches_shared_mount_target(self):
        """DATA_DIR 必须等于共享卷挂载目标 —— 整棵子树才共享（否则产物落容器层）。"""
        for path, target, volume in self.CASES:
            compose = _load_compose(path)
            for svc in ("api", "celery-worker"):
                env = compose["services"][svc].get("environment", {})
                if isinstance(env, list):
                    env = dict(e.split("=", 1) for e in env if "=" in e)
                assert env.get("DATA_DIR") == target, (
                    f"{path} {svc}: DATA_DIR={env.get('DATA_DIR')!r} 必须等于共享"
                    f"卷挂载目标 {target}"
                )

    def test_data_dir_volume_declared(self):
        for path, _, volume in self.CASES:
            compose = _load_compose(path)
            assert volume in compose.get("volumes", {}), (
                f"{path}: top-level `{volume}` volume not declared"
            )

    def test_no_legacy_uploads_volume(self):
        """旧设计（uploads 命名卷挂 /app/uploads）已被共享数据卷取代 —— 残留
        会制造两个存储位置，上传/产物再次分裂。"""
        for path, _, volume in self.CASES:
            compose = _load_compose(path)
            assert "uploads" not in compose.get("volumes", {}), (
                f"{path}: 旧 `uploads` 卷残留 —— 应统一用 {volume}"
            )
            for svc in ("api", "celery-worker"):
                vols = "\n".join(
                    v if isinstance(v, str) else str(v)
                    for v in compose["services"][svc].get("volumes", [])
                )
                assert "/app/uploads" not in vols, (
                    f"{path} {svc}: 残留 /app/uploads 挂载 —— 应用不再写该路径"
                )

    def test_dev_celery_uploads_mount_present_textually(self):
        """docker-compose.yml 的 volumes 块含 `${WEBGIS_DEV_MOUNT:+...}` 可选
        展开行（审计 INF-003），yaml.safe_load 把整块折叠成 plain scalar 而非
        数组 —— 该文件按折叠后的文本断言（与 api 服务相同的既有结构）。"""
        compose = _load_compose("docker-compose.yml")
        for svc in ("api", "celery-worker"):
            volumes = compose["services"][svc]["volumes"]
            assert isinstance(volumes, str) and "- uploads:/app/data" in volumes, (
                f"docker-compose.yml {svc}: volumes={volumes!r} 缺少 "
                "`- uploads:/app/data` —— celery 必须与 api 共享 uploads 卷"
            )


class TestComposeDualProcessHealthcheck:
    """#474（#400 的 compose-override 兄弟项）：api 的 compose healthcheck
    不得退化成 api-only 探测。

    compose service healthcheck 会完全覆盖镜像 HEALTHCHECK。Dockerfile.prod
    （#400）的双进程 HEALTHCHECK 同时探测 uvicorn:8000 与 node:3000 —— 若
    compose 覆盖成只探 :8000，node 前端进程死亡时容器仍 "healthy"，nginx 对
    / 502 而编排层与 CI 探针毫无感知。
    """

    PROD_FILES = ["docker-compose.prod.yml", "docker-compose.prod.secure.yml"]

    def test_api_healthcheck_probes_both_processes(self):
        for path in self.PROD_FILES:
            compose = _load_compose(path)
            hc = compose["services"]["api"].get("healthcheck", {})
            test = hc.get("test", [])
            cmd = " ".join(test) if isinstance(test, list) else str(test)
            assert ":8000/api/v1/health/live" in cmd, (
                f"{path}: api healthcheck 未探测 uvicorn:8000 健康端点: {cmd}"
            )
            assert "localhost:3000" in cmd, (
                f"{path}: api healthcheck 只探测 uvicorn:8000 —— node 前端进程"
                "死亡时容器仍 healthy（覆盖了 #400 的镜像双进程 HEALTHCHECK）"
            )

    def test_compose_healthcheck_matches_image_healthcheck_contract(self):
        """compose 探测必须与 Dockerfile.prod 的镜像 HEALTHCHECK 同步（双端口）。"""
        with open("Dockerfile.prod") as f:
            lines = f.read().splitlines()
        idx = next(
            (i for i, ln in enumerate(lines) if ln.startswith("HEALTHCHECK")), None
        )
        assert idx is not None, "Dockerfile.prod has no HEALTHCHECK directive"
        # HEALTHCHECK 指令跨行（续行是 CMD python3 -c "..."）
        image_hc = "\n".join(lines[idx : idx + 2])
        assert ":8000" in image_hc and ":3000" in image_hc, (
            "Dockerfile.prod 镜像 HEALTHCHECK 不再是双进程探测？本测试的契约"
            "前提变了，需同步更新"
        )
        for path in self.PROD_FILES:
            compose = _load_compose(path)
            test = compose["services"]["api"]["healthcheck"]["test"]
            cmd = " ".join(test) if isinstance(test, list) else str(test)
            for probe in ("localhost:8000", "localhost:3000"):
                assert probe in cmd, (
                    f"{path}: compose healthcheck 缺少 {probe} 探测（与镜像 "
                    "HEALTHCHECK 契约不一致）"
                )


class TestProdDeployTransport:
    """#472: deploy-prod 的工件集（compose + redis 配置 + 镜像 tar + .env.Priv）
    必须能在一个干净主机上产出可用部署。

    - 镜像：api/celery 引用 ${WEBGIS_IMAGE:-...}（CI 写入 .env.Priv 解析为
      `docker load` 出的 ghcr.io/<repo>:<sha>），本地检出保留 build: 兜底。
    - 健康检查：api 端口发布到主机 loopback —— deploy-prod 在主机上
      curl http://localhost:8000，仅 expose 的端口对主机不可达。
    - nginx：配置/证书内联为 configs（见 test_nginx_security.py 的 parity）。
    """

    def test_secure_compose_references_ci_image(self):
        compose = _load_compose(DOCKER_COMPOSE_PROD_SECURE)
        for svc in ("api", "celery-worker"):
            image = compose["services"][svc].get("image", "")
            assert image.startswith("${WEBGIS_IMAGE:") and ":-" in image, (
                f"{DOCKER_COMPOSE_PROD_SECURE} {svc}: image={image!r} 未参数化 —— "
                "deploy-prod 主机上没有构建上下文，仅 build: 会让 compose 构建失败"
                "或静默复用过期本地镜像（CI 加载的 tar 从未被引用）"
            )

    def test_secure_compose_keeps_local_build_fallback(self):
        compose = _load_compose(DOCKER_COMPOSE_PROD_SECURE)
        for svc in ("api", "celery-worker"):
            assert "build" in compose["services"][svc], (
                f"{DOCKER_COMPOSE_PROD_SECURE} {svc}: 丢失 build: —— 本地检出"
                "（未设 WEBGIS_IMAGE）将无法构建镜像"
            )

    def test_secure_api_port_published_on_loopback(self):
        compose = _load_compose(DOCKER_COMPOSE_PROD_SECURE)
        api = compose["services"]["api"]
        assert "expose" not in api, (
            "api 仍用 expose（仅容器网络可见）—— 主机侧健康检查不可达"
        )
        ports = api.get("ports", [])
        assert any(
            str(p).startswith("127.0.0.1:") and str(p).endswith(":8000")
            for p in ports
        ), (
            f"api 必须把 8000 发布到主机 loopback（deploy-prod 在主机上探测），"
            f"且不得暴露到非 loopback 地址：ports={ports}"
        )
        for p in ports:
            assert str(p).startswith("127.0.0.1:"), f"端口暴露到网络: {p}"


class TestCiEnvPrivScript:
    """#472: deploy/ci-generate-env-priv.sh 必须把 CI 镜像 tag 写进 .env.Priv。"""

    SCRIPT = os.path.join(
        os.path.dirname(__file__), os.pardir, "deploy", "ci-generate-env-priv.sh"
    )

    def _run(self, tmp_path, extra_env):
        env = {
            "DB_PWD": "db",
            "REDIS_PASSWORD": "redis",
            "JWT_SECRET_KEY": "jwt",
            "LLM_API_KEY": "llm",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        env.update(extra_env)
        proc = subprocess.run(
            ["sh", self.SCRIPT],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"script failed: {proc.stderr}"
        return (tmp_path / ".env.Priv").read_text()

    def test_emits_webgis_image_from_github_env(self, tmp_path):
        content = self._run(
            tmp_path,
            {
                "GITHUB_REPOSITORY": "WindWang2/webgis-ai-agent",
                "GITHUB_SHA": "abc123def",
            },
        )
        # docker tag 要求全小写 —— 与 build job 的 Lowercase IMAGE_NAME 步骤一致
        assert "WEBGIS_IMAGE=ghcr.io/windwang2/webgis-ai-agent:abc123def" in content, (
            f".env.Priv 缺少与 docker load 出的 tar 同名的 WEBGIS_IMAGE: {content!r}"
        )

    def test_env_webgis_image_override_wins(self, tmp_path):
        content = self._run(
            tmp_path,
            {
                "GITHUB_REPOSITORY": "WindWang2/webgis-ai-agent",
                "GITHUB_SHA": "abc123def",
                "WEBGIS_IMAGE": "ghcr.io/windwang2/webgis-ai-agent:rollback",
            },
        )
        assert "WEBGIS_IMAGE=ghcr.io/windwang2/webgis-ai-agent:rollback" in content

    def test_no_webgis_image_outside_ci(self, tmp_path):
        content = self._run(tmp_path, {})
        assert "WEBGIS_IMAGE" not in content, (
            "非 CI 环境（无 GITHUB_SHA）不应写入 WEBGIS_IMAGE —— 本地走 "
            "${WEBGIS_IMAGE:-webgis-ai-agent:local} 的 build: 兜底"
        )
