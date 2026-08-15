"""Security: K8s deployments must have securityContext."""
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


def _deployment_volume_sources(path, mount_path):
    """Map each Deployment's container volumeMount at mount_path to its
    underlying volume source (pvc claimName / emptyDir marker)."""
    out = {}
    for doc in _load_k8s(path):
        if not doc or doc.get("kind") != "Deployment":
            continue
        spec = doc["spec"]["template"]["spec"]
        volumes = {v["name"]: v for v in spec.get("volumes", [])}
        for c in spec.get("containers", []):
            for m in c.get("volumeMounts", []):
                if m.get("mountPath") == mount_path:
                    v = volumes.get(m["name"], {})
                    out[c["name"]] = (
                        v.get("persistentVolumeClaim", {}).get("claimName")
                        or ("emptyDir" if "emptyDir" in v else None)
                    )
    return out


class TestK8sUploadsVolumeParity:
    def test_api_and_celery_share_the_uploads_pvc(self):
        """#394: api 与 celery 的 /app/uploads 必须来自同一个 PVC。

        celery 挂 emptyDir 会让 worker 看到每 pod 各自的空目录 ——
        API 写入的上传文件（shapefile/raster 任务）在 worker 侧不存在，
        "compose 下正常、k8s 下失败"。
        """
        api = _deployment_volume_sources("deploy/k8s/02-api-deployment.yaml", "/app/uploads")
        celery = _deployment_volume_sources("deploy/k8s/03-celery-deployment.yaml", "/app/uploads")
        assert api, "api deployment mounts no /app/uploads volume"
        assert celery, "celery deployment mounts no /app/uploads volume"
        claim = set(api.values()) | set(celery.values())
        assert claim == {"webgis-uploads-pvc"}, (
            f"/app/uploads must be the shared webgis-uploads-pvc on both "
            f"deployments (got api={api}, celery={celery})"
        )

    def test_uploads_pvc_allows_multi_pod_access(self):
        """上传 PVC 需 ReadWriteMany（或至少声明多 pod 共享意图）。"""
        for path in ("deploy/k8s/02-api-deployment.yaml", "deploy/k8s/03-celery-deployment.yaml"):
            for doc in _load_k8s(path):
                if not doc or doc.get("kind") != "PersistentVolumeClaim":
                    continue
                if doc["metadata"]["name"] != "webgis-uploads-pvc":
                    continue
                modes = doc["spec"]["accessModes"]
                assert "ReadWriteMany" in modes, (
                    f"{path}: webgis-uploads-pvc accessModes={modes} 缺 ReadWriteMany"
                )


class TestK8sResumeStickyRouting:
    """#377: SSE turn-resume buffers are process-local (event_resume.py), so
    k8s must route a client's reconnect back to the pod that served the turn.
    Without sticky routing, ~50% of resumes (replicas: 2, plain ClusterIP)
    land on a replica that never saw the turn and fail with
    ``error {resumed: false}`` — the client stops auto-retrying per contract.
    """

    def test_api_service_has_session_affinity(self):
        """The api Service must pin clients to one pod (sessionAffinity:
        ClientIP). This is the WEAK fix (probability reduction); the STRONG
        fix — an externalized resume store — is tracked separately."""
        services = [
            doc for doc in _load_k8s("deploy/k8s/02-api-deployment.yaml")
            if doc and doc.get("kind") == "Service"
        ]
        assert services, "no Service found in 02-api-deployment.yaml"
        for doc in services:
            if doc["metadata"]["name"] == "webgis-api-service":
                assert doc["spec"].get("sessionAffinity") == "ClientIP", (
                    "webgis-api-service must set sessionAffinity: ClientIP — "
                    "SSE turn-resume buffers are process-local and a reconnect "
                    "must land on the pod that served the turn"
                )
                return
        raise AssertionError(
            "webgis-api-service not found in deploy/k8s/02-api-deployment.yaml"
        )

    def test_ingress_has_sticky_affinity_annotation(self):
        """The nginx ingress must pin each client to one backend with a cookie
        so SSE reconnects (Last-Event-ID resumes) reach the serving pod."""
        ingresses = [
            doc for doc in _load_k8s("deploy/k8s/04-ingress.yaml")
            if doc and doc.get("kind") == "Ingress"
        ]
        assert ingresses, "no Ingress found in 04-ingress.yaml"
        for doc in ingresses:
            ann = doc["metadata"].get("annotations", {})
            assert ann.get("nginx.ingress.kubernetes.io/affinity") == "cookie", (
                "webgis-ingress must set nginx.ingress.kubernetes.io/affinity: "
                "cookie — SSE turn-resume buffers are process-local"
            )
            assert "nginx.ingress.kubernetes.io/session-cookie-name" in ann, (
                "webgis-ingress must name its session affinity cookie"
            )
