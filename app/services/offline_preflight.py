"""离线/内网部署 preflight（ADR-0197）—— 部署 doctor 的探测面。

``manage.py preflight`` 消费。设计要点：

- 组件化：每个检查返回 ``{check, status, detail, required}``，词表沿用
  SRE 的 ok | degraded | down | not_configured。
- exit code 语义：**required 检查出现 down → 1**，否则 0（degraded/
  not_configured 是诚实状态，不是失败）。``--only`` 支持范围收窄。
- 探测分层：纯配置面（profile/egress/rag 开关）零 IO；连通性面
  （DB/Redis/LLM）有界超时。LLM 连通失败 vs 策略拒绝在 detail 里区分。
- 不伪造：本地数据缺失如实 down；CLI 上下文工具 registry 未注入的
  依赖矩阵如实标注（见 network_dependency.tool_network_matrix）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

#: 词表（与 health.py SRE 组件一致）
_OK = "ok"
_DEGRADED = "degraded"
_DOWN = "down"
_NOT_CONFIGURED = "not_configured"


def _check(check: str, status: str, detail: str,
           required: bool = False) -> Dict[str, Any]:
    return {"check": check, "status": status, "detail": detail,
            "required": required}


def _dir_status(path_str: str) -> str:
    """目录三态：missing / empty / ok（供本地数据面检查复用）。"""
    if not os.path.isdir(path_str):
        return "missing"
    try:
        if not any(os.scandir(path_str)):
            return "empty"
    except OSError:
        return "missing"
    return "ok"


# ── 纯配置面（零 IO）────────────────────────────────────────────────


def check_deployment_profile() -> Dict[str, Any]:
    from app.core.config import settings

    profile = settings.DEPLOYMENT_PROFILE
    env = settings.ENV
    detail = f"profile={profile} env={env}"
    if profile == "air_gapped":
        detail += "（离线/内网部署；运行手册 docs/DEPLOYMENT-offline.md）"
    return _check("deployment_profile", _OK, detail)


def check_egress_policy() -> Dict[str, Any]:
    from app.core import network_dependency as nd
    from app.core.egress import current_policy

    policy = current_policy()
    if policy.mode != "allowlist":
        return _check("egress_policy", _NOT_CONFIGURED,
                      "NETWORK_EGRESS_MODE=unrestricted（守卫未激活）")
    summary = nd.offline_capability_summary()
    counts = summary["counts"]
    return _check(
        "egress_policy", _OK,
        f"allowlist 激活；显式主机 {len(policy.exact_hosts)}；"
        f"离线依赖 {counts['available_offline']}/{counts['total']}"
        f"（守卫覆盖 {counts['covered_by_egress_guard']}）",
    )


def check_rag_embeddings() -> Dict[str, Any]:
    from app.core.config import settings

    offline = bool(settings.RAG_EMBEDDING_OFFLINE)
    if offline:
        return _check("rag_embeddings", _OK,
                      "RAG_EMBEDDING_OFFLINE=true（模型缓存须预置，见 "
                      "docs/DEPLOYMENT-offline.md §嵌入模型）")
    profile = settings.DEPLOYMENT_PROFILE
    if profile == "air_gapped":
        return _check(
            "rag_embeddings", _DOWN,
            "air-gapped 下 RAG_EMBEDDING_OFFLINE=false：首次加载将尝试联网"
            "下载并把 to_thread worker 挂死——置 RAG_EMBEDDING_OFFLINE=true",
            required=True,
        )
    return _check("rag_embeddings", _NOT_CONFIGURED,
                  "RAG_EMBEDDING_OFFLINE=false（首用自动下载；离线部署需改）")


def check_local_geodata() -> Dict[str, Any]:
    from app.core.config import settings

    raw = (settings.LOCAL_GEODATA_DIR or "").strip()
    if not raw:
        return _check(
            "local_geodata", _NOT_CONFIGURED,
            "LOCAL_GEODATA_DIR 未配置（local-first 行政区/OSM/POI/年鉴关闭）",
        )
    state = _dir_status(raw)
    if state == "ok":
        return _check("local_geodata", _OK, f"root={raw}")
    if state == "empty":
        # 目录已配置但未灌数：local-first 优雅回退（本地 miss → 工具面
        # 提示），是能力降级不是部署阻断。
        return _check(
            "local_geodata", _DEGRADED,
            f"root={raw} 为空（先跑 manage.py osm-ingest / gd-poi-ingest / "
            "yearbook-ingest 恢复本地优先能力）",
        )
    return _check("local_geodata", _DOWN, f"root={raw} 不存在", required=True)


def check_data_fabric_local_roots() -> Dict[str, Any]:
    from app.core.config import settings

    raw = (settings.DATA_FABRIC_LOCAL_FILE_ROOTS or "").strip()
    roots = [r.strip() for r in raw.split(",") if r.strip()]
    if not roots:
        return _check("data_fabric_local_roots", _NOT_CONFIGURED,
                      "DATA_FABRIC_LOCAL_FILE_ROOTS 为空（本地文件读取不受限）")
    bad = [r for r in roots if not os.path.isdir(r)]
    if bad:
        return _check("data_fabric_local_roots", _DEGRADED,
                      f"缺失 roots: {bad}（远程源不受影响；本地源读取会失败）")
    return _check("data_fabric_local_roots", _OK, f"roots={roots}")


def check_data_dirs() -> Dict[str, Any]:
    """DATA_DIR / TMP_DIR 可写性（产物/export 落盘前提）。"""
    from app.core.config import settings

    problems: List[str] = []
    for name in ("DATA_DIR", "TMP_DIR"):
        path = getattr(settings, name)
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".preflight-write-probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
        except OSError as exc:
            problems.append(f"{name}={path}: {exc}")
    if problems:
        return _check("data_dirs", _DOWN, "; ".join(problems), required=True)
    return _check("data_dirs", _OK, "DATA_DIR/TMP_DIR 可写")


def check_network_dependencies() -> Dict[str, Any]:
    from app.core import network_dependency as nd

    summary = nd.offline_capability_summary()
    if summary["profile"] != "air_gapped":
        return _check(
            "network_dependencies", _OK,
            f"cloud 模式：{summary['counts']['total']} 类依赖按需出网",
        )
    unavailable = [
        d["id"] for d in summary["dependencies"]
        if not d["available_offline"]
    ]
    detail = (
        f"离线可用 {summary['counts']['available_offline']}"
        f"/{summary['counts']['total']}"
    )
    if unavailable:
        return _check(
            "network_dependencies", _DEGRADED,
            detail + f"；unavailable: {unavailable}（typed 降级，能力面如实报）",
        )
    return _check("network_dependencies", _OK, detail)


# ── 连通性面（有界 IO）──────────────────────────────────────────────


def check_database(timeout_s: float = 3.0) -> Dict[str, Any]:
    try:
        from sqlalchemy import text

        from app.core.database import Engine
        with Engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return _check("database", _OK, "SQLAlchemy 连接 OK", required=True)
    except Exception as exc:  # noqa: BLE001
        return _check("database", _DOWN, str(exc), required=True)


def check_redis(timeout_s: float = 2.0) -> Dict[str, Any]:
    from app.core.config import settings

    if not settings.USE_REDIS:
        return _check("redis", _NOT_CONFIGURED, "USE_REDIS=false（内存实现）")
    try:
        import redis
        r = redis.from_url(settings.REDIS_URL,
                           socket_connect_timeout=timeout_s)
        r.ping()
        r.close()
        return _check("redis", _OK, settings.REDIS_URL, required=True)
    except Exception as exc:  # noqa: BLE001
        return _check("redis", _DOWN,
                      f"{settings.REDIS_URL}: {exc}", required=True)


def check_celery_worker(timeout_s: float = 3.0) -> Dict[str, Any]:
    import subprocess as _sp
    import sys as _sys

    try:
        res = _sp.run(
            [_sys.executable, "-m", "celery", "-A",
             "app.services.task_queue.celery_app", "inspect", "ping",
             "--timeout", str(int(timeout_s))],
            capture_output=True, text=True, timeout=timeout_s + 5,
        )
        if "pong" in (res.stdout or "").lower():
            return _check("celery_worker", _OK, "worker 在线")
        return _check("celery_worker", _DEGRADED,
                      "无 worker 响应（异步任务面降级；核心 API 不受影响）")
    except Exception as exc:  # noqa: BLE001
        return _check("celery_worker", _DEGRADED, f"探测失败: {exc}")


def check_llm_endpoint(timeout_s: float = 5.0) -> Dict[str, Any]:
    """LLM endpoint：先策略判定（local/public），再连通性探测。

    策略拒绝与网络失败分开陈述——离线部署里"指向了公网 LLM"是配置错误，
    "本机 LLM 没起"是运行状态，运维动作完全不同。
    """
    import urllib.parse

    from app.core.config import settings

    base_url = settings.LLM_BASE_URL
    host = urllib.parse.urlparse(base_url).hostname or ""
    from app.core.egress import _is_private_host

    is_local = _is_private_host(host.strip("[]").lower())
    policy_part = "policy=local" if is_local else "policy=public"

    profile = settings.DEPLOYMENT_PROFILE
    if profile == "air_gapped" and not is_local:
        return _check(
            "llm_endpoint", _DOWN,
            f"{base_url} {policy_part}——air-gapped 下公网 LLM 被守卫拒绝，"
            "请指向本地/内网端点或加入 NETWORK_EGRESS_ALLOW",
            required=True,
        )

    try:
        import httpx
        from app.core.egress import assert_egress_allowed

        assert_egress_allowed(base_url, dependency_id="llm_chat")
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(base_url.rstrip("/") + "/models")
        if resp.status_code < 500:
            return _check("llm_endpoint", _OK,
                          f"{base_url} 可达（{policy_part}）", required=True)
        return _check("llm_endpoint", _DOWN,
                      f"{base_url} 返回 {resp.status_code}（{policy_part}）",
                      required=True)
    except Exception as exc:  # noqa: BLE001
        reason = type(exc).__name__
        hint = (
            "本机/内网推理服务（ollama/vllm 等）未启动？"
            if is_local
            else "公网端点在离线网络下不可达：指向内网端点或加入 "
                 "NETWORK_EGRESS_ALLOW"
        )
        return _check(
            "llm_endpoint", _DOWN,
            f"{base_url} 不可达（{policy_part}，{reason}）——{hint}",
            required=True,
        )


#: 检查注册表（preflight 的稳定顺序；--only 按名过滤）
ALL_CHECKS = (
    ("deployment_profile", check_deployment_profile, False),
    ("egress_policy", check_egress_policy, False),
    ("network_dependencies", check_network_dependencies, False),
    ("database", check_database, True),
    ("redis", check_redis, True),
    ("celery_worker", check_celery_worker, False),
    ("llm_endpoint", check_llm_endpoint, True),
    ("rag_embeddings", check_rag_embeddings, True),
    ("local_geodata", check_local_geodata, True),
    ("data_fabric_local_roots", check_data_fabric_local_roots, False),
    ("data_dirs", check_data_dirs, True),
)


async def run_preflight(only: Optional[List[str]] = None) -> Dict[str, Any]:
    """执行 preflight，返回结构化报告（含 exit code 判据）。"""
    from app.core import network_dependency as nd

    selected = [(n, fn, req) for (n, fn, req) in ALL_CHECKS
                if only is None or n in only]
    checks: List[Dict[str, Any]] = []
    for name, fn, required in selected:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 — 探测自身故障按 down 诚实上报
            result = _check(name, _DOWN, f"probe error: {type(exc).__name__}",
                            required=required)
        checks.append(result)
    failed = sum(1 for c in checks
                 if c["status"] == _DOWN and c["required"])
    degraded = sum(1 for c in checks if c["status"] == _DEGRADED)
    nd.reset_catalog_settings_cache()
    return {
        "schema_version": 1,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "failed": failed,
            "degraded": degraded,
            "exit_code": 1 if failed else 0,
        },
    }
