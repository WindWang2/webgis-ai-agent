"""#1377 延期项（#1349 ISSUE-036）收尾：Docker 基础镜像 digest pin 防回归守护。

ISSUE-036「基础镜像浮动 tag 未 pin digest」的主体已在 master 完成：
Dockerfile 与 Dockerfile.prod 的全部 FROM 行均 pin 到 ``name:tag@sha256:<64hex>``。
本文件是纯文本结构守卫（与 test_deploy_migration_wiring.py 的结构断言同款）：
任何 FROM 在后续 bump / 重写中回退到浮动 tag（丢失 digest）时测试必须失败。

设计约束：
  - 纯文本解析：只读两个 Dockerfile 的文本 + 正则，不依赖 Docker 守护进程、
    不发起网络请求（不做「digest 是否真实存在」的远端校验 —— 那是构建期
    pull 的职责，本守卫只锁「必须 pin」这一仓库约定）；
  - 全部 FROM 一律要求 ``name:tag@sha256:<64hex>``，无白名单：将来若确需
    不可 pin 的基础镜像（如 ``scratch``），必须显式修改本测试 —— 是有意识
    的决定，而不是默认放过；
  - ``FROM --platform=…`` flag 与多阶段 ``AS <alias>`` 不影响镜像引用解析；
    纯文本按行解析，不识别续行（仓库两个 Dockerfile 无跨行 FROM）。
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

DOCKERFILES = ("Dockerfile", "Dockerfile.prod")

# 镜像引用必须形如 name:tag@sha256:<64 位 hex>：显式 tag + 64 hex digest 收尾
# （OCI distribution 规范的 sha256 digest 为小写 hex，混写按未 pin 处理）。
_DIGEST_PINNED = re.compile(r"^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$")


def _from_image_refs(dockerfile: Path) -> list[tuple[int, str]]:
    """按行解析 Dockerfile，返回 (行号, 镜像引用) 列表。

    语法 ``FROM [--flag] <image> [AS <alias>]``：跳过 ``--`` 开头的 flag，
    取第一个非 flag token 为镜像引用；指令名大小写不敏感（Docker 规范）。
    """
    refs: list[tuple[int, str]] = []
    for lineno, line in enumerate(
        dockerfile.read_text(encoding="utf-8").splitlines(), start=1
    ):
        tokens = line.strip().split()
        if not tokens or tokens[0].upper() != "FROM":
            continue
        image_tokens = [t for t in tokens[1:] if not t.startswith("--")]
        assert image_tokens, f"{dockerfile.name}:{lineno}: FROM 行缺少镜像引用: {line!r}"
        refs.append((lineno, image_tokens[0]))
    return refs


@pytest.mark.parametrize("dockerfile_name", DOCKERFILES)
def test_all_from_lines_are_digest_pinned(dockerfile_name: str):
    path = REPO_ROOT / dockerfile_name
    assert path.exists(), f"{dockerfile_name} 不存在（守卫对象移动/改名时须同步本测试）"
    refs = _from_image_refs(path)
    assert refs, f"{dockerfile_name} 未解析到任何 FROM 行（守卫解析器失效？）"

    floating = [
        f"  L{lineno}: {ref}" for lineno, ref in refs if not _DIGEST_PINNED.match(ref)
    ]
    assert not floating, (
        f"{dockerfile_name} 存在未 pin digest 的 FROM 行"
        f"（#1377/ISSUE-036 防回归，浮动 tag 不可复现构建）：\n" + "\n".join(floating)
    )
