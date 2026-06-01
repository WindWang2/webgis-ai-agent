"""Security: K8s deployments must have securityContext."""
import pytest
import yaml


def _load_k8s(path):
    with open(path) as f:
        return list(yaml.safe_load_all(f))


class TestK8sRedisAuth:
    def test_redis_has_requirepass(self):
        """K8s Redis must use --requirepass."""
        for doc in _load_k8s("deploy/k8s/05-deps-optional.yaml"):
            if doc and doc.get("kind") == "Deployment":
                containers = doc["spec"]["template"]["spec"].get("containers", [])
                for c in containers:
                    if c.get("name") == "redis":
                        cmd = c.get("command", [])
                        args = c.get("args", [])
                        all_args = cmd + args
                        assert any("requirepass" in a for a in all_args), (
                            f"Redis has no --requirepass. Command: {all_args}"
                        )


class TestK8sSecurityContext:
    def test_api_deployment_has_security_context(self):
        """API deployment must enforce runAsNonRoot."""
        for doc in _load_k8s("deploy/k8s/02-api-deployment.yaml"):
            if doc and doc.get("kind") == "Deployment":
                spec = doc["spec"]["template"]["spec"]
                assert "securityContext" in spec, "Pod-level securityContext missing"
                sc = spec["securityContext"]
                assert sc.get("runAsNonRoot") is True, "runAsNonRoot must be true"

    def test_celery_deployment_has_security_context(self):
        """Celery deployment must enforce runAsNonRoot."""
        for doc in _load_k8s("deploy/k8s/03-celery-deployment.yaml"):
            if doc and doc.get("kind") == "Deployment":
                spec = doc["spec"]["template"]["spec"]
                assert "securityContext" in spec, "Pod-level securityContext missing"
                sc = spec["securityContext"]
                assert sc.get("runAsNonRoot") is True, "runAsNonRoot must be true"
