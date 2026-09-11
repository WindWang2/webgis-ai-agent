# Contract Drift Report（自动生成）

> 由 `python scripts/gen_drift_report.py` 从各 registry 派生，请勿手改。红线：BLOCKER/MAJOR 必须清零（tests/quality/test_contract_drift_gate.py）。

- 指纹：`0ad9267bd11a71fe…`
- 计数：total=1，BLOCKER=1，MAJOR=0，MINOR=0

## api_frontend（1）

- **BLOCKER** `FRONTEND_CALL_NO_BACKEND_ROUTE` /api/v1/geocompute/runs/*/cancel — frontend calls /api/v1/geocompute/runs/*/cancel ×1 but no backend route matches
