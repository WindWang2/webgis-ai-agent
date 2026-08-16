"""Issue #519: DATA_DIR 必须铺满整个部署矩阵（k8s + 两个 prod compose）。

app/main.py:330-331 在 import 期对 settings.DATA_DIR 执行 os.makedirs
（config.py 默认 "./data"，相对 WORKDIR /app/backend 展开）。k8s 的
readOnlyRootFilesystem 容器没收到 DATA_DIR → import 期在只读 rootfs 上
makedirs → OSError → CrashLoopBackOff；prod compose 的 celery-worker 也没收到
→ worker 把 /app/backend/data/... 写进 DB，api 从 DATA_DIR=/app 下发 → 产物
跨容器 404 且 worker 重启即丢。

本文件守住三条契约：
  1. 应用侧：config.py 声明 DATA_DIR 字段（env 同名），main.py 对它 makedirs；
  2. k8s 侧：ConfigMap 设 DATA_DIR，且指向每个 readOnlyRootFilesystem 容器
     的可写挂载路径；uploads PVC 必须同时挂到 <DATA_DIR>/uploads（不落 emptyDir）；
  3. compose 侧：两个 prod 栈的 celery-worker 都必须设置与 api 相同的 DATA_DIR。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = REPO_ROOT / "deploy" / "k8s"


# ── 1. 应用侧契约 ─────────────────────────────────────────────────────────


def test_config_declares_data_dir_env_field():
    """config.py 必须声明 DATA_DIR 字段 —— pydantic BaseSettings 让同名环境
    变量（DATA_DIR=...）直接覆盖该默认值，这是部署矩阵唯一应使用的注入点。"""
    config = (REPO_ROOT / "app" / "core" / "config.py").read_text(encoding="utf-8")
    assert "DATA_DIR: str" in config, "config.py 必须声明 DATA_DIR 字段"


def test_main_creates_data_dir_at_import_time():
    """main.py 必须对 settings.DATA_DIR 做 import 期 makedirs —— 这是 k8s
    只读 rootfs 崩溃循环的直接触发点，部署配置必须保证该目录可写。"""
    main = (REPO_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "os.makedirs(settings.DATA_DIR" in main, (
        "main.py 必须 import 期创建 settings.DATA_DIR"
    )
    assert "if not os.path.exists(settings.DATA_DIR)" in main


# ── 2. k8s 侧契约 ─────────────────────────────────────────────────────────


def _k8s_docs(path: Path) -> list:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _configmap_data() -> dict:
    for doc in _k8s_docs(K8S_DIR / "01-configmap.yaml"):
        if doc.get("kind") == "ConfigMap":
            return doc.get("data", {})
    raise AssertionError("01-configmap.yaml 无 ConfigMap")


def _deployment(path: Path, name: str) -> dict:
    for doc in _k8s_docs(path):
        if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == name:
            return doc
    raise AssertionError(f"{path.name} 无 Deployment {name}")


def test_k8s_configmap_sets_data_dir():
    data = _configmap_data()
    assert "DATA_DIR" in data, "ConfigMap 必须提供 DATA_DIR（缺了容器退回 ./data）"
    assert data["DATA_DIR"].startswith("/"), (
        "k8s DATA_DIR 必须是绝对路径（相对路径解析到只读的 WORKDIR /app/backend）"
    )


def _app_container_data_dir_under_writable_mount(deployment: dict) -> None:
    """应用容器（readOnlyRootFilesystem 且跑 app 代码）必须：
    - envFrom configMapRef（从而拿到 ConfigMap 的 DATA_DIR）；
    - 该 DATA_DIR 解析到某 volumeMount 目标之下，且该卷不是只读源。"""
    pod = deployment["spec"]["template"]["spec"]
    data_dir = _configmap_data()["DATA_DIR"]
    volumes = {v["name"]: v for v in pod.get("volumes", [])}
    mount_paths = []
    for container in pod.get("containers", []):
        sc = container.get("securityContext", {})
        if not sc.get("readOnlyRootFilesystem"):
            continue
        env_from_names = [
            ef.get("configMapRef", {}).get("name")
            for ef in container.get("envFrom", [])
            if "configMapRef" in ef
        ]
        assert "webgis-config" in env_from_names, (
            f"{container['name']}: 应用容器必须 envFrom webgis-config（DATA_DIR 来源）"
        )
        for m in container.get("volumeMounts", []):
            mount_paths.append((container["name"], m["name"], m["mountPath"]))

    assert mount_paths, "readOnlyRootFilesystem 容器必须有可写挂载"
    covered = False
    for cname, vname, mpath in mount_paths:
        # DATA_DIR 必须挂在某个卷目标下（/app/data 的父路径是 /app/data 自身）
        if data_dir == mpath or data_dir.startswith(mpath.rstrip("/") + "/"):
            volume = volumes.get(vname, {})
            # 可写源：emptyDir / PVC（都不能是 hostPath 只读或 configMap）
            if "emptyDir" in volume or "persistentVolumeClaim" in volume:
                covered = True
                break
    assert covered, (
        f"DATA_DIR={data_dir} 未落在任何 emptyDir/PVC 挂载之下 —— 只读 rootfs 下"
        " import 期 makedirs 崩溃（mounts={mount_paths}）"
    )


def test_k8s_api_data_dir_under_writable_mount():
    _app_container_data_dir_under_writable_mount(
        _deployment(K8S_DIR / "02-api-deployment.yaml", "webgis-api")
    )


def test_k8s_celery_data_dir_under_writable_mount():
    _app_container_data_dir_under_writable_mount(
        _deployment(K8S_DIR / "03-celery-deployment.yaml", "webgis-celery")
    )


def test_k8s_uploads_pvc_mounted_under_data_dir():
    """DATA_DIR/uploads 不能落在 emptyDir —— 上传必须留在 webgis-uploads-pvc
    （api 与 celery 共享；emptyDir 会随 pod 重建消失）。"""
    data_dir = _configmap_data()["DATA_DIR"]
    expected = data_dir.rstrip("/") + "/uploads"
    for path, name in (
        (K8S_DIR / "02-api-deployment.yaml", "webgis-api"),
        (K8S_DIR / "03-celery-deployment.yaml", "webgis-celery"),
    ):
        dep = _deployment(path, name)
        pod = dep["spec"]["template"]["spec"]
        volumes = {v["name"]: v for v in pod.get("volumes", [])}
        mounts = []
        for container in pod.get("containers", []):
            mounts += container.get("volumeMounts", [])
        seen = {m["mountPath"]: volumes.get(m["name"], {}).get("persistentVolumeClaim", {}).get("claimName") for m in mounts}
        assert seen.get(expected) == "webgis-uploads-pvc", (
            f"{name}: 缺 {expected} → webgis-uploads-pvc 挂载（上传会落 emptyDir，"
            f"pod 重建即丢；mounts={seen}）"
        )


# ── 3. compose 侧契约 ─────────────────────────────────────────────────────


def _compose_services(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))["services"]


def _env_entries(env) -> dict:
    """Normalize compose environment（list-form 或 map-form）为 {KEY: value}。"""
    if not env:
        return {}
    if isinstance(env, dict):
        return {k: v for k, v in env.items()}
    out = {}
    for e in env:
        key, _, value = str(e).partition("=")
        out[key.strip()] = value
    return out


def test_prod_composes_celery_data_dir_matches_api():
    """两个 prod 栈的 celery-worker 都必须设 DATA_DIR，且与 api 相同 ——
    worker 侧写路径与 api 侧下发路径必须一致，否则产物跨容器 404。"""
    for name in ("docker-compose.prod.yml", "docker-compose.prod.secure.yml"):
        services = _compose_services(name)
        api_data = _env_entries(services["api"].get("environment")).get("DATA_DIR")
        celery_data = _env_entries(
            services["celery-worker"].get("environment")
        ).get("DATA_DIR")
        assert celery_data, f"{name}: celery-worker 缺 DATA_DIR"
        assert api_data, f"{name}: api 缺 DATA_DIR"
        assert celery_data == api_data, (
            f"{name}: celery-worker DATA_DIR={celery_data!r} 与 api DATA_DIR="
            f"{api_data!r} 不一致"
        )
