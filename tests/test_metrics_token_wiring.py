"""PLT-01 / PLT-05（deep-review 2026-09-19）：METRICS_TOKEN 端到端接线。

审计：
  * PLT-05：标准 prod 栈的 prometheus 没有挂载 metrics token 文件，而
    deploy/prometheus.yml 的 webgis-api scrape job 用 credentials_file 读取
    它 —— 监控全盲；
  * PLT-01：CI 的 deploy-prod / rollback 不生成 METRICS_TOKEN，而 secure
    compose 用 ``${METRICS_TOKEN:?}`` 插值 —— compose 解析期即失败。

本文件把两栈的 configs 挂载、prometheus 的 credentials_file 路径、模板键与
CI 传递链钉在一起（任一处漂移即红）。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production.yml"
PROMETHEUS_TARGET = "/etc/prometheus/secrets/metrics_token"


def _compose(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))


def _config_mounts(compose: dict, service: str) -> list[dict]:
    return list(compose["services"][service].get("configs", []) or [])


def test_both_prod_composes_define_metrics_token_config():
    for name in ("docker-compose.prod.yml", "docker-compose.prod.secure.yml"):
        compose = _compose(name)
        config = (compose.get("configs") or {}).get("webgis_metrics_token")
        assert config is not None, f"{name}: 缺少 webgis_metrics_token config"
        content = str(config.get("content", ""))
        assert "METRICS_TOKEN" in content and ":?" in content, (
            f"{name}: metrics token config 必须用 ${{METRICS_TOKEN:?}} fail-fast"
        )


def test_both_prod_prometheus_mount_metrics_token_at_documented_path():
    for name in ("docker-compose.prod.yml", "docker-compose.prod.secure.yml"):
        compose = _compose(name)
        mounts = [
            m
            for m in _config_mounts(compose, "prometheus")
            if m.get("source") == "webgis_metrics_token"
        ]
        assert mounts, f"{name}: prometheus 未挂载 webgis_metrics_token"
        assert mounts[0].get("target") == PROMETHEUS_TARGET, (
            f"{name}: metrics token 必须挂在 {PROMETHEUS_TARGET}"
        )


def test_prometheus_credentials_file_matches_compose_mount_path():
    prom = yaml.safe_load((REPO_ROOT / "deploy" / "prometheus.yml").read_text(encoding="utf-8"))
    api_job = next(
        job for job in prom["scrape_configs"] if job.get("job_name") == "webgis-api"
    )
    assert api_job["authorization"]["type"] == "Bearer"
    assert api_job["authorization"]["credentials_file"] == PROMETHEUS_TARGET


def test_env_templates_expose_metrics_token():
    for name in (".env.prod.example", ".env.Priv.example"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "METRICS_TOKEN=" in text, f"{name} 必须暴露 METRICS_TOKEN 模板行"


def test_ci_deploy_and_rollback_forward_metrics_token():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in ("deploy-prod", "rollback"):
        steps = workflow["jobs"][job]["steps"]
        deployment = [
            s
            for s in steps
            if s.get("env", {}).get("METRICS_TOKEN") is not None
        ]
        assert deployment, (
            f"{job}: 部署步骤必须从 secrets.METRICS_TOKEN 注入 METRICS_TOKEN"
        )
        assert "secrets.METRICS_TOKEN" in deployment[0]["env"]["METRICS_TOKEN"]


def test_ci_env_script_emits_metrics_token():
    script = (REPO_ROOT / "deploy" / "ci-generate-env-priv.sh").read_text(encoding="utf-8")
    assert "METRICS_TOKEN=" in script, "ci-generate-env-priv.sh 必须写 METRICS_TOKEN 行"
    assert "${METRICS_TOKEN:-}" in script, "缺失时写空行（compose :? 快速失败）"
