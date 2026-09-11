#!/bin/bash
# Harness V7 本地测试入口（Windows Git Bash / 沙箱 DNS fake-IP 环境适配）。
# 沙箱 DNS 把一切域名解析到 198.18.0.0/15 保留段，触发 Settings 的 SSRF
# 守卫；用白名单内主机名预占 setdefault 基线（conftest.py 语义允许）。
# 用法: bash .agent-work/harness-v7/run-tests.sh [pytest args...]
set -e
cd "$(dirname "$0")/../.."
export OVERPASS_API_URL="https://nominatim.openstreetmap.org/api/interpreter"
python -m pytest -o addopts="" "$@"
