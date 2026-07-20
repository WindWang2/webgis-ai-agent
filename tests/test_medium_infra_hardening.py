"""PR H - 基础设施加固的回归测试。

覆盖：
- I17: dev compose bind-mount 可控
- I18: Dockerfile runner 不用 GDAL dev 头
- I19: Redis maxmemory-policy 改为 noeviction
- I20: prod.secure.yml 有 PGDATA
- I22: K8s 有 PDB + topologySpread
- I23: K8s Postgres/Redis 有 resources
- I24: K8s 有 ServiceAccount
- I27: rollback 用 pinned commit 而非 tag
"""
from pathlib import Path
import pytest

REPO = Path(__file__).parent.parent


# ── I17: dev compose bind-mount ─────────────────────────────────────────


def test_i17_dev_compose_mount_is_configurable():
    """I17：bind-mount 应通过 WEBGIS_DEV_MOUNT 环境变量可关闭。"""
    compose = (REPO / "docker-compose.yml").read_text()
    assert "WEBGIS_DEV_MOUNT" in compose, "dev compose 应让 bind-mount 可配置"


# ── I18: Dockerfile runner no GDAL dev ──────────────────────────────────


def test_i18_dockerfile_runner_no_gdal_dev():
    """I18：runner stage 不应装 libgdal-dev（用 runtime libs 替代）。"""
    dockerfile = (REPO / "Dockerfile").read_text()
    # 找 runner stage（AS runner 之后的所有内容）
    if "AS runner" in dockerfile:
        runner_section = dockerfile.split("AS runner", 1)[1]
    else:
        runner_section = dockerfile
    # runner 不应有 libgdal-dev（dev 头文件）
    assert "libgdal-dev" not in runner_section, "runner stage 仍有 libgdal-dev"
    # 应有 runtime lib（libgdal32t64 或类似）
    assert "libgdal" in runner_section, "runner stage 应有 GDAL runtime lib"


# ── I19: Redis noeviction ───────────────────────────────────────────────


def test_i19_prod_compose_redis_no_eviction():
    """I19：prod compose Redis 应该用 noeviction（不静默丢 broker 消息）。"""
    compose = (REPO / "docker-compose.prod.yml").read_text()
    # 直接检查：不应有 allkeys-lru，应有 noeviction
    assert "allkeys-lru" not in compose, "prod compose 仍含 allkeys-lru"
    assert "noeviction" in compose


def test_i19_k8s_redis_no_eviction():
    """I19：k8s Redis 同样。"""
    deps = (REPO / "deploy" / "k8s" / "05-deps-optional.yaml").read_text()
    assert "noeviction" in deps
    assert "allkeys-lru" not in deps


# ── I20: prod.secure.yml PGDATA ─────────────────────────────────────────


def test_i20_secure_compose_has_pgdata():
    """I20：prod.secure.yml 的 db 服务必须有 PGDATA 环境变量。"""
    compose = (REPO / "docker-compose.prod.secure.yml").read_text()
    assert "PGDATA" in compose, "prod.secure.yml 缺 PGDATA"


# ── I22: PDB + topologySpread ───────────────────────────────────────────


def test_i22_k8s_has_pdb():
    """I22：必须有 PodDisruptionBudget。"""
    # 在 06-hpa-pdb-rbac.yaml
    pdb_file = (REPO / "deploy" / "k8s" / "06-hpa-pdb-rbac.yaml").read_text()
    assert "PodDisruptionBudget" in pdb_file


def test_i22_api_deployment_has_topology_spread():
    """I22：api deployment 应有 topologySpreadConstraints。"""
    deploy = (REPO / "deploy" / "k8s" / "02-api-deployment.yaml").read_text()
    assert "topologySpreadConstraints" in deploy


def test_i22_kustomization_includes_pdb_file():
    """I22：kustomization 应 include 06-hpa-pdb-rbac.yaml。"""
    kustomization = (REPO / "deploy" / "k8s" / "kustomization.yaml").read_text()
    assert "06-hpa-pdb-rbac.yaml" in kustomization


# ── I23: Postgres/Redis resources ───────────────────────────────────────


def test_i23_k8s_postgres_has_resources():
    """I23：k8s Postgres 必须有 resources。"""
    deps = (REPO / "deploy" / "k8s" / "05-deps-optional.yaml").read_text()
    # postgres 和 redis 都应有 resources
    assert "resources:" in deps
    assert "requests:" in deps
    assert "limits:" in deps


def test_i23_k8s_redis_has_resources():
    """I23：k8s Redis 也应有 resources。"""
    deps = (REPO / "deploy" / "k8s" / "05-deps-optional.yaml").read_text()
    # 整个文件至少 2 处 resources（postgres + redis 各一个）
    assert deps.count("resources:") >= 2, f"应有 postgres + redis 两处 resources，实际 {deps.count('resources:')}"


# ── I24: ServiceAccount ─────────────────────────────────────────────────


def test_i24_k8s_has_service_account():
    """I24：必须有专用 ServiceAccount。"""
    sa_file = (REPO / "deploy" / "k8s" / "06-hpa-pdb-rbac.yaml").read_text()
    assert "ServiceAccount" in sa_file
    assert "automountServiceAccountToken: false" in sa_file


def test_i24_api_deployment_uses_service_account():
    """I24：api deployment 应指定 serviceAccountName。"""
    deploy = (REPO / "deploy" / "k8s" / "02-api-deployment.yaml").read_text()
    assert "serviceAccountName:" in deploy


# ── I27: rollback pinned commit ─────────────────────────────────────────


def test_i27_rollback_uses_pinned_commit():
    """I27：rollback job 应 checkout pinned commit（而非模糊 tag）。"""
    workflow = (REPO / ".github" / "workflows" / "production.yml").read_text()
    rollback_section = workflow.split("rollback:")[1] if "rollback:" in workflow else ""
    assert "PREV_SHA" in rollback_section or "prev-commit" in rollback_section
    # 不应再用 git describe --abbrev=0 --tags
    assert "git describe --abbrev=0 --tags HEAD^" not in rollback_section
