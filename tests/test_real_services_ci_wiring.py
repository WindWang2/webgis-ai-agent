"""Issue #477+#531：CI 的 real-services smoke lane 结构守卫。

CI 一直在 test-backend lane 里配 postgis + redis service container，但套件
跑的是 SQLite / fakeredis / eager-celery —— 容器基本闲置。#477 的修复是
新增 real-services-smoke lane 真正消费这些服务（asyncpg / PostGIS / 真实
Redis 线协议 / 真实 celery broker 投递），本文件守住这条 lane 不被悄悄拆掉。
#531 补充：lane 必须导出 USE_REDIS=true + CELERY_BROKER_URL/RESULT_BACKEND，
让生产 celery app（app.services.task_queue）走真实 broker，而不是继续钉在
conftest 的 eager/memory。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_yaml_parses():
    """YAML 必须可解析（yaml.safe_load 全文）。"""
    doc = _workflow()
    assert "jobs" in doc


def test_real_services_lane_exists_with_both_services():
    jobs = _workflow()["jobs"]
    assert "real-services-smoke" in jobs, "缺少 real-services-smoke lane"
    job = jobs["real-services-smoke"]
    images = {str(s.get("image", "")) for s in job.get("services", {}).values()}
    assert any("postgis" in i for i in images), "lane 必须配 postgis service"
    assert any(i.startswith("redis") for i in images), "lane 必须配 redis service"


def test_real_services_lane_runs_marker_and_env():
    jobs = _workflow()["jobs"]
    job = jobs["real-services-smoke"]
    run_text = "\n".join(str(s.get("run", "")) for s in job.get("steps", []))
    env_text = "\n".join(
        "\n".join(f"{k}={v}" for k, v in s.get("env", {}).items())
        for s in job.get("steps", [])
    )
    assert "-m real_services" in run_text, "lane 必须按 marker 选择 real-services 子集"
    assert "--no-cov" in run_text, "smoke lane 无需 coverage（与 perf/cartography lane 一致）"
    assert "DATABASE_URL=postgresql://" in env_text or "DATABASE_URL" in run_text, (
        "lane 必须把 DATABASE_URL 指到 provisioned Postgres"
    )
    assert "REDIS_URL" in env_text or "REDIS_URL" in run_text, (
        "lane 必须把 REDIS_URL 指到 provisioned Redis"
    )


def test_real_services_lane_is_release_blocking():
    needs = _workflow()["jobs"]["release-gate"]["needs"]
    assert "real-services-smoke" in needs, "real-services-smoke 必须进入 release DAG"


def test_backend_lane_excludes_real_services_marker():
    """real_services 子集由专属 lane 拥有 —— 主 lane 的 -m 过滤必须排除，
    避免双重执行 / 在无服务保障的上下文意外跑（与 perf/cartography 同款契约）。"""
    jobs = _workflow()["jobs"]
    run_text = "\n".join(str(s.get("run", "")) for s in jobs["test-backend"].get("steps", []))
    assert "not real_services" in run_text


def test_pytest_ini_registers_real_services_marker():
    ini = (REPO_ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "real_services:" in ini, "pytest.ini 必须注册 real_services marker"


def test_smoke_module_self_skips_without_services():
    """服务不可达时必须 self-skip（本地无容器不红），绝不把连不上伪装成通过。"""
    smoke = (REPO_ROOT / "tests" / "test_real_services_smoke.py").read_text(encoding="utf-8")
    assert "pytest.skip" in smoke
    assert "pytestmark = pytest.mark.real_services" in smoke


def test_real_services_lane_enables_production_celery_config():
    """#531：lane 必须导出 USE_REDIS=true + CELERY_BROKER_URL/RESULT_BACKEND，
    否则 conftest 的 setdefault 把生产 app 钉在 eager/memory，lane 只验证 toy app。"""
    jobs = _workflow()["jobs"]
    run_text = "\n".join(str(s.get("run", "")) for s in jobs["real-services-smoke"].get("steps", []))
    assert "USE_REDIS=true" in run_text, "lane 必须导出 USE_REDIS=true"
    assert "CELERY_BROKER_URL=redis://" in run_text, (
        "lane 必须把 CELERY_BROKER_URL 指到 provisioned Redis"
    )
    assert "CELERY_RESULT_BACKEND=redis://" in run_text, (
        "lane 必须把 CELERY_RESULT_BACKEND 指到 provisioned Redis"
    )


def test_real_services_smoke_exercises_production_celery_app():
    """#531：smoke 必须经生产 app（app.services.task_queue）投递生产任务。"""
    smoke = (REPO_ROOT / "tests" / "test_real_services_smoke.py").read_text(encoding="utf-8")
    assert "app.services.task_queue" in smoke, "smoke 必须 import 生产 task_queue app"
    assert "-A" in smoke and '"app.services.task_queue"' in smoke, (
        "worker 必须以生产 app 启动（-A app.services.task_queue）"
    )


def test_real_services_lane_requires_explicit_flag():
    """#661：lane 必须显式导出 REAL_SERVICES=1，smoke 守卫只认显式 flag。

    历史根因（#663-A 已根治 import 期 load_dotenv）：全量套件 import
    app.main 把本地 .env 的 REDIS_URL 泄进 os.environ；机器上恰好有可达
    Redis（如 dev 容器的 16379）时，"变量存在且可达"不再能区分"真 lane"与"被污染的本地套件"
    —— self-skip 被打穿后 production worker 在本地挂死。守卫必须以显式
    REAL_SERVICES=1 为准（与 #532 的 REQUIRE_BROWSER=1 同款契约），ambient
    环境变量一律不足以武装 smoke。
    """
    jobs = _workflow()["jobs"]
    run_text = "\n".join(
        str(s.get("run", "")) for s in jobs["real-services-smoke"]["steps"]
    )
    assert "REAL_SERVICES=1" in run_text, "lane 必须导出 REAL_SERVICES=1"

    smoke = (REPO_ROOT / "tests" / "test_real_services_smoke.py").read_text(encoding="utf-8")
    assert 'REAL_SERVICES' in smoke and '== "1"' in smoke, (
        'smoke 守卫必须以 REAL_SERVICES=="1" 显式 gate，不认 ambient env'
    )
