# Release Readiness（生成物 · 确定性）

- verdict: **NOT-READY**
- git commit: `1ac40019c033`

## 现场闸（生成时真实执行）

- [pass] quality_manifest
- [pass] contract_drift
- [pass] integration_preflight
- [pass] frontend_behavior

## 车道证据

- quick: not-run
- backend: not-run
- frontend: not-run
- science: not-run
- cartography: not-run
- data: not-run
- security: not-run
- quality: not-run
- perf: not-run
- real: not-run
- 证据说明: 未提供 --runner-report（车道证据 opt-in）

## 政策缺口

- lane backend 状态 not-run（需 pass 证据或显式 waiver）
- lane quick 状态 not-run（需 pass 证据或显式 waiver）

## Known gaps（诚实披露）

- adr-duplicate-77: 存量 ADR 0077 撞号（watermark 下 known limitation）: ['docs/adr/0077-multi-pod-topology-turn-ownership.md', 'docs/adr/0077-wrap-vendored-pi.md']
- adr-duplicate-88: 存量 ADR 0088 撞号（watermark 下 known limitation）: ['docs/adr/0088-autonomous-gis-product-runtime.md', 'docs/adr/0088-cartographic-component-library-v2.md', 'docs/adr/0088-gis-data-analysis-runtime-v2.md']
- adr-duplicate-94: 存量 ADR 0094 撞号（watermark 下 known limitation）: ['docs/adr/0094-enterprise-geospatial-data-fabric-v2.md', 'docs/adr/0094-spatial-decision-intelligence-v3.md']
- adr-duplicate-96: 存量 ADR 0096 撞号（watermark 下 known limitation）: ['docs/adr/0096-agent-product-plane-vnext.md', 'docs/adr/0096-geocompute-data-plane-v3.md']
- adr-duplicate-99: 存量 ADR 0099 撞号（watermark 下 known limitation）: ['docs/adr/0099-map-product-lifecycle-v2.md', 'docs/adr/0099-spatial-science-geoai-platform-vnext.md']
- adr-duplicate-101: 存量 ADR 0101 撞号（watermark 下 known limitation）: ['docs/adr/0101-agent-tool-model-runtime-v2.md', 'docs/adr/0101-cartographic-template-library-v3.md', 'docs/adr/0101-geocompute-data-fabric-v4.md', 'docs/adr/0101-geoworkflow-recipe-conformance-foundation.md']
- adr-duplicate-103: 存量 ADR 0103 撞号（watermark 下 known limitation）: ['docs/adr/0103-cartographic-design-system-v4.md', 'docs/adr/0103-data-artifact-workspace-foundation-v3.md', 'docs/adr/0103-pi-gis-runtime-v3.md']
- adr-duplicate-104: 存量 ADR 0104 撞号（watermark 下 known limitation）: ['docs/adr/0104-data-control-geocompute-platform-v5.md', 'docs/adr/0104-gis-extension-platform-v1.md', 'docs/adr/0104-gis-harness-autonomous-runtime-v4.md', 'docs/adr/0104-professional-cartography-workbench-v4.md', 'docs/adr/0104-quality-reliability-platform-v1.md']
- adr-duplicate-105: 存量 ADR 0105 撞号（watermark 下 known limitation）: ['docs/adr/0105-gis-extension-platform-v2.md', 'docs/adr/0105-workbench-v5-collaboration.md']
- adr-duplicate-118: 存量 ADR 0118 撞号（watermark 下 known limitation）: ['docs/adr/0118-cartographic-rendering-v5.md', 'docs/adr/0118-federated-spatial-query-optimizer-v6.md', 'docs/adr/0118-gis-harness-autonomous-runtime-v5.md', 'docs/adr/0118-quality-reliability-security-platform-v2.md', 'docs/adr/0118-semantic-workflow-compiler-v4.md', 'docs/adr/0118-spatial-data-lakehouse-cube-v6.md']
- real-lane-opt-in: real-services 车道为 opt-in 资源纪律；未运行时本报告以 not-run 显式标注，不构成 pass
- browser-e2e: Playwright 浏览器级 E2E 沿用既有 nightly REQUIRE_BROWSER 机制；本 Epic 的前端证据为 vitest 组件/模块行为级
