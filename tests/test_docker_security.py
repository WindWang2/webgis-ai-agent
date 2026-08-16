"""Security: docker-compose files must follow secure defaults."""
import yaml


DOCKER_COMPOSE = "docker-compose.yml"
DOCKER_COMPOSE_PROD = "docker-compose.prod.yml"


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


def _uploads_mounts(service):
    """The `uploads:` named-volume mounts (target paths) of a compose service."""
    return [
        v
        for v in service.get("volumes", [])
        if isinstance(v, str) and v.startswith("uploads:")
    ]


class TestComposeUploadsVolumeParity:
    """#471（#394 的 compose 兄弟项）：每个 compose 栈里 celery-worker 都必须
    与 api 挂同一个 uploads 卷。

    celery 任务按本地路径打开 api 写入的上传文件（spatial_tasks.py 的
    run_ndvi_analysis → rasterio.open(raster_path)）。worker 在独立容器文件
    系统层里 —— 不挂同一个卷，上传成功的 raster/shapefile 分析任务在 worker
    侧必然 "No such file"。#394 已为 k8s 修复（celery deployment 挂同一 PVC），
    这里守住 compose 路径。
    """

    CASES = [
        # docker-compose.yml 的 volumes 是 `${WEBGIS_DEV_MOUNT:+...}` 可选展开
        # 块（审计 INF-003），yaml.safe_load 折叠成 plain scalar —— 由独立的
        # 文本断言用例覆盖，见 test_dev_celery_uploads_mount_present_textually。
        ("docker-compose.prod.yml", "/app/uploads"),
        ("docker-compose.prod.secure.yml", "/app/uploads"),
    ]

    def test_celery_mounts_same_uploads_volume_as_api(self):
        for path, target in self.CASES:
            compose = _load_compose(path)
            services = compose["services"]
            assert "celery-worker" in services, f"{path}: no celery-worker service"

            api_mounts = _uploads_mounts(services["api"])
            celery_mounts = _uploads_mounts(services["celery-worker"])
            assert api_mounts, f"{path}: api mounts no uploads volume"
            assert celery_mounts, (
                f"{path}: celery-worker mounts no uploads volume — celery tasks "
                "open uploaded files by local path in a separate container "
                "filesystem; uploaded-raster analysis fails with 'No such file'"
            )
            assert celery_mounts == api_mounts, (
                f"{path}: celery-worker uploads mount {celery_mounts} != api "
                f"{api_mounts} — the worker would not see the api's uploads"
            )
            assert celery_mounts == [f"uploads:{target}"], (
                f"{path}: expected uploads volume at {target} "
                f"(the stack's DATA_DIR uploads path), got {celery_mounts}"
            )

    def test_uploads_volume_declared(self):
        for path, _ in self.CASES:
            compose = _load_compose(path)
            assert "uploads" in compose.get("volumes", {}), (
                f"{path}: top-level `uploads` volume not declared"
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
