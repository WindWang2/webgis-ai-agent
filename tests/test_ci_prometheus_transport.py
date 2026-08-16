"""Issue #530：deploy-prod / rollback 的 scp 必须送达 prometheus 配置。

docker-compose.prod.secure.yml 的 prometheus 服务 bind-mount
./deploy/prometheus.yml 与 ./deploy/alerts-rules.json。修复前两个 scp 清单都
漏掉它们 —— 生产主机上没有仓库检出，Docker 把缺失的挂载源创建为空目录，
prometheus 以目录当 config 加载即 crash-loop，告警全哑（nginx 在 #472 内联修复，
prometheus 走 scp 传输）。本文件守住：secure compose 里任何 ./deploy/ bind
源都必须出现在 deploy-prod 与 rollback 的传输步骤里。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production.yml"
COMPOSE = REPO_ROOT / "docker-compose.prod.secure.yml"

TRANSPORTED = {"deploy/prometheus.yml", "deploy/alerts-rules.json"}


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job_run_text(job: str) -> str:
    return "\n".join(
        s.get("run", "") for s in _workflow()["jobs"][job]["steps"] if s.get("run")
    )


def _secure_compose_deploy_bind_sources() -> set:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    sources = set()
    for svc, cfg in compose["services"].items():
        for v in cfg.get("volumes", []):
            text = v if isinstance(v, str) else v.get("source", "")
            if text.startswith("./deploy/"):
                host = text.split(":", 1)[0]  # "./deploy/x:/etc/...:ro" -> "./deploy/x"
                sources.add(host[2:])          # -> "deploy/x"
    return sources


def test_secure_compose_prometheus_bind_mounts_exist():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    prometheus = compose["services"]["prometheus"]
    vol_text = "\n".join(
        v if isinstance(v, str) else v.get("source", "") for v in prometheus["volumes"]
    )
    assert "./deploy/prometheus.yml" in vol_text
    assert "./deploy/alerts-rules.json" in vol_text


def test_deploy_prod_scp_includes_prometheus_files():
    run = _job_run_text("deploy-prod")
    for path in TRANSPORTED:
        assert path in run, f"deploy-prod scp 清单缺少 {path}"


def test_rollback_scp_includes_prometheus_files():
    run = _job_run_text("rollback")
    for path in TRANSPORTED:
        assert path in run, f"rollback scp 清单缺少 {path}"


def test_every_deploy_bind_source_is_transported_in_both_jobs():
    """通用契约：secure compose 里任何 ./deploy/ bind 源，deploy-prod 与
    rollback 都必须送达主机（或经内联 configs 分发 —— 本文件只扫 volumes，
    nginx 的 configs: 挂载不在此列）。"""
    sources = _secure_compose_deploy_bind_sources()
    assert sources, "secure compose 竟然没有 ./deploy/ bind 源？"
    for job in ("deploy-prod", "rollback"):
        run = _job_run_text(job)
        missing = {s for s in sources if s not in run}
        assert not missing, f"{job} 未传输的 ./deploy/ bind 源: {missing}"
