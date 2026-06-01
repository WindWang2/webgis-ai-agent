"""Security: docker-compose files must follow secure defaults."""
import pytest
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
