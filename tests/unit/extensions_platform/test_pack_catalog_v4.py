"""Pack catalog 与认证状态投影测试（ADR-0201）。

- catalog 确定性（排序）；surface 如实转述（tier / scientific_status /
  governance_tier=candidate）；
- certified_only 过滤：无报告 / 报告过期 / 认证通过的差异；
- 过期报告在 catalog 中标 stale（运维盘点信号）。
"""

from __future__ import annotations

from app.extensions_platform.capability_certification import run_pack_certification
from app.extensions_platform.pack_catalog import build_pack_catalog, certification_status_for

EXTENSION_ID = "certv4.pack"


class TestCatalog:
    def test_catalog_shape_and_bounded_surface(self, v4_env):
        v4_env.build(skill_contract={
            "name": "Area Skill", "domain": "general",
            "procedure": {"steps": [{"step_id": "s1", "title": "t", "kind": "analyze"}]},
        })
        host, _ = v4_env.make_host()
        catalog = build_pack_catalog(host)
        assert catalog["catalog_schema_version"] == 1
        namespaces = [ns["namespace"] for ns in catalog["namespaces"]]
        assert namespaces == ["certv4"]
        pack = catalog["namespaces"][0]["packs"][0]
        assert pack["id"] == EXTENSION_ID
        tool = pack["surface"]["tools"][0]
        assert tool["name"] == "certv4_rect_area" and tool["tier"] == 1
        algo = pack["surface"]["algorithms"][0]
        assert algo["id"] == "certv4.rect_area_algo"
        skill = pack["surface"]["skills"][0]
        assert skill["governance_tier"] == "candidate"
        assert pack["certification"]["state"] == "missing"

    def test_certified_only_filter(self, v4_env):
        v4_env.build()
        host, _ = v4_env.make_host()
        # 未认证：被过滤。
        filtered = build_pack_catalog(host, certified_only=True)
        assert filtered["namespaces"] == []
        assert [s["reason"] for s in filtered["skipped"]] == ["missing"]
        # 认证并落地报告：进入 catalog。
        run_pack_certification(host, EXTENSION_ID, save=True)
        host.discover()
        catalog = build_pack_catalog(host, certified_only=True)
        ids = [p["id"] for ns in catalog["namespaces"] for p in ns["packs"]]
        assert ids == [EXTENSION_ID]
        assert "skipped" in catalog and catalog["skipped"] == []

    def test_stale_report_surfaces_in_status(self, v4_env):
        v4_env.build()
        host, _ = v4_env.make_host()
        run_pack_certification(host, EXTENSION_ID, save=True)
        main_py = v4_env.tmp_path / "certv4-pack" / "main.py"
        main_py.write_text(main_py.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
        host.discover()
        record = host.get_record(EXTENSION_ID)
        status = certification_status_for(record)
        assert status["state"] == "stale"

    def test_catalog_is_deterministic(self, v4_env):
        v4_env.build()
        host, _ = v4_env.make_host()
        first = build_pack_catalog(host)
        second = build_pack_catalog(host)
        assert first == second
