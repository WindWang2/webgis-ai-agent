"""Issue #560: 承担 celery broker/result backend 角色的 Redis 不得用 allkeys-lru。

docker-compose.prod.secure.yml 的 redis 服务挂载 deploy/redis.conf 并用
redis-entrypoint.sh 启动（redis-server /usr/local/etc/redis/redis.conf）。修复前
该 conf 是 maxmemory-policy allkeys-lru —— 同一实例还兼 celery broker(db0)/
result backend(db1) + session 缓存，内存压力下驱逐 broker 列表键和结果键且
不报错 → 任务无声丢失（告警看不出异常：键消失而非 backlog 增长）。

本文件守住整个部署矩阵里每个 broker-bearing Redis 的有效策略：
  1. deploy/redis.conf（secure 栈与 #1676 起的 prod 栈挂载）必须是 noeviction，且不许 allkeys-lru；
  2. 参考栈 docker-compose.prod.yml 必须挂载该 conf（策略经 conf 生效，#1676 起不再走 command）；
  3. k8s 可选 redis（05-deps-optional.yaml）的运行时 conf 保持 noeviction；
  4. 禁止任何栈把 allkeys-lru 传进 secure 栈的 redis（防回归）。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

SAFE_POLICIES = {"noeviction", "volatile-lru", "volatile-ttl", "volatile-random"}


def _redis_conf_text() -> str:
    return (REPO_ROOT / "deploy" / "redis.conf").read_text(encoding="utf-8")


def _compose(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))


def test_secure_stack_conf_policy_is_safe():
    conf = _redis_conf_text()
    # 只检查指令形态（maxmemory-policy <值>），注释里对旧值的解释不影响判定
    assert "maxmemory-policy allkeys-lru" not in conf, (
        "deploy/redis.conf 的 maxmemory-policy 仍是 allkeys-lru —— secure 栈 "
        "redis 兼 celery broker/result backend，驱逐会无声丢任务"
    )
    assert "maxmemory-policy noeviction" in conf, (
        "deploy/redis.conf 必须显式 noeviction（broker 键绝不能被驱逐，"
        "内存写满时宁可 OOM 显式失败）"
    )


def test_secure_stack_redis_uses_mounted_conf_without_cli_override():
    """secure 栈 redis 只经 conf 生效，不得在 command/entrypoint 里用 CLI
    allkeys-lru 把 conf 盖回去。"""
    redis = _compose("docker-compose.prod.secure.yml")["services"]["redis"]
    text = str(redis)
    assert "redis.conf" in text, "secure 栈 redis 必须挂载 deploy/redis.conf"
    assert "allkeys-lru" not in text, "secure 栈 redis 不能把 allkeys-lru 传进容器"


def test_prod_compose_redis_policy_is_safe():
    """参考栈（docker-compose.prod.yml）：策略必须保持 noeviction。

    #1676 起该栈与 secure 栈同路 —— 配置（含 maxmemory-policy）经挂载的
    deploy/redis.conf 生效，不再走 command argv（口令与策略都不进
    Config.Cmd）。因此断言点从 command 改为「挂载了 conf + conf 本身安全」。
    """
    redis = _compose("docker-compose.prod.yml")["services"]["redis"]
    text = str(redis)
    assert "redis.conf" in text, "prod 栈 redis 必须挂载 deploy/redis.conf"
    assert "allkeys-lru" not in text, "prod 栈 redis 不能把 allkeys-lru 传进容器"
    assert "maxmemory-policy noeviction" in _redis_conf_text(), (
        "prod 栈经 deploy/redis.conf 生效，conf 必须显式 noeviction"
    )


def test_k8s_optional_redis_policy_is_safe():
    """k8s 可选内部 redis（05-deps-optional.yaml）同样承担 broker 角色。"""
    docs = [
        d
        for d in yaml.safe_load_all(
            (REPO_ROOT / "deploy" / "k8s" / "05-deps-optional.yaml").read_text(encoding="utf-8")
        )
        if d
    ]
    for doc in docs:
        if doc.get("kind") != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"].get("containers", []):
            if container.get("name") == "redis":
                args = " ".join(container.get("command", []) + container.get("args", []))
                assert "noeviction" in args, "k8s 可选 redis 必须 noeviction"
                assert "allkeys-lru" not in args
                return
    raise AssertionError("05-deps-optional.yaml 无 redis 容器")


def test_no_broker_bearing_stack_uses_eviction_policy():
    """矩阵级守卫：所有（显式声明或经 conf）给 broker-bearing redis 的策略都必须是安全集。

    #1676 起 prod 栈与 secure 栈同走挂载的 deploy/redis.conf、k8s 走 sh -c
    运行时 conf —— 采集点随形态更新，安全集不变。
    """
    policies: dict[str, str] = {}
    for line in _redis_conf_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("maxmemory-policy "):
            policies["deploy/redis.conf (secure+prod)"] = stripped.split(None, 1)[1]
    for doc in yaml.safe_load_all(
        (REPO_ROOT / "deploy" / "k8s" / "05-deps-optional.yaml").read_text(encoding="utf-8")
    ):
        if not doc or doc.get("kind") != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"].get("containers", []):
            if container.get("name") != "redis":
                continue
            parts = " ".join(
                (container.get("command") or []) + (container.get("args") or [])
            ).split()
            if "maxmemory-policy" in parts:
                policies["k8s 05-deps-optional"] = parts[parts.index("maxmemory-policy") + 1]
    assert policies, "未采集到任何 broker-bearing redis 的 maxmemory-policy"
    unsafe = {k: v for k, v in policies.items() if v not in SAFE_POLICIES}
    assert not unsafe, f"broker-bearing redis 策略不在安全集内: {unsafe}"
