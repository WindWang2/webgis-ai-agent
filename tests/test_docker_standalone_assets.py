"""#1706 防回归守卫：Next standalone 静态资源必须拷进 standalone 目录内。

``frontend/next.config.mjs:10`` 为 ``output: "standalone"``，该布局下
``.next/static`` 与 ``public`` **不在** standalone 目录里，需由 Dockerfile
显式拷到 ``<standalone>/.next/static`` 与 ``<standalone>/public`` ——
``server.js`` 相对自身目录解析这两处（Dockerfile:104 /
Dockerfile.prod:145 的 CMD 即以 ``frontend/.next/standalone/server.js``
为入口）。#1706 的错位（拷到 standalone 目录**之外**）构建期不报错、后端
``/health`` 仍全绿，但浏览器里所有 ``/_next/static/*`` 与 ``/public`` 资源
404，前端是无 JS/CSS 的空壳。

本文件是纯文本结构守卫（与 tests/test_docker_digests.py 同款）：只读两个
Dockerfile 的文本 + 正则，不依赖 Docker 守护进程、不发起网络请求。
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

DOCKERFILES = ("Dockerfile", "Dockerfile.prod")

# frontend-builder 阶段的三个 COPY 目的地（相对 runner WORKDIR）。
_STANDALONE = "./frontend/.next/standalone"
_STATIC = f"{_STANDALONE}/.next/static"
_PUBLIC = f"{_STANDALONE}/public"

_EXPECTED_DESTS = (_STANDALONE, _STATIC, _PUBLIC)


def _copy_lines(dockerfile: Path) -> list[tuple[int, str, str]]:
    """按行解析 Dockerfile，返回 (行号, 源路径, 目的路径) 列表。

    语法 ``COPY [--flag=…] <src> <dest>``：跳过 ``--`` 开头的 flag（
    ``--from=<stage>`` / ``--chown=…``），余下第一个 token 为源、第二个为
    目的地；指令名大小写不敏感（Docker 规范）。纯文本按行解析，不识别
    续行（仓库两个 Dockerfile 的 COPY 均为单行）。
    """
    copies: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(
        dockerfile.read_text(encoding="utf-8").splitlines(), start=1
    ):
        tokens = line.strip().split()
        if not tokens or tokens[0].upper() != "COPY":
            continue
        operands = [t for t in tokens[1:] if not t.startswith("--")]
        assert len(operands) >= 2, (
            f"{dockerfile.name}:{lineno}: COPY 行缺少源/目的地: {line!r}"
        )
        copies.append((lineno, operands[0], operands[1]))
    return copies


@pytest.mark.parametrize("dockerfile_name", DOCKERFILES)
def test_standalone_static_and_public_land_inside_standalone(dockerfile_name: str):
    """frontend-builder 的三个 COPY 目的地必须恰好是 issue #1706 的布局。"""
    path = REPO_ROOT / dockerfile_name
    assert path.exists(), f"{dockerfile_name} 不存在（守卫对象移动/改名时须同步本测试）"

    relevant = [
        (lineno, src, dest) for lineno, src, dest in _copy_lines(path)
        if src.startswith("/app/frontend/.next/")
        or src.startswith("/app/frontend/public")
    ]
    assert relevant, (
        f"{dockerfile_name} 未解析到 frontend-builder 的 standalone 产物 COPY"
        "（守卫解析器失效？）"
    )

    bad = [
        f"  L{lineno}: {src} -> {dest}"
        for lineno, src, dest in relevant
        if dest not in _EXPECTED_DESTS
    ]
    assert not bad, (
        f"{dockerfile_name} 的 standalone 产物 COPY 目的地不在 standalone 目录内"
        f"（#1706：server.js 相对自身目录解析 <standalone>/.next/static 与"
        f" <standalone>/public，摆错则静态资源全部 404）：\n" + "\n".join(bad)
        + f"\n期望目的地: {', '.join(_EXPECTED_DESTS)}"
    )

    # 三个目的地都要出现（少一个 = 对应资源在镜像里缺失）。
    dests = {dest for _, _, dest in relevant}
    missing = [d for d in _EXPECTED_DESTS if d not in dests]
    assert not missing, (
        f"{dockerfile_name} 缺少 standalone 产物 COPY（#1706 回归）：{missing}"
    )


@pytest.mark.parametrize("dockerfile_name", DOCKERFILES)
def test_no_copy_writes_static_or_public_outside_standalone(dockerfile_name: str):
    """反向守卫：任何把 static/public 落到 standalone 目录之外的 COPY 都算回退。"""
    path = REPO_ROOT / dockerfile_name
    assert path.exists(), f"{dockerfile_name} 不存在（守卫对象移动/改名时须同步本测试）"

    offenders = [
        f"  L{lineno}: {src} -> {dest}"
        for lineno, src, dest in _copy_lines(path)
        if (src.rstrip("/").endswith("/.next/static") or src.rstrip("/").endswith("/public"))
        and not dest.startswith(_STANDALONE)
    ]
    assert not offenders, (
        f"{dockerfile_name} 把静态资源拷到了 standalone 目录之外（#1706 的错位"
        f"回归）：\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("dockerfile_name", DOCKERFILES)
def test_build_time_assertion_present(dockerfile_name: str):
    """runner 阶段必须有构建期断言：摆错即构建失败，防「部署后第一眼才发现」。"""
    path = REPO_ROOT / dockerfile_name
    assert path.exists(), f"{dockerfile_name} 不存在（守卫对象移动/改名时须同步本测试）"

    text = path.read_text(encoding="utf-8")
    run_lines = [
        (lineno, line)
        for lineno, line in enumerate(text.splitlines(), start=1)
        if line.strip().split()[:1] and line.strip().split()[0].upper() == "RUN"
    ]
    assertions = [
        f"  L{lineno}: {line.strip()}"
        for lineno, line in run_lines
        if re.search(r"test\s+-d\s+frontend/\.next/standalone/\.next/static", line)
    ]
    assert assertions, (
        f"{dockerfile_name} 缺少构建期断言（#1706 建议方向 2）：runner 阶段应有\n"
        f"  RUN test -d frontend/.next/standalone/.next/static && "
        f"test -d frontend/.next/standalone/public\n"
        f"现有 RUN 行：{[lineno for lineno, _ in run_lines]}"
    )
