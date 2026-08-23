"""#473 regression gate — alert/dashboard PromQL must match the real metric surface.

Silent alerting blackout class this file prevents: rules written against an
imagined instrumentation surface (metrics never emitted, labels that do not
exist, exporters never scraped, single-quoted label matchers that are not even
valid PromQL). Such rules load into Prometheus and then evaluate to empty/NaN
forever — nobody gets paged.

The gate cross-checks three inventories:

1. APP-EMITTED METRICS — collected live: a FastAPI app instrumented exactly the
   way ``app/main.py`` does it (``Instrumentator().instrument(app).expose(...)``,
   default metric set on the default REGISTRY) is driven with real requests
   (2xx/4xx/5xx) and its /metrics exposition is parsed into
   {series_name: {label_names}}. This is precisely what scrape job
   ``webgis-api`` ingests.
2. EXPORTER METRICS — the metrics each WIRED exporter provides, keyed by the
   scrape job that ingests it; the test also asserts the job is enabled in
   deploy/prometheus.yml and the exporter service exists in BOTH prod compose
   files, so a rule can only reference an exporter metric whose data path is
   actually deployed.
3. PROMETHEUS BUILTINS — ``up`` (one series per scrape job).

Every PromQL expression in deploy/alerts-rules.json and the Grafana dashboard
must only reference metrics from 1-3, with label selectors restricted to the
metric's real label names (+ scrape-added job/instance). Drift fails CI.
"""
import asyncio
import json
import re
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"

# ── Inventory 2: wired exporters ──────────────────────────────────────────
# job -> {service: compose service name, metrics: {metric: allowed labels}}
# Metric names are the documented surface of each exporter image pinned in
# docker-compose.prod*.yml. Keep in sync with the compose services.
EXPORTER_METRICS = {
    "postgres": {
        "service": "postgres-exporter",
        "metrics": {"pg_up": set()},
    },
    "redis": {
        "service": "redis-exporter",
        "metrics": {
            "redis_memory_used_bytes": set(),
            "redis_memory_max_bytes": set(),
            # exported only because compose sets REDIS_EXPORTER_CHECK_KEYS=celery
            "redis_key_size": {"key"},
        },
    },
    "node": {
        "service": "node-exporter",
        "metrics": {
            "node_filesystem_avail_bytes": {"device", "fstype", "mountpoint"},
            "node_filesystem_size_bytes": {"device", "fstype", "mountpoint"},
        },
    },
}

# Labels Prometheus itself attaches to every scraped series.
SCRAPE_ADDED_LABELS = {"job", "instance"}

# PromQL keywords/functions/aggregations that are not metric names.
PROMQL_KEYWORDS = {
    "sum", "min", "max", "avg", "group", "stddev", "stdvar", "count",
    "count_values", "bottomk", "topk", "quantile", "limitk", "limit_ratio",
    "by", "without", "on", "ignoring", "group_left", "group_right",
    "offset", "bool", "and", "or", "unless", "atan2", "start", "end",
    "rate", "irate", "increase", "delta", "idelta", "deriv",
    "avg_over_time", "sum_over_time", "min_over_time", "max_over_time",
    "count_over_time", "quantile_over_time", "stddev_over_time",
    "stdvar_over_time", "last_over_time", "present_over_time",
    "changes", "resets", "predict_linear", "holt_winters",
    "double_exponential_smoothing", "histogram_quantile", "histogram_count",
    "histogram_sum", "histogram_fraction", "absent", "absent_over_time",
    "scalar", "vector", "time", "timestamp", "sort", "sort_desc",
    "sort_by_label", "label_replace", "label_join", "clamp", "clamp_max",
    "clamp_min", "round", "ln", "log2", "log10", "exp", "sqrt", "floor",
    "ceil", "sgn", "pi", "day_of_month", "day_of_week", "day_of_year",
    "days_in_month", "hour", "minute", "month", "year", "inf", "nan",
}

# Known-phantom names from the pre-#473 rules — must never come back.
FORBIDDEN_METRICS = {
    "http_requests_status_code",
    "http_requests_success_total",
    "container_cpu_usage_seconds_total",
    "container_memory_working_set_bytes",
    "container_memory_usage_bytes",
    "container_spec_memory_limit_bytes",
    "kube_pod_container_resource_limits",
    "celery_queue_length",
    "redis_list_length",
}

# Incident classes the alert file must keep covering (anti-decorative guard:
# deleting a rule to make the consistency check pass is not a fix).
REQUIRED_ALERTS = {
    "WebGIS_API_Down",
    "High_Error_Rate",
    "Slow_Response_P95",
    "Slow_Response_P99",
    "High_API_CPU_Usage",
    "High_API_Memory_Usage",
    "Database_Connection_Failure",
    "Redis_Memory_High",
    "Disk_Space_Low",
    "Celery_Task_Backlog",
    "Auth_JWT_Errors",
}


# ── Inventory 1: live app-emitted metrics ─────────────────────────────────

def parse_prom_exposition(text: str) -> dict[str, set[str]]:
    """Parse a /metrics exposition into {series_name: {label_names}}."""
    metrics: dict[str, set[str]] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name_part = line.rpartition(" ")[0]
        if "{" in name_part:
            name, _, labels_part = name_part.partition("{")
            for match in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_]*)="', labels_part):
                metrics.setdefault(name, set()).add(match.group(1))
        else:
            metrics.setdefault(name_part.strip(), set())
    return metrics


def _drive_fixture_app() -> str:
    """Instrument an app exactly like app/main.py, hit 2xx/4xx/5xx, scrape."""
    # Register the app's custom counter into the default REGISTRY (the same
    # registry the instrumentator exposes) so it appears in the inventory.
    import app.core.auth_metrics  # noqa: F401
    from prometheus_fastapi_instrumentator import Instrumentator

    app = FastAPI()
    # 保持默认 REGISTRY：本 fixture 的职责是复现 app/main.py 的完整清单面
    #（含 process_* 与 auth 计数器），供 inventory 断言消费。
    Instrumentator().instrument(app).expose(
        app, endpoint="/metrics", include_in_schema=False
    )

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        return JSONResponse({"error": "x"}, status_code=500)

    async def drive() -> str:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            await c.get("/ok")
            await c.get("/boom")
            await c.get("/missing")  # 4xx via unmatched handler
            return (await c.get("/metrics")).text

    return asyncio.run(drive())


@pytest.fixture(scope="module")
def app_emitted_metrics():
    return parse_prom_exposition(_drive_fixture_app())


# ── Parsers ───────────────────────────────────────────────────────────────

def enabled_scrape_jobs() -> set[str]:
    cfg = yaml.safe_load((DEPLOY / "prometheus.yml").read_text())
    return {job["job_name"] for job in cfg["scrape_configs"]}


def compose_services(compose_file: str) -> dict:
    root = Path(__file__).resolve().parents[1]
    return yaml.safe_load((root / compose_file).read_text())["services"]


def collect_exprs() -> list[tuple[str, str]]:
    """All (source, expr) pairs from alert rules and the Grafana dashboard."""
    pairs: list[tuple[str, str]] = []
    rules = json.loads((DEPLOY / "alerts-rules.json").read_text())
    for group in rules["groups"]:
        for rule in group["rules"]:
            pairs.append((f"alert {rule.get('alert', '<unnamed>')}", rule["expr"]))
    dash = json.loads((DEPLOY / "grafana/provisioning/dashboards/dashboard.json").read_text())
    for panel in dash.get("panels", []):
        for target in panel.get("targets", []):
            if target.get("expr"):
                pairs.append((f"dashboard panel '{panel.get('title')}'", target["expr"]))
    return pairs


GROUPING_CLAUSE = re.compile(r"\b(?:by|without|on|ignoring)\s*\(([^)]*)\)")
IDENTIFIER = re.compile(r"(?<![0-9a-zA-Z_:])([a-zA-Z_:][a-zA-Z0-9_:]*)")
METRIC_WITH_SELECTOR = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)\s*(\{[^}]*\})")
LABEL_MATCHER = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=~|!~|!?=)\s*"([^"]*)"')


def extract_references(expr: str) -> dict[str, dict[str, str]]:
    """Extract {metric: {label_name: matched_value}} from a PromQL expr."""
    refs: dict[str, dict[str, str]] = {}
    for match in METRIC_WITH_SELECTOR.finditer(expr):
        metric, selector = match.group(1), match.group(2)
        labels = {lm.group(1): lm.group(2) for lm in LABEL_MATCHER.finditer(selector)}
        refs.setdefault(metric, {}).update(labels)
    stripped = METRIC_WITH_SELECTOR.sub(" ", expr)
    stripped = GROUPING_CLAUSE.sub(" ", stripped)
    for token in IDENTIFIER.findall(stripped):
        if token not in PROMQL_KEYWORDS:
            refs.setdefault(token, {})
    return refs


# ── The gate ──────────────────────────────────────────────────────────────

def test_alert_rules_reference_only_real_metrics(app_emitted_metrics):
    jobs = enabled_scrape_jobs()
    exporter_metric_owner = {
        metric: job for job, spec in EXPORTER_METRICS.items() for metric in spec["metrics"]
    }
    problems: list[str] = []
    for source, expr in collect_exprs():
        if "'" in expr:
            problems.append(f"{source}: single-quoted matcher is invalid PromQL: {expr}")
        for metric, labels in extract_references(expr).items():
            if metric in FORBIDDEN_METRICS:
                problems.append(f"{source}: forbidden phantom metric {metric!r}")
                continue
            job = labels.get("job")
            if job is not None and job not in jobs:
                problems.append(f"{source}: {metric} selects job={job!r} which is not scraped")
            if metric == "up":
                continue  # Prometheus builtin: one series per scrape job
            if metric in app_emitted_metrics:
                allowed = app_emitted_metrics[metric] | SCRAPE_ADDED_LABELS
                if job is not None and job != "webgis-api":
                    problems.append(
                        f"{source}: app metric {metric} only exists on job 'webgis-api', not {job!r}"
                    )
            elif metric in exporter_metric_owner:
                owning_job = exporter_metric_owner[metric]
                spec = EXPORTER_METRICS[owning_job]
                allowed = spec["metrics"][metric] | SCRAPE_ADDED_LABELS
                if owning_job not in jobs:
                    problems.append(
                        f"{source}: {metric} needs exporter job {owning_job!r} "
                        "which is not enabled in prometheus.yml"
                    )
                if job is not None and job != owning_job:
                    problems.append(
                        f"{source}: {metric} belongs to job {owning_job!r}, expr selects {job!r}"
                    )
            else:
                problems.append(
                    f"{source}: metric {metric!r} is not emitted by the app or any wired "
                    "exporter — a rule referencing it would never fire"
                )
                continue
            bad_labels = set(labels) - allowed
            if bad_labels:
                problems.append(
                    f"{source}: metric {metric} has no label(s) {sorted(bad_labels)} "
                    f"(real labels: {sorted(allowed)})"
                )
    assert not problems, "Alert/dashboard expressions drifted from reality:\n" + "\n".join(problems)


def test_no_service_label_on_http_metrics():
    # The instrumentator emits handler/method/status — a service= label never exists.
    for source, expr in collect_exprs():
        assert "service=" not in expr.replace(" ", ""), (
            f"{source} uses the non-existent service= label: {expr}"
        )


def test_required_alert_coverage():
    rules = json.loads((DEPLOY / "alerts-rules.json").read_text())
    alerts = {r["alert"] for g in rules["groups"] for r in g["rules"]}
    missing = REQUIRED_ALERTS - alerts
    assert not missing, f"alert coverage regressed, missing: {sorted(missing)}"


def test_exporter_jobs_have_compose_services():
    services_prod = compose_services("docker-compose.prod.yml")
    services_secure = compose_services("docker-compose.prod.secure.yml")
    jobs = enabled_scrape_jobs()
    for job, spec in EXPORTER_METRICS.items():
        assert job in jobs, f"exporter job {job!r} not enabled in prometheus.yml"
        for services, fname in (
            (services_prod, "docker-compose.prod.yml"),
            (services_secure, "docker-compose.prod.secure.yml"),
        ):
            assert spec["service"] in services, (
                f"{fname} is missing the {spec['service']!r} service providing job "
                f"{job!r} metrics — rules referencing it would never fire"
            )


def test_rule_files_are_mounted_in_both_prod_stacks():
    # #473: docker-compose.prod.yml never mounted alerts-rules.json while
    # prometheus.yml rule_files referenced it — the non-secure prod stack had
    # NO alerting at all.
    for compose_file in ("docker-compose.prod.yml", "docker-compose.prod.secure.yml"):
        volumes = compose_services(compose_file)["prometheus"]["volumes"]
        assert any("alerts-rules.json" in v for v in volumes), (
            f"{compose_file}: prometheus does not mount alerts-rules.json — "
            "prometheus.yml rule_files points at a file that is not there"
        )


# ── "Can actually fire" checks (CI substitute for staging fault injection) ─

def test_error_rate_and_latency_rules_have_data(app_emitted_metrics):
    """The series the headline alerts need exist on the scraped surface."""
    assert "http_requests_total" in app_emitted_metrics
    assert "status" in app_emitted_metrics["http_requests_total"]
    assert "http_request_duration_highr_seconds_bucket" in app_emitted_metrics
    assert "le" in app_emitted_metrics["http_request_duration_highr_seconds_bucket"]
    assert "process_cpu_seconds_total" in app_emitted_metrics
    assert "process_resident_memory_bytes" in app_emitted_metrics


@pytest.mark.asyncio
async def test_error_rate_rule_numerator_matches_real_status_values():
    """High_Error_Rate's status=~"5.." matcher must match the instrumentator's
    grouped status values ("5xx") — a 5xx storm produces a real numerator."""
    from prometheus_fastapi_instrumentator import Instrumentator
    from prometheus_client import CollectorRegistry

    app = FastAPI()
    # 污染治理：默认 REGISTRY 可能已被更早 import 的 app.main 占用（同名
    # 序列互扰导致本 app 的 5xx 计数丢失）。隔离 registry + 把断言依赖的
    # auth 计数器（模块级 Collector，与 registry 解耦）注册进来。
    _reg = CollectorRegistry()
    from app.core.auth_metrics import AUTH_JWT_VALIDATION_ERRORS

    _reg.register(AUTH_JWT_VALIDATION_ERRORS)
    Instrumentator(registry=_reg).instrument(app).expose(
        app, endpoint="/metrics", include_in_schema=False
    )

    @app.get("/boom")
    async def boom():
        return JSONResponse({"error": "x"}, status_code=500)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.get("/boom")
        text = (await c.get("/metrics")).text

    five_xx = [ln for ln in text.splitlines() if ln.startswith("http_requests_total{") and 'status="5' in ln]
    assert five_xx, "no 5xx http_requests_total series — High_Error_Rate could never fire"
    status_values = re.findall(r'status="([^"]+)"', "\n".join(five_xx))
    assert any(re.fullmatch(r"5..", v) for v in status_values), (
        f"status values {status_values} do not match the alert matcher 5.."
    )

    les = {
        m.group(1)
        for ln in text.splitlines()
        if ln.startswith("http_request_duration_highr_seconds_bucket{")
        for m in [re.search(r'le="([^"]+)"', ln)]
        if m
    }
    assert {"2.0", "5.0", "+Inf"} <= les, (
        f"latency buckets {sorted(les)} do not cover the P95/P99 alert thresholds"
    )

    # Auth_JWT_Errors: the counter is registered and exposed on /metrics.
    assert "auth_jwt_validation_errors_total" in text, (
        "auth_jwt_validation_errors_total missing from /metrics — "
        "Auth_JWT_Errors would never fire"
    )


def test_jwt_validation_error_counter_increments():
    """The Auth_JWT_Errors data source actually counts failures."""
    from app.core.auth import verify_token
    from app.core.auth_metrics import AUTH_JWT_VALIDATION_ERRORS

    before = AUTH_JWT_VALIDATION_ERRORS._value.get()  # noqa: SLF001
    assert verify_token("not-a-jwt") is None
    assert AUTH_JWT_VALIDATION_ERRORS._value.get() == before + 1  # noqa: SLF001
