"""Issue #519: DATA_DIR 必须铺满整个部署矩阵（k8s + 两个 prod compose），
且 api 与 celery-worker 共享同一份 DATA_DIR 存储。

app/main.py:330-331 在 import 期对 settings.DATA_DIR 执行 os.makedirs
（config.py 默认 "./data"，相对 WORKDIR /app/backend 展开）。只设置 env 不够：
- k8s readOnlyRootFilesystem 容器若 DATA_DIR 指向只读 rootfs → import 期
  makedirs 崩溃 → CrashLoopBackOff；
- 即使设置了 DATA_DIR，若挂到 per-pod emptyDir / 各自容器层，celery worker 登记
  的 DATA_DIR 下绝对路径（analysis_results / monitoring_reports / uploads /
  exports）在 api 侧按自身文件系统解析 → 404，且 worker 重启即丢。

本文件守住三条契约：
  1. 应用侧：config.py 声明 DATA_DIR 字段（env 同名），main.py 对它 makedirs，
     并从 app 源码采集所有 DATA_DIR 产物子目录；
  2. k8s 侧：ConfigMap 设 DATA_DIR=/app/data；两个 Deployment（api + celery）
     都把同一共享 RWX PVC（webgis-uploads-pvc）挂到 DATA_DIR 父路径 —— 全部
     产物子目录跨 pod 可见，DATA_DIR 上不得是 per-pod emptyDir；
  3. compose 侧：两个 prod 栈的 api 与 celery-worker 设同一 DATA_DIR，且挂
     同一个共享命名卷（webgis_data）到该路径 —— 整棵子树共享。
"""
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = REPO_ROOT / "deploy" / "k8s"
APP_DIR = REPO_ROOT / "app"

# ── 1. 应用侧契约 ─────────────────────────────────────────────────────────

_ARTIFACT_RE = re.compile(
    r'os\.path\.join\(settings\.DATA_DIR,\s*"([A-Za-z0-9_]+)"\)'
    r"|Path\(settings\.DATA_DIR,\s*\"([A-Za-z0-9_]+)\"\)"
)


def _artifact_subdirs() -> set:
    """从 app 源码采集所有写进 DATA_DIR 的产物子目录（跨 worker/api 需共享）。"""
    subdirs = set()
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in _ARTIFACT_RE.finditer(text):
            name = m.group(1) or m.group(2)
            if name:
                subdirs.add(name)
    assert subdirs, "app/ 里竟然没有 DATA_DIR 子目录消费者？"
    return subdirs


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


def test_artifact_subdirs_are_known():
    """采集到的产物子目录必须包含上传/分析/报告/导出（防止误改后漏网）。"""
    subdirs = _artifact_subdirs()
    assert {"uploads", "analysis_results", "monitoring_reports", "exports"} <= subdirs, (
        f"DATA_DIR 产物子目录集合异常: {sorted(subdirs)}"
    )


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


def _data_dir_mount_info(dep: dict):
    """返回 (data_dir, volume_source_kind, volume_name) 或抛断言。"""
    pod = dep["spec"]["template"]["spec"]
    data_dir = _configmap_data()["DATA_DIR"]
    volumes = {v["name"]: v for v in pod.get("volumes", [])}
    mount_to_volume = {}
    for container in pod.get("containers", []):
        for m in container.get("volumeMounts", []):
            mount_to_volume[m["mountPath"]] = m["name"]
    assert data_dir in mount_to_volume, (
        f"{dep['metadata']['name']}: DATA_DIR={data_dir} 未挂载任何卷"
    )
    volume = volumes[mount_to_volume[data_dir]]
    return data_dir, volume


def test_k8s_data_dir_mounted_from_shared_pvc_on_both():
    """DATA_DIR 父路径必须来自共享 PVC（同一 claim 覆盖 api + celery）——
    per-pod emptyDir 会让 celery 写的产物 api pod 读不到、重启即丢。"""
    claims = {}
    for path, name in (
        (K8S_DIR / "02-api-deployment.yaml", "webgis-api"),
        (K8S_DIR / "03-celery-deployment.yaml", "webgis-celery"),
    ):
        data_dir, volume = _data_dir_mount_info(_deployment(path, name))
        assert "emptyDir" not in volume, (
            f"{name}: DATA_DIR={data_dir} 挂的是 per-pod emptyDir —— 跨 pod 不可见"
        )
        assert "persistentVolumeClaim" in volume, (
            f"{name}: DATA_DIR={data_dir} 必须来自 PVC"
        )
        claims[name] = volume["persistentVolumeClaim"]["claimName"]
    assert claims["webgis-api"] == claims["webgis-celery"] == "webgis-uploads-pvc", (
        f"api/celery 必须共享同一 webgis-uploads-pvc（got {claims}）"
    )


def test_k8s_artifact_subdirs_resolve_under_shared_mount():
    """每个产物子目录（uploads/analysis_results/monitoring_reports/exports）
    都必须落在共享挂载之下 —— 由 DATA_DIR 父路径挂载覆盖。"""
    data_dir = _configmap_data()["DATA_DIR"]
    for subdir in _artifact_subdirs():
        sub_path = data_dir.rstrip("/") + "/" + subdir
        assert sub_path.startswith(data_dir.rstrip("/") + "/"), sub_path
    # 父路径挂载已由 test_k8s_data_dir_mounted_from_shared_pvc_on_both 断言；
    # 这里再显式列出覆盖关系，防止未来把某个子目录单独移到 emptyDir。
    for path, name in (
        (K8S_DIR / "02-api-deployment.yaml", "webgis-api"),
        (K8S_DIR / "03-celery-deployment.yaml", "webgis-celery"),
    ):
        dep = _deployment(path, name)
        pod = dep["spec"]["template"]["spec"]
        volumes = {v["name"]: v for v in pod.get("volumes", [])}
        empty_dir_mounts = {
            m["mountPath"]
            for c in pod.get("containers", [])
            for m in c.get("volumeMounts", [])
            if "emptyDir" in volumes.get(m["name"], {})
        }
        for subdir in _artifact_subdirs():
            sub_path = data_dir.rstrip("/") + "/" + subdir
            assert not any(
                sub_path == m or sub_path.startswith(m.rstrip("/") + "/")
                for m in empty_dir_mounts
            ), f"{name}: 产物子目录 {sub_path} 被 emptyDir 挂载遮蔽"


# ── 3. compose 侧契约 ─────────────────────────────────────────────────────


def _compose(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))


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


def _named_volume_mounts(service: dict) -> list:
    """service 的命名卷挂载 [(source, target), ...]（排除 bind-mount）。"""
    out = []
    for v in service.get("volumes", []):
        if not isinstance(v, str) or ":" not in v:
            continue
        source, _, target = v.partition(":")
        if source and not source.startswith((".", "/")):
            out.append((source, target))
    return out


def test_prod_composes_share_data_dir_volume():
    """两个 prod 栈：api 与 celery-worker 设同一 DATA_DIR，且挂同一个共享
    命名卷（webgis_data）到该路径 —— 整棵 DATA_DIR 子树跨容器共享。"""
    for name in ("docker-compose.prod.yml", "docker-compose.prod.secure.yml"):
        compose = _compose(name)
        services = compose["services"]
        envs = {
            svc: _env_entries(services[svc].get("environment"))
            for svc in ("api", "celery-worker")
        }
        api_data = envs["api"].get("DATA_DIR")
        celery_data = envs["celery-worker"].get("DATA_DIR")
        assert celery_data, f"{name}: celery-worker 缺 DATA_DIR"
        assert api_data, f"{name}: api 缺 DATA_DIR"
        assert celery_data == api_data, (
            f"{name}: celery-worker DATA_DIR={celery_data!r} 与 api "
            f"DATA_DIR={api_data!r} 不一致"
        )

        api_mounts = _named_volume_mounts(services["api"])
        celery_mounts = _named_volume_mounts(services["celery-worker"])
        api_data_source = {tgt: src for src, tgt in api_mounts}.get(api_data)
        celery_data_source = {tgt: src for src, tgt in celery_mounts}.get(celery_data)
        assert api_data_source, (
            f"{name}: api 没有把任何命名卷挂到 DATA_DIR={api_data}"
        )
        assert celery_data_source, (
            f"{name}: celery-worker 没有把任何命名卷挂到 DATA_DIR={celery_data}"
        )
        assert api_data_source == celery_data_source, (
            f"{name}: api 与 celery-worker 的 DATA_DIR 挂载卷不一致 "
            f"(api={api_data_source} celery={celery_data_source})"
        )
        assert api_data_source in compose.get("volumes", {}), (
            f"{name}: 共享卷 {api_data_source} 未在顶层 volumes 声明"
        )


def test_prod_composes_artifact_subdirs_under_shared_mount():
    """每个产物子目录都必须落在共享挂载之下（DATA_DIR == 挂载目标即覆盖）。"""
    for name in ("docker-compose.prod.yml", "docker-compose.prod.secure.yml"):
        services = _compose(name)["services"]
        data_dir = _env_entries(services["api"].get("environment"))["DATA_DIR"]
        for subdir in _artifact_subdirs():
            sub_path = data_dir.rstrip("/") + "/" + subdir
            assert sub_path.startswith(data_dir.rstrip("/") + "/"), sub_path
        assert data_dir.rstrip("/") in {
            tgt for _, tgt in _named_volume_mounts(services["api"])
        } and data_dir.rstrip("/") in {
            tgt for _, tgt in _named_volume_mounts(services["celery-worker"])
        }, f"{name}: DATA_DIR={data_dir} 不是共享挂载目标 —— 产物落在容器层"


# ── 4. #760: MapSpec/raster durable store 写根契约 ────────────────────────

def test_mapspec_store_root_lives_under_data_dir():
    """#760: .webgis-agent 硬编码在 PROJECT_ROOT 下时游离于部署矩阵之外
    （k8s readOnlyRootFilesystem 首个 mutation 即 EROFS；compose 落容器层）。
    必须解析到 DATA_DIR（已挂共享卷）之下，或显式 MAPSPEC_STORAGE_DIR。"""
    import os
    from app.services.mapspec import store as mapspec_store_module

    explicit = os.environ.get("MAPSPEC_STORAGE_DIR")
    base = mapspec_store_module.BASE_STORAGE_DIR
    if explicit:
        assert str(base) == str(Path(explicit).resolve() / ".webgis-agent")
    else:
        data_dir = Path(mapspec_store_module.settings.DATA_DIR).resolve()
        assert base == data_dir / ".webgis-agent", (
            f"BASE_STORAGE_DIR={base} 必须位于 DATA_DIR={data_dir} 之下"
        )
        assert mapspec_store_module.PROJECT_ROOT not in base.parents or data_dir in base.parents
