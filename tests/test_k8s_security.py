"""Security: K8s deployments must have securityContext."""
import yaml


def _load_k8s(path):
    with open(path, encoding="utf-8") as f:
        return list(yaml.safe_load_all(f))


class TestK8sRedisAuth:
    def test_redis_has_requirepass(self):
        """K8s Redis 必须启用认证，且口令不得进 argv（#1676 / SEC-04）。

        原断言只查 args 含 requirepass —— 而 `--requirepass $(REDIS_PASSWORD)`
        作为独立 argv 元素时，kubelet 在容器启动时把 secret 值展开进进程
        命令行：`kubectl describe`、节点 `/proc/<pid>/cmdline` 可见明文。现在
        由 sh -c 把 requirepass 写进 /dev/shm tmpfs 的 0400 运行时 conf 后
        exec redis-server（配置指令，非 argv 标志）。
        """
        for doc in _load_k8s("deploy/k8s/05-deps-optional.yaml"):
            if doc and doc.get("kind") == "Deployment":
                containers = doc["spec"]["template"]["spec"].get("containers", [])
                for c in containers:
                    if c.get("name") == "redis":
                        all_args = (c.get("command") or []) + (c.get("args") or [])
                        assert "--requirepass" not in all_args, (
                            f"Redis 口令进 argv（k8s 进程列表可见明文，#1676）: {all_args}"
                        )
                        assert any("requirepass" in a for a in all_args), (
                            f"Redis has no requirepass. Command: {all_args}"
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


class TestK8sOptionalDepsHardening:
    """#1636：05-deps-optional 的可选内部 postgres/redis 与 02/03 对齐 ——
    pod 级非 Root 声明；pg livenessProbe 不得硬编码用户名（POSTGRES_USER
    来自 webgis-secret 的 DB_USER，允许任意值）。"""

    def test_optional_deps_pods_run_as_non_root(self):
        for doc in _load_k8s("deploy/k8s/05-deps-optional.yaml"):
            if not doc or doc.get("kind") != "Deployment":
                continue
            name = doc["metadata"]["name"]
            sc = doc["spec"]["template"]["spec"].get("securityContext", {})
            assert sc.get("runAsNonRoot") is True, (
                f"{name}: 缺 pod 级 runAsNonRoot（对齐 02/03 的 pod 级安全上下文）"
            )
            assert isinstance(sc.get("runAsUser"), int) and sc["runAsUser"] > 0, (
                f"{name}: 缺 pod 级 runAsUser"
            )
            assert isinstance(sc.get("fsGroup"), int) and sc["fsGroup"] > 0, (
                f"{name}: 缺 pod 级 fsGroup（PVC 属主需对运行用户可写）"
            )

    def test_postgres_probe_does_not_hardcode_user(self):
        for doc in _load_k8s("deploy/k8s/05-deps-optional.yaml"):
            if not doc or doc.get("kind") != "Deployment":
                continue
            for c in doc["spec"]["template"]["spec"].get("containers", []):
                if c.get("name") != "postgres":
                    continue
                probe = c.get("livenessProbe", {})
                cmd = " ".join(probe.get("exec", {}).get("command", []))
                assert "webgis_prod" not in cmd, (
                    "pg livenessProbe 硬编码 webgis_prod —— POSTGRES_USER 来自 "
                    "webgis-secret 的 DB_USER（允许任意值），探针与配置脱节"
                )
                assert "$POSTGRES_USER" in cmd, (
                    "pg livenessProbe 必须经 sh -c 展开容器环境变量 POSTGRES_USER"
                    "（kubelet 不展开 exec 探针里的 $(VAR)）"
                )


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


class TestK8sIngressSSETransport:
    """#475: k8s ingress 的 SSE 传输行为必须与 compose nginx 契约一致。

    deploy/nginx/nginx.conf（deploy-config CI 门用 nginx -t 校验的传输层）对
    两个流式 location（/api/v1/chat/stream、/api/v1/explorer/stream/）设置
    proxy_buffering off + proxy_read/send_timeout 3600s。ingress 缺少等价配置
    时，k8s 部署得到的是被缓冲的 SSE（延迟尖峰 + chunk 突发）和 300s 硬切断
    —— 前端被迫走 Last-Event-ID 重连，压测 #377 的进程本地 resume 注册表。
    """

    def _ingress_annotations(self):
        ingresses = [
            doc for doc in _load_k8s("deploy/k8s/04-ingress.yaml")
            if doc and doc.get("kind") == "Ingress"
        ]
        assert ingresses, "no Ingress found in 04-ingress.yaml"
        ann = ingresses[0]["metadata"].get("annotations", {})
        assert "nginx.ingress.kubernetes.io" in " ".join(ann) or ann, (
            "webgis-ingress has no annotations"
        )
        return ann

    def test_ingress_disables_proxy_buffering(self):
        ann = self._ingress_annotations()
        assert ann.get("nginx.ingress.kubernetes.io/proxy-buffering") == "off", (
            "webgis-ingress 必须 proxy-buffering: off —— ingress-nginx 默认缓冲"
            "会把 SSE chunk 攒批下发（compose nginx 契约是 proxy_buffering off）"
        )

    def test_ingress_read_timeout_not_cutting_sse_turns(self):
        ann = self._ingress_annotations()
        for key in ("proxy-read-timeout", "proxy-send-timeout"):
            value = ann.get(f"nginx.ingress.kubernetes.io/{key}", "0")
            assert int(value) >= 3600, (
                f"webgis-ingress {key}={value} 会在多分钟工具链轮次中途切断 "
                "SSE（compose nginx 流式 location 契约是 3600s）"
            )

    def test_compose_nginx_sse_contract_still_holds(self):
        """契约源头：compose nginx.conf 的流式 location 必须保持不缓冲 +
        3600s —— ingress 注解与之对齐（防止一边漂移后另一边失去参照）。"""
        with open("deploy/nginx/nginx.conf", encoding="utf-8") as f:
            conf = f.read()
        assert "proxy_buffering off;" in conf, (
            "deploy/nginx/nginx.conf 丢失 SSE 不缓冲契约（proxy_buffering off）"
        )
        for stream_loc in ("location = /api/v1/chat/stream", "~ ^/api/v1/explorer/stream/"):
            assert stream_loc in conf, (
                f"deploy/nginx/nginx.conf 缺少 SSE location: {stream_loc}"
            )
            # 每个 SSE location 块内都有 3600s 读超时
            block = conf.split(stream_loc, 1)[1].split("location", 1)[0]
            assert "proxy_read_timeout 3600s;" in block, (
                f"{stream_loc}: 丢失 proxy_read_timeout 3600s 契约"
            )
