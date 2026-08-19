"""Issue #559: kustomize images 转换器必须能匹配 Deployment 镜像，且 newName
必须指向 CI 真正推送的 registry 坐标。

修复前 kustomization.yaml 写成 `name: webgis-prod:latest`（name 带 tag）——
kustomize 的 images 转换器按「仓库部分」匹配，Deployment 里是
webgis-prod:v0.1.2（tag 不同且仓库名也从未是 webgis-prod:latest），转换器
永不命中；newName 又是占位符 your-registry.com —— 没有任何 pipeline 推送过
该坐标，imagePullPolicy: Always 保证任何真实集群 ImagePullBackOff。

本文件守住：
  1. images[].name 不得含 tag/digest（新 kustomize 直接拒绝 name 带 tag）；
  2. images[].name 与 02/03 Deployment 里所有应用镜像的仓库部分一致（匹配
     契约成立）；
  3. newName 是 ghcr.io 上 CI 实际推送的坐标（production.yml build job 推送
     ghcr.io/<lowercased repo>:<sha>），而非占位符。
"""
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = REPO_ROOT / "deploy" / "k8s"


def _kustomization() -> dict:
    return yaml.safe_load((K8S_DIR / "kustomization.yaml").read_text(encoding="utf-8"))


def _image_repo(image: str) -> str:
    """ghcr.io/x/y:v1 → ghcr.io/x/y；webgis-prod:v0.1.2 → webgis-prod。"""
    return image.split("@")[0].split(":")[0]


def _deployment_images() -> set:
    """02/03 Deployment 里所有应用镜像（含 initContainer）的仓库部分。"""
    repos = set()
    for fname in ("02-api-deployment.yaml", "03-celery-deployment.yaml"):
        docs = [
            d
            for d in yaml.safe_load_all((K8S_DIR / fname).read_text(encoding="utf-8"))
            if d
        ]
        for doc in docs:
            if doc.get("kind") != "Deployment":
                continue
            pod = doc["spec"]["template"]["spec"]
            containers = pod.get("containers", []) + pod.get("initContainers", [])
            for c in containers:
                repos.add(_image_repo(c["image"]))
    return repos


def test_kustomize_image_names_have_no_tag_or_digest():
    kust = _kustomization()
    images = kust.get("images", [])
    assert images, "kustomization 必须声明 images 转换器"
    for img in images:
        assert ":" not in img["name"] and "@" not in img["name"], (
            f"images[].name={img['name']!r} 含 tag/digest —— kustomize 按仓库部分"
            "匹配（且新版本拒绝 name 带 tag），必须去掉"
        )


def test_kustomize_image_name_matches_deployment_images():
    kust = _kustomization()
    names = {img["name"] for img in kust.get("images", [])}
    dep_repos = _deployment_images()
    assert dep_repos, "02/03 Deployment 里竟然没有应用镜像？"
    assert names == dep_repos, (
        f"images[].name={sorted(names)} 与 Deployment 镜像仓库部分 "
        f"{sorted(dep_repos)} 不一致 —— 转换器匹配不到任何资源"
    )


def test_kustomize_new_name_is_ci_published_coordinate():
    kust = _kustomization()
    for img in kust.get("images", []):
        new_name = img.get("newName", "")
        assert new_name, f"images[].name={img['name']} 缺 newName"
        assert "your-registry.com" not in new_name, (
            f"newName={new_name!r} 仍是占位符 —— CI 只推送 ghcr.io 坐标"
        )
        assert new_name.startswith("ghcr.io/"), (
            f"newName={new_name!r} 必须指向 CI 推送的 ghcr.io registry"
            "（production.yml env.REGISTRY=ghcr.io）"
        )
        assert new_name == new_name.lower(), (
            f"newName={new_name!r} 必须小写（docker tag 要求全小写）"
        )


def test_default_image_tag_matches_ci_branch_tag():
    """#618-37: Deployment 默认 tag 不得再是从未被 CI 推送的 `v0.1.2`。

    production.yml build job 推送 ``<full sha>``；metadata-action 还声明
    ``type=ref,event=branch``（``master``）与无 ``v`` 前缀的 semver。
    清单默认用 ``master`` 作为 raw-apply 回退；生产必须 kustomize pin sha。
    """
    for fname in ("02-api-deployment.yaml", "03-celery-deployment.yaml"):
        docs = [
            d
            for d in yaml.safe_load_all((K8S_DIR / fname).read_text(encoding="utf-8"))
            if d
        ]
        for doc in docs:
            if doc.get("kind") != "Deployment":
                continue
            pod = doc["spec"]["template"]["spec"]
            containers = pod.get("containers", []) + pod.get("initContainers", [])
            for c in containers:
                image = c.get("image", "")
                tag = image.rsplit(":", 1)[-1]
                assert tag != "v0.1.2", (
                    f"{fname} {c.get('name')}: tag v0.1.2 与 CI 约定不符"
                )
                assert not tag.startswith("v"), (
                    f"{fname} {c.get('name')}: CI semver 无 v 前缀，默认 tag="
                    f"{tag!r}"
                )
                assert tag == "master", (
                    f"{fname} {c.get('name')}: 默认 tag 应为 master "
                    f"（CI branch tag），实际 {tag!r}。部署时再钉 sha。"
                )


def test_no_placeholder_coordinates_left_in_manifests():
    """整个 k8s 树不允许残留占位 registry / 未转换的本地镜像坐标。
    只检查 YAML 指令形态（image: 字段值），注释里对旧值的解释不算。"""
    placeholder = "your-registry.com"
    for path in sorted(K8S_DIR.glob("*.yaml")):
        docs = [
            d
            for d in yaml.safe_load_all(path.read_text(encoding="utf-8"))
            if d
        ]
        for doc in docs:
            if doc.get("kind") not in (
                "Deployment",
                "StatefulSet",
                "DaemonSet",
                "Job",
                "CronJob",
            ):
                continue
            pod = doc["spec"]["template"]["spec"]
            containers = pod.get("containers", []) + pod.get("initContainers", [])
            for c in containers:
                assert placeholder not in c.get("image", ""), (
                    f"{path.name}: 容器镜像 {c['image']!r} 仍是占位 registry"
                )
                assert not re.search(r"^webgis-prod:", c.get("image", "")), (
                    f"{path.name}: 容器镜像 {c['image']!r} 仍是未转换的本地坐标"
                    "（应为 ghcr.io/... 仓库）"
                )
