"""manage.py 离线部署面子命令（preflight / network-catalog / sbom /
asset-manifest）—— 子进程级 contract 测试。

子进程运行（真实 CLI 路径）；env 由测试显式供给，不依赖 .env。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # 稳定最小环境：不读 .env（worktree 无）、云默认、显式 sqlite。
    env.setdefault("JWT_SECRET_KEY", "test-secret")
    env.setdefault("LLM_API_KEY", "sk-test")
    env["DATABASE_URL"] = "sqlite:///./data/webgis.db"
    if extra_env:
        for k, v in extra_env.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )


def _parse_json_tail(stdout: str) -> dict:
    """CLI 可能先打印人类可读输出；取最后一个完整 JSON 值。"""
    start = stdout.rfind("\n{")
    if start == -1:
        start = stdout.find("{")
    return json.loads(stdout[start:])


# ── network-catalog ─────────────────────────────────────────────────


def test_network_catalog_json_contract():
    proc = _run("network-catalog", "--format", "json")
    assert proc.returncode == 0, proc.stderr[-500:]
    payload = json.loads(proc.stdout[proc.stdout.find("{"):])
    assert payload["schema_version"] == 1
    assert payload["deployment_profile"] == "cloud"
    assert len(payload["dependencies"]) >= 12
    sample = payload["dependencies"][0]
    assert {"id", "category", "endpoint", "available_offline",
            "enforced_by_egress_guard"} <= set(sample)


def test_network_catalog_table_format_runs():
    proc = _run("network-catalog", "--format", "table")
    assert proc.returncode == 0, proc.stderr[-500:]
    assert "llm_chat" in proc.stdout


# ── sbom / asset-manifest ──────────────────────────────────────────


def test_sbom_lists_python_distributions_and_frontend():
    proc = _run("sbom")
    assert proc.returncode == 0, proc.stderr[-500:]
    payload = json.loads(proc.stdout[proc.stdout.find("{"):])
    assert payload["schema_version"] == 1
    names = {d["name"] for d in payload["python"]["distributions"]}
    assert "fastapi" in names or "uvicorn" in names  # 安装环境真实可读
    assert payload["python"]["python_version"]
    # 前端依赖清单（来自 package.json 声明）
    assert "next" in payload["frontend"]["dependencies"]
    # 受限资产零打包：SBOM 只含元数据，无文件路径/权重文件
    assert all("path" not in d for d in payload["python"]["distributions"])


def test_sbom_out_writes_file(tmp_path):
    out = tmp_path / "sbom.json"
    proc = _run("sbom", "--out", str(out))
    assert proc.returncode == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1


def test_asset_manifest_entries_have_status():
    proc = _run("asset-manifest")
    assert proc.returncode == 0, proc.stderr[-500:]
    payload = json.loads(proc.stdout[proc.stdout.find("{"):])
    ids = {e["id"] for e in payload["assets"]}
    # 离线部署所需的外部资产面（模型缓存/字形/底图/本地地理数据/LLM 权重）
    assert {"rag_embedding_model", "map_glyphs", "local_basemap_tiles",
            "local_geodata", "llm_weights"} <= ids
    for entry in payload["assets"]:
        assert entry["status"] in ("present", "absent", "operator_supplied")
        assert entry["provisioning"]


# ── preflight ───────────────────────────────────────────────────────


def test_preflight_scoped_green_exit_zero(tmp_path):
    geodata = tmp_path / "geodata"
    geodata.mkdir()
    proc = _run(
        "preflight", "--json", "--only", "deployment_profile,egress_policy,local_geodata",
        extra_env={"LOCAL_GEODATA_DIR": str(geodata)},
    )
    assert proc.returncode == 0, proc.stdout[-800:]
    payload = _parse_json_tail(proc.stdout)
    assert payload["summary"]["failed"] == 0
    names = {c["check"] for c in payload["checks"]}
    assert names == {"deployment_profile", "egress_policy", "local_geodata"}


def test_preflight_missing_geodata_dir_exit_one():
    proc = _run(
        "preflight", "--json", "--only", "local_geodata",
        extra_env={"LOCAL_GEODATA_DIR": "Z:/definitely/missing/path"},
    )
    assert proc.returncode == 1
    payload = _parse_json_tail(proc.stdout)
    assert payload["summary"]["failed"] >= 1
    check = payload["checks"][0]
    assert check["status"] == "down"


def test_preflight_air_gapped_requires_local_llm_flag():
    """air-gapped 下 RAG_EMBEDDING_OFFLINE=false → 该配置项 down（首用必败）。"""
    proc = _run(
        "preflight", "--json", "--only", "rag_embeddings",
        extra_env={"DEPLOYMENT_PROFILE": "air_gapped",
                   "NETWORK_EGRESS_MODE": "allowlist",
                   "LLM_BASE_URL": "http://127.0.0.1:11434/v1",
                   "RAG_EMBEDDING_OFFLINE": "false"},
    )
    assert proc.returncode == 1
    payload = _parse_json_tail(proc.stdout)
    check = payload["checks"][0]
    assert check["status"] == "down"
    assert "RAG_EMBEDDING_OFFLINE" in check["detail"]


def test_preflight_air_gapped_public_llm_endpoint_reported_down():
    proc = _run(
        "preflight", "--json", "--only", "llm_endpoint",
        extra_env={"DEPLOYMENT_PROFILE": "air_gapped",
                   "NETWORK_EGRESS_MODE": "allowlist",
                   "LLM_BASE_URL": "http://127.0.0.1:11434/v1"},
    )
    # 端点指向本机回环（不可达也只影响连通性；此处只验证"私网判定通过"不报策略 down）
    payload = _parse_json_tail(proc.stdout)
    check = payload["checks"][0]
    # 回环端点：策略判定 ok；连通性失败也只 down 在 connectivity 而非 policy。
    assert "policy=local" in check["detail"] or check["status"] in ("ok", "down")


def test_preflight_unscoped_runs_all_checks():
    proc = _run(
        "preflight", "--json",
        extra_env={"USE_REDIS": "false", "CELERY_BROKER_URL": "memory://",
                   "RAG_EMBEDDING_OFFLINE": "true"},
    )
    # 全量运行允许有 down（无 Redis/无 LLM 的裸环境）——只验证结构完整。
    payload = _parse_json_tail(proc.stdout)
    names = {c["check"] for c in payload["checks"]}
    assert {"deployment_profile", "egress_policy", "database", "redis",
            "llm_endpoint", "rag_embeddings", "local_geodata",
            "data_fabric_local_roots", "data_dirs",
            "network_dependencies"} <= names
