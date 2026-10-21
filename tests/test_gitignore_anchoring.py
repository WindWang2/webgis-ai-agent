"""#1677：.gitignore 不得用未锚定/父目录规则静默吞掉新增代码。

四宗罪（均已用 `git check-ignore -v` 实体探针复现，修复前全部见红）：

1. 未锚定 ``data/`` 匹配任意深度的同名目录，吞掉生产 import 包
   ``app/services/spatial_guardrails/data/`` 的新增模块 —— 新 clone / CI
   直接 ``ImportError``（该包早已跟踪 3 个生产模块，规则早于它 4.5 个月）；
2. 未锚定 ``.agents/`` 让第 100 个 skill 起永不入库（739 个 skill 文件靠
   ``git add -f`` 强行入库，外部 skills CLI / gstack-upgrade 结果稳定丢失）；
3. 父目录整体排除 ``.claude/`` 使三条 ``!`` 否定规则**永不生效** —— git 不会
   下降遍历被排除的目录，``.claude/README.md`` / ``.claude/commands/`` 既未
   跟踪也被遮蔽，新 clone 永久缺失；
4. 未锚定 ``review/`` 吞掉 ``tests/review``（9 文件）、``app/services/review``
   （7）、``frontend/lib/review``（3）的新文件 —— 测试目录受害最危险：新测试
   在 CI 中直接不存在，表现为「绿灯但没跑」。

探针不落盘：``git check-ignore`` 是纯路径名匹配（不看文件系统、不看 index），
因此本文件零副作用，可在任何 clone 上跑。反向探针（运行时产物仍须被忽略）
防止「为修 bug 把规则整条删掉」的过度修复。
"""
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 必须对 git 可见（不被忽略）—— 代码包 / 活跃技能树 / 镜像层文档
MUST_BE_VISIBLE = (
    # 1. 生产 import 包与测试语料的新模块（未锚定 data/ 的直接受害者）
    "app/services/spatial_guardrails/data/probe_module.py",
    "app/lib/data/probe_module.py",
    "tests/data/probe_test.py",
    "tests/science_oracles/data/probe_test.py",
    # 2. 活跃技能树的新 skill（未锚定 .agents/ 的直接受害者）
    ".agents/skills/zz-probe/SKILL.md",
    ".claude/skills/zz-probe/SKILL.md",
    # 3. .claude 镜像层（父目录排除令 ! 否定规则失效的直接受害者）
    ".claude/README.md",
    ".claude/commands/zz-probe.md",
    # 4. 四个 review 代码目录的新文件（未锚定 review/ 的直接受害者）
    "tests/review/probe_test.py",
    "app/services/review/probe.py",
    "frontend/lib/review/probe.ts",
    ".agents/skills/review/probe.md",
)

#: 仍必须被忽略 —— 运行时产物 / 会话稿 / 本地设置（防修复过度）
MUST_BE_IGNORED = (
    "data/runtime-artifact.json",           # 根级运行时数据（锚定后的 /data/）
    "data/.webgis-agent/rv-x/review/y.md",  # data/ 下的会话产物连带忽略
    ".agents/sessions/x.md",                # .agents 的直接子项（会话产物）
    ".agents/cache/x.json",
    "review/scratch.md",                    # 根级评审工作目录（锚定后的 /review/）
    "tests/review/x.scratch.md",            # 任意深度评审草稿按后缀忽略
    ".claude/settings.local.json",          # .claude 的其余子项继续忽略
)


def _is_ignored(path: str) -> bool:
    """``git check-ignore`` 语义：退出码 0=被规则忽略，1=可见。

    ``--no-index`` 让判定只看 .gitignore 规则本身（不受 index/工作区状态
    影响）—— 本测试钉的是规则，不是某个文件当下的跟踪状态。
    """
    proc = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if proc.returncode not in (0, 1):
        raise AssertionError(
            f"git check-ignore 失败（exit={proc.returncode}）: {proc.stderr.strip()}"
        )
    return proc.returncode == 0


@pytest.mark.parametrize("path", MUST_BE_VISIBLE)
def test_new_code_paths_are_not_ignored(path):
    """新增代码/测试/技能必须对 git 可见 —— 被吞 = 新 clone ImportError 或
    「CI 绿灯但没跑」（#1677）。"""
    assert not _is_ignored(path), (
        f"{path} 仍被 .gitignore 静默吞掉（未锚定规则或父目录排除）—— "
        "新增文件不会入库（#1677）"
    )


@pytest.mark.parametrize("path", MUST_BE_IGNORED)
def test_runtime_artifacts_stay_ignored(path):
    """反向探针：锚定 ≠ 删规则。运行时数据 / 会话稿 / 本地设置仍须忽略，
    否则 data/ 运行产物（exports、reports、uploads）会污染 git status。"""
    assert _is_ignored(path), (
        f"{path} 不再被忽略 —— #1677 的修复过度了（锚定后原规则的覆盖面 "
        "被意外扩大/删除）"
    )
