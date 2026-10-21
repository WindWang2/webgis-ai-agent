"""#1750-3: 开发/生产镜像 runner 的 Node 运行时契约（PLT-03）。

Debian bookworm 的 ``nodejs`` apt 包是 18.x，而 frontend 打包的是
Next 16 standalone server（frontend/package.json next 16.4.0，要求
node >=20.9）—— runner 阶段 ``apt-get install nodejs`` 会让默认 CMD
里的 node 进程起不来。既定方案（Dockerfile.prod 首创）是从官方
``node:*-bookworm-slim`` 阶段只复制 node 单二进制（与 python slim
runner 同 glibc 基线；alpine 的 musl 二进制不可用）。

本文件是纯文本结构守卫（与 test_docker_digests.py 同款，不依赖
Docker 守护进程）：runner 阶段不得 apt 装 nodejs、必须从
node-runtime 阶段 COPY node 单二进制、node-runtime 阶段必须 digest
pin 到 bookworm 基线的官方 node 镜像。
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

DOCKERFILES = ("Dockerfile", "Dockerfile.prod")

_DIGEST_PINNED = re.compile(r"^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$")


def _stages(dockerfile: Path) -> dict[str, str]:
    """按 FROM 切分阶段：{stage alias/name: 该阶段的指令文本（去注释）}。"""
    stages: dict[str, list[str]] = {}
    current: str | None = None
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FROM "):
            tokens = stripped.split()
            alias = tokens[-1] if len(tokens) > 2 and tokens[-2].upper() == "AS" else tokens[1]
            current = alias
            stages.setdefault(current, [])
        elif current is not None and not stripped.startswith("#"):
            stages[current].append(stripped)
    return {name: "\n".join(lines) for name, lines in stages.items()}


@pytest.mark.parametrize("dockerfile_name", DOCKERFILES)
def test_runner_stage_does_not_apt_install_nodejs(dockerfile_name: str):
    """runner 不得再 apt 装 bookworm 的 nodejs（18.x，Next 16 起不来）。"""
    stages = _stages(REPO_ROOT / dockerfile_name)
    assert "runner" in stages, f"{dockerfile_name}: 找不到 runner 阶段"
    tokens = stages["runner"].split()
    assert "nodejs" not in tokens, (
        f"{dockerfile_name} runner 阶段仍在 apt 安装 bookworm 的 nodejs "
        "（18.x）——Next 16 standalone server 要求 node >=20.9（#1750/PLT-03）。"
        "请改用官方 node:*-bookworm-slim 单二进制（见 node-runtime 阶段）。"
    )


@pytest.mark.parametrize("dockerfile_name", DOCKERFILES)
def test_runner_stage_copies_official_node_binary(dockerfile_name: str):
    """runner 必须从 node-runtime 阶段 COPY node 单二进制。"""
    stages = _stages(REPO_ROOT / dockerfile_name)
    assert "node-runtime" in stages, (
        f"{dockerfile_name}: 缺少 node-runtime 阶段（官方 node 单二进制来源）"
    )
    normalized = " ".join(stages["runner"].split())
    assert (
        "COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node"
        in normalized
    ), (
        f"{dockerfile_name} runner 未从 node-runtime 复制 node 单二进制"
    )


@pytest.mark.parametrize("dockerfile_name", DOCKERFILES)
def test_node_runtime_stage_is_digest_pinned_bookworm(dockerfile_name: str):
    """node-runtime 必须 digest pin 到 bookworm 基线的官方 node 镜像
    （与 python slim runner 同 glibc；alpine musl 二进制不可用）。"""
    path = REPO_ROOT / dockerfile_name
    refs = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped.upper().startswith("FROM "):
            continue
        tokens = stripped.split()
        alias = tokens[-1] if len(tokens) > 2 and tokens[-2].upper() == "AS" else None
        if alias == "node-runtime":
            image = [t for t in tokens[1:] if not t.startswith("--") and t.upper() != "AS"][0]
            refs.append((lineno, image))
    assert refs, f"{dockerfile_name}: 未解析到 node-runtime 阶段的 FROM 行"
    for lineno, ref in refs:
        assert _DIGEST_PINNED.match(ref), (
            f"{dockerfile_name}:{lineno}: node-runtime 镜像未 pin digest: {ref}"
        )
        assert ref.startswith("node:") and "bookworm-slim" in ref, (
            f"{dockerfile_name}:{lineno}: node-runtime 必须是 bookworm 基线的"
            f"官方 node 镜像（glibc 兼容 python slim runner）: {ref}"
        )


def test_frontend_next_version_requires_node20_plus():
    """版本前提自证：打包进镜像的是 Next 16（要求 node >=20.9），
    这就是 bookworm apt nodejs(18) 不够用的根因。"""
    import json

    pkg = json.loads(
        (REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    next_major = int(pkg["dependencies"]["next"].split(".")[0])
    assert next_major >= 16, (
        f"frontend next 主版本为 {next_major} —— node 运行时前提变化时"
        "请复核本契约（#1750 的版本锚点）"
    )
