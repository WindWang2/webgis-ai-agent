"""#1676（SEC-04）：部署面口令卫生 —— 口令不得进容器 argv / inspect 可见面。

修复前四处缺陷（均已用实体探针复现）：

1. ``docker-compose.yml`` / ``docker-compose.prod.yml`` 的 redis
   ``command:`` 带 ``--requirepass ${REDIS_PASSWORD}`` —— compose 在解析期
   完成插值并把结果存进容器 ``Config.Cmd``，``docker inspect
   --format '{{.Config.Cmd}}'`` 直接打印明文；
2. ``deploy/redis-entrypoint.sh`` 的
   ``exec redis-server ... --requirepass "$requirepass"`` —— 口令进容器进程
   argv（``docker top`` / 宿主机 ``/proc/<pid>/cmdline`` 可见），把
   deploy/redis.conf「本文件不存任何凭据」的努力抵消殆尽；
3. ``deploy/k8s/05-deps-optional.yaml`` 的 ``--requirepass $(REDIS_PASSWORD)``
   —— kubelet 在容器启动时把 secret 值展开进 argv；
4. 两个 prod 栈 redis-exporter 的 ``REDIS_PASSWORD`` env —— ``docker
   inspect`` 的 Config.Env 明文。

该口令同时是 celery broker + result backend + session store 的钥匙；任何能
连 Docker socket、能 inspect/top、或能读宿主机 proc 的身份（含被攻破的低权
容器经 socket 逃逸）即可取到。

统一修复为「运行时 0400 文件 + config include」（tmpfs /dev/shm —— 不落盘、
不进镜像层、容器停止即消失）；redis-exporter 改上游 ``REDIS_PASSWORD_FILE``
（redis_exporter v1.58.0 main.go 的 ``-redis.password-file``；exporter/
pwd_file.go 用 json.Unmarshal 解析 ``{redis URI: 口令}``，且仅在
``REDIS_PASSWORD`` 为空时生效）。

本文件是纯文本 / YAML 结构断言（不需要 Docker）：任何把口令搬回 argv 或
env 的改动在此红。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.prod.yml",
    "docker-compose.prod.secure.yml",
)
ENTRYPOINT = REPO_ROOT / "deploy" / "redis-entrypoint.sh"
K8S_DEPS = REPO_ROOT / "deploy" / "k8s" / "05-deps-optional.yaml"


def _compose(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))


def _k8s_redis_container() -> tuple[dict, dict]:
    """(redis 容器 spec, 所在 pod spec)。"""
    for doc in yaml.safe_load_all(K8S_DEPS.read_text(encoding="utf-8")):
        if doc and doc.get("kind") == "Deployment":
            pod_spec = doc["spec"]["template"]["spec"]
            for c in pod_spec.get("containers", []):
                if c.get("name") == "redis":
                    return c, pod_spec
    raise AssertionError("05-deps-optional.yaml 里找不到 redis 容器")


# ── 1. compose command/args 不得出现 --requirepass ──────────────────────────


def _code_lines(text: str) -> str:
    """去掉注释行后的内容 —— 注释里的历史说明（定位 #1676 的路标）不算缺陷。"""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_no_requirepass_flag_in_any_compose():
    """`--requirepass` 作为 argv 标志出现 = compose 插值后进 Config.Cmd。"""
    for name in COMPOSE_FILES:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        code = _code_lines(text)
        assert "--requirepass" not in code, (
            f"{name} 仍把 Redis 口令插值进 command/args —— "
            "docker inspect Config.Cmd / docker top 可见明文（#1676）"
        )
        # 结构层再钉一次：redis 的 command/args（即容器 Config.Cmd）
        redis = _compose(name)["services"]["redis"]
        argv = redis.get("command") or []
        if isinstance(argv, str):
            argv = [argv]
        argv = list(argv) + list(redis.get("args") or [])
        assert "--requirepass" not in " ".join(argv), (
            f"{name} 的 redis command/args 含 --requirepass（#1676）: {argv}"
        )


def test_every_compose_redis_still_enforces_auth():
    """正向：认证不得因修 argv 暴露而被删掉。

    dev 栈入口内联写 tmpfs 运行时 conf；两个 prod 栈走
    deploy/redis-entrypoint.sh（写 /dev/shm 0400 conf + config include）。
    """
    for name in COMPOSE_FILES:
        redis = _compose(name)["services"]["redis"]
        blob = yaml.safe_dump(redis)  # command/entrypoint/environment 全量
        assert "requirepass" in blob or "redis-entrypoint.sh" in blob, (
            f"{name} 的 redis 既无 requirepass 注入也未委托 entrypoint —— "
            "认证被修没了（#1676 只修 argv 暴露，不删认证）"
        )


# ── 2. entrypoint 口令不进 argv ─────────────────────────────────────────────


def test_entrypoint_never_puts_password_in_argv():
    ep = _code_lines(ENTRYPOINT.read_text(encoding="utf-8"))
    assert "--requirepass" not in ep, (
        "deploy/redis-entrypoint.sh 把口令放回 argv —— docker top / "
        "宿主机 /proc/<pid>/cmdline 可见明文（#1676）"
    )
    # 运行时 conf 注入三要素：写文件（tmpfs）+ 0400 + 作为配置文件传入
    assert "/dev/shm/redis-runtime.conf" in ep, "entrypoint 未写 tmpfs 运行时 conf"
    assert "chmod 400" in ep, "运行时 conf 未收紧到 0400"
    assert 'redis-server /usr/local/etc/redis/redis.conf "$rtconf"' in ep, (
        "entrypoint 未以「配置文件 include」方式传入 requirepass"
    )


# ── 3. k8s redis 口令不进 argv ──────────────────────────────────────────────


def test_k8s_redis_password_not_in_argv():
    container, pod_spec = _k8s_redis_container()
    all_args = (container.get("command") or []) + (container.get("args") or [])
    assert "--requirepass" not in all_args, (
        f"k8s redis 口令进 argv（kubectl describe / 节点 proc 可见明文，#1676）: {all_args}"
    )
    script = " ".join(all_args)
    assert "requirepass" in script and "/dev/shm/redis-runtime.conf" in script, (
        f"k8s redis 未改为 tmpfs 运行时 conf 注入: {script}"
    )
    # readOnlyRootFilesystem: true 下必须显式挂可写 tmpfs（与 02-api 的 /tmp 同款）
    mounts = {m["name"] for m in container.get("volumeMounts", [])}
    volumes = {v["name"] for v in pod_spec.get("volumes", [])}
    assert "redis-runtime" in mounts and "redis-runtime" in volumes, (
        "k8s redis 缺少 /dev/shm tmpfs 挂载（readOnlyRootFilesystem 下不可写）"
    )


# ── 4. exporter 口令不进 env ────────────────────────────────────────────────


def test_redis_exporter_password_not_in_env():
    """两个 prod 栈的 redis-exporter：REDIS_PASSWORD env → REDIS_PASSWORD_FILE。

    redis_exporter v1.58.0 的 main.go 定义 `-redis.password-file`（env
    REDIS_PASSWORD_FILE），exporter/pwd_file.go 以 json.Unmarshal 解析
    {redis URI: 口令}，且仅在 REDIS_PASSWORD 为空时生效 —— 故 env 里的
    REDIS_PASSWORD 必须一并移除，否则文件不生效。
    """
    for name in ("docker-compose.prod.yml", "docker-compose.prod.secure.yml"):
        svc = _compose(name)["services"]["redis-exporter"]
        env = svc.get("environment") or {}
        assert "REDIS_PASSWORD" not in env, (
            f"{name} redis-exporter 仍把口令放 env（docker inspect Config.Env 可见明文，#1676）"
        )
        assert env.get("REDIS_PASSWORD_FILE") == "/run/secrets/redis_password", (
            f"{name} redis-exporter 未改用 REDIS_PASSWORD_FILE"
        )
        # 口令文件来自 compose configs（不入库、不进 env）
        sources = {c["source"] for c in svc.get("configs") or []}
        assert "webgis_redis_password" in sources, (
            f"{name} redis-exporter 未挂载 webgis_redis_password config"
        )
        configs = _compose(name).get("configs") or {}
        assert "webgis_redis_password" in configs, (
            f"{name} 缺少 webgis_redis_password config 定义"
        )
