# BUG-003: Docker 构建权限不足

## 问题描述

执行 Docker 构建时出现权限错误，无法连接到 Docker daemon socket。

## 错误信息

```
permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock: 
Post "http://%2Fvar%2Frun%2Fdocker.sock/v1.50/build?...": 
dial unix /var/run/docker.sock: connect: permission denied
```

## 原因分析

当前用户没有访问 Docker socket 的权限。

## 解决方案

### 方案 1: 将用户加入 docker 组（推荐）

```bash
sudo usermod -aG docker $USER
# 重新登录或执行
newgrp docker
```

### 方案 2: 使用 sudo 构建

```bash
sudo docker build -t webgis-ai-agent:test .
```

## 优先级

🟡 中 - 可临时使用 sudo 绕过

## 关联

- 测试任务：TST001

---
**报告人**: Tester-Agent
**日期**: 2026-03-23
