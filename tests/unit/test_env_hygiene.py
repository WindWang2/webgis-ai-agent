"""#663：进程环境卫生契约（A：import 纯净；B：conftest 全键钉扎）。

A —— import 任何 app 模块不得改写 os.environ。此前 `import app.main` 会执行
`load_dotenv()`（override=False 只补缺），把本地 `.env` 的键值泄进整个测试
进程：全量套件里更早的测试 import 了 app，后续所有测试都看得见 `.env`
（#661 的根因）。env 加载因此上移到启动器（根 main.py / manage.py 显式
load_dotenv，裸 uvicorn 用 --env-file），app 代码本身不再有 import 期副作用。

B —— conftest 顶部把 `.env.example` 全部键 setdefault 预占测试安全值
（与 Settings 默认等价）。CI 各 lane 在 pytest 启动前显式导出的变量不受
setdefault 影响；本地开发 shell 里导出的真实键（真 API key、真 Redis）也
无法再改变套件行为 —— 套件在脏机器上等价于干净机器。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 允许不钉扎的键及理由。代理键必须保持 absent —— 空串在 httpx/requests
# 语义里不等于未设置，钉 "" 反而会改变网络行为。
_UNPINNED_OK = {
    "HTTP_PROXY",
    "HTTPS_PROXY",
}

# 第三方库 import 期已知的环境改写（与 app 代码无关，不可由本仓库修复）：
# rasterio / pyogrio 初始化 GDAL/PROJ 时把 CA bundle 指到 venv 内 certifi。
# 白名单必须显式 —— 新出现的库改写会让纯净测试先红，逼出一次有意识的决策。
_LIBRARY_MUTATIONS_OK = {
    "GDAL_CURL_CA_BUNDLE",
    "PROJ_CURL_CA_BUNDLE",
}


def test_import_app_main_does_not_mutate_os_environ():
    """A 的证明性验收：子进程 import app.main 前后 os.environ 完全一致。"""
    code = (
        "import os, json\n"
        "before = dict(os.environ)\n"
        "import app.main  # noqa: F401\n"
        "after = dict(os.environ)\n"
        "diff = {k: (before.get(k), after[k]) for k in after if before.get(k) != after.get(k)}\n"
        "print(json.dumps(diff))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"import app.main 失败:\n{proc.stderr[-2000:]}"
    diff = json.loads(proc.stdout.strip().splitlines()[-1])
    ours = {k: v for k, v in diff.items() if k not in _LIBRARY_MUTATIONS_OK}
    assert ours == {}, f"import app.main 改写了进程环境: {ours}"


def test_conftest_pins_every_env_example_key():
    """B 的 wiring 守卫：.env.example 每个键都必须被 conftest setdefault 预占。

    新增 env 键却忘了钉扎时，这里先红 —— 把"打地鼠"变成显式清单。
    """
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    example_keys = {
        line.split("=", 1)[0].strip()
        for line in example.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert example_keys, ".env.example 解析为空 —— 解析器坏了"

    conftest = (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    # conftest 以 `_ENV_BASELINE` 字典 + setdefault 循环钉扎 —— 解析字典键。
    match = re.search(r"_ENV_BASELINE = \{(.*?)\n\}", conftest, re.DOTALL)
    assert match, "tests/conftest.py 缺少 _ENV_BASELINE 钉扎表"
    pinned = set(re.findall(r'"([A-Za-z_0-9]+)":', match.group(1)))

    missing = example_keys - pinned - _UNPINNED_OK
    assert not missing, (
        f".env.example 存在未钉扎的键: {sorted(missing)} —— "
        "请在 tests/conftest.py 为其 setdefault 一个与 Settings 默认等价的安全值"
    )


def test_unpinned_allowlist_still_justified():
    """skip-list 自身也要被锁：往 _UNPINNED_OK 里加键必须有 .env.example 依据。"""
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    example_keys = {
        line.split("=", 1)[0].strip()
        for line in example.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert _UNPINNED_OK <= example_keys, "skip-list 里出现了 .env.example 之外的键"


def test_env_pins_are_visible_in_this_process():
    """钉扎在 conftest import 期生效：本测试进程里关键键已是安全值。

    与 wiring 测试互补：wiring 锁清单完整性，本测试锁行为真实生效
    （防 setdefault 写错位置/时机导致根本没跑）。
    """
    assert os.environ.get("USE_REDIS") == "false"
    assert os.environ.get("CELERY_BROKER_URL") == "memory://"
    assert os.environ.get("CELERY_RESULT_BACKEND") == "cache+memory://"
