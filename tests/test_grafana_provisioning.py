"""Issue #562: Grafana dashboards 必须有 provisioning provider，否则不加载。

deploy/grafana/provisioning/ 里只有 dashboard.json 导出 + datasources.yml，
没有 dashboards 的 provider 描述 —— Grafana 默认忽略 provisioning 树里的未知
JSON，面板永远不加载（datasources 侧有同款 provider 约定且工作正常，形成对照）。

本文件守住：
  1. dashboards/ 目录一旦含 .json，就必须同时含 provider .yml（apiVersion 1,
     type: file, options.path 指向被 compose 挂载的目录）；
  2. datasources 的 provisioning 同样存在且形状正确；
  3. compose grafana 服务确实把 provisioning 目录挂进容器。
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PROVISIONING = REPO_ROOT / "deploy" / "grafana" / "provisioning"


def _dashboards_providers() -> list:
    dash_dir = PROVISIONING / "dashboards"
    providers = []
    for path in sorted(dash_dir.glob("*.y*ml")):
        providers.append(yaml.safe_load(path.read_text(encoding="utf-8")))
    return providers


def test_dashboard_json_requires_provider():
    dash_dir = PROVISIONING / "dashboards"
    jsons = list(dash_dir.glob("*.json"))
    assert jsons, "dashboards/ 竟然没有 dashboard 导出？"
    providers = _dashboards_providers()
    assert providers, (
        "dashboards/ 含 .json 但没有 provider yml —— Grafana 不会加载这些面板"
    )


def test_provider_shape_is_valid():
    for doc in _dashboards_providers():
        assert doc.get("apiVersion") == 1, "provider 必须 apiVersion: 1"
        assert doc.get("providers"), "provider 必须声明 providers 列表"
        for prov in doc["providers"]:
            assert prov.get("type") == "file", "必须用 file 型 provider 扫描目录"
            options = prov.get("options", {})
            assert options.get("path"), "provider 必须声明 options.path"


def test_provider_path_matches_compose_mount():
    """provider 的 options.path 必须是 compose 挂载进容器的路径
    （/etc/grafana/provisioning/...），否则文件在容器内根本不存在。"""
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    grafana = compose["services"]["grafana"]
    vol_text = "\n".join(
        v if isinstance(v, str) else v.get("source", "") for v in grafana.get("volumes", [])
    )
    assert "./deploy/grafana/provisioning" in vol_text, (
        "grafana 服务必须挂载 provisioning 目录"
    )
    expected = "/etc/grafana/provisioning/dashboards"
    for doc in _dashboards_providers():
        for prov in doc["providers"]:
            assert prov["options"]["path"] == expected, (
                f"provider path={prov['options']['path']!r} 必须指向 compose 挂载"
                f"目录 {expected}"
            )


def test_datasources_provisioning_exists_and_shaped():
    ds_path = PROVISIONING / "datasources" / "datasources.yml"
    assert ds_path.exists(), "datasources provisioning 缺失"
    doc = yaml.safe_load(ds_path.read_text(encoding="utf-8"))
    assert doc.get("apiVersion") == 1
    assert doc.get("datasources"), "datasources.yml 必须声明 datasources"
    assert any(
        d.get("type") == "prometheus" for d in doc["datasources"]
    ), "datasources 必须含 prometheus 条目（面板查询依赖）"
