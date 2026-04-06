# WebGIS AI Agent Kubernetes 部署指南

## 概述

本文档描述如何将 WebGIS AI Agent 部署到 Kubernetes 集群，支持高可用、自动扩缩容、灰度发布等生产级特性。

---

## 一、环境要求

### 1.1 Kubernetes 版本
- Kubernetes ≥ 1.24
- 已安装 Ingress Controller (推荐 Nginx Ingress)
- 已安装 Cert Manager (可选，用于自动签发 SSL 证书)
- 已安装 Prometheus + Grafana 监控栈 (可选，用于监控告警)

### 1.2 资源要求
| 组件 | 最低资源 | 推荐资源 |
|------|----------|----------|
| API 服务 | 2核CPU/4GB内存 | 4核CPU/8GB内存 |
| Celery Worker | 1核CPU/2GB内存 | 2核CPU/4GB内存 |
| PostgreSQL (可选) | 2核CPU/4GB内存 | 4核CPU/16GB内存 |
| Redis (可选) | 1核CPU/1GB内存 | 2核CPU/4GB内存 |

---

## 二、部署前准备

### 2.1 推送镜像到镜像仓库
```bash
# 构建生产镜像
docker build -f Dockerfile.prod -t your-registry.com/webgis-prod:v1.0.0 .

# 推送镜像到私有镜像仓库
docker push your-registry.com/webgis-prod:v1.0.0
```

### 2.2 修改配置文件
```bash
cd deploy/k8s/

# 1. 修改 kustomization.yaml 中的镜像地址为实际的镜像仓库地址
# 2. 修改 01-configmap.yaml 中的敏感配置（数据库连接、JWT密钥等）
# 3. 修改 04-ingress.yaml 中的域名配置为实际域名
```

### 2.3 创建命名空间
```bash
kubectl apply -f 00-namespace.yaml
```

---

## 三、部署方式

### 方式一：使用 Kustomize 一键部署（推荐）
```bash
# 部署所有资源
kubectl apply -k .

# 查看部署状态
kubectl get all -n webgis-prod
```

### 方式二：按顺序手动部署
```bash
# 1. 部署配置和密钥
kubectl apply -f 01-configmap.yaml

# 2. 部署API服务
kubectl apply -f 02-api-deployment.yaml

# 3. 部署Celery Worker
kubectl apply -f 03-celery-deployment.yaml

# 4. 部署Ingress
kubectl apply -f 04-ingress.yaml

# 5. (可选) 部署内部PostgreSQL和Redis（测试环境用）
kubectl apply -f 05-deps-optional.yaml
```

---

## 四、部署验证

### 4.1 检查Pod状态
```bash
kubectl get pods -n webgis-prod
```

预期输出：
```
NAME                         READY   STATUS    RESTARTS   AGE
webgis-api-xxxxxxxxx-xxxxx   1/1     Running   0          5m
webgis-api-xxxxxxxxx-xxxxx   1/1     Running   0          5m
webgis-celery-xxxxxxxxx-xx   1/1     Running   0          5m
```

### 4.2 检查服务状态
```bash
kubectl get services -n webgis-prod
```

### 4.3 健康检查
```bash
# 转发端口到本地
kubectl port-forward svc/webgis-api-service 8000:8000 -n webgis-prod

# 调用健康检查接口
curl http://localhost:8000/api/v1/health/live
```

预期返回：
```json
{
  "status": "operational",
  "database": "connected",
  "redis": "connected"
}
```

### 4.4 访问前端页面
```bash
# 转发前端端口
kubectl port-forward svc/webgis-api-service 3000:80 -n webgis-prod

# 在浏览器中访问 http://localhost:3000
```

---

## 五、监控与告警

### 5.1 Prometheus 指标采集
API服务默认暴露 `/metrics` 端点，已配置自动采集注解：
```yaml
annotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8000"
  prometheus.io/path: "/metrics"
```

### 5.2 告警规则
告警规则位于 `deploy/alerts-rules.json`，包含以下告警项：
- 🔴 服务宕机告警（API/Celery）
- 🟡 CPU/内存使用率过高告警
- 🟡 API错误率过高告警（>5%）
- 🟡 API响应慢告警（P95>2s, P99>5s）
- 🟡 任务积压告警
- 🔴 数据库/Redis连接失败告警

### 5.3 Grafana 面板
导入 `deploy/grafana/dashboards/` 目录下的面板JSON文件到Grafana，可查看：
- API请求统计（QPS、响应时间、错误率）
- 系统资源使用（CPU、内存、磁盘）
- 任务处理统计
- 数据库性能指标

---

## 六、日常运维

### 6.1 扩缩容配置
```bash
# 手动扩缩容API副本数
kubectl scale deployment webgis-api -n webgis-prod --replicas=5

# 查看HPA状态
kubectl get hpa -n webgis-prod
```

### 6.2 版本升级
```bash
# 更新镜像版本
kubectl set image deployment/webgis-api webgis-api=your-registry.com/webgis-prod:v1.1.0 -n webgis-prod

# 查看升级进度
kubectl rollout status deployment/webgis-api -n webgis-prod
```

### 6.3 回滚操作
```bash
# 查看历史版本
kubectl rollout history deployment/webgis-api -n webgis-prod

# 回滚到上一个版本
kubectl rollout undo deployment/webgis-api -n webgis-prod

# 回滚到指定版本
kubectl rollout undo deployment/webgis-api --to-revision=2 -n webgis-prod
```

### 6.4 查看日志
```bash
# 查看API日志
kubectl logs -f deployment/webgis-api -n webgis-prod

# 查看Celery日志
kubectl logs -f deployment/webgis-celery -n webgis-prod
```

### 6.5 数据备份
```bash
# 备份PostgreSQL（如使用内部部署）
kubectl exec -it postgres-xxxxxxxxx-xxxxx -n webgis-prod -- pg_dump -U webgis_prod webgis_prod > backup_$(date +%Y%m%d).sql

# 备份Redis（如使用内部部署）
kubectl exec -it redis-xxxxxxxxx-xxxxx -n webgis-prod -- redis-cli BGSAVE
kubectl cp redis-xxxxxxxxx-xxxxx:/data/dump.rdb ./redis_backup_$(date +%Y%m%d).rdb
```

---

## 七、生产环境最佳实践

### 7.1 高可用配置
1. **使用外部托管的数据库和缓存**：推荐使用云服务商的托管PostgreSQL（带PostGIS扩展）和Redis服务
2. **配置多可用区调度**：为Pod配置节点亲和性，分散到不同可用区
3. **开启Pod中断预算**：配置PDB确保更新时服务可用性

### 7.2 安全加固
1. **使用RBAC权限控制**：限制服务账户的权限
2. **配置网络策略**：限制不同服务间的访问
3. **开启镜像扫描**：镜像推送前进行漏洞扫描
4. **密钥使用Secret管理**：敏感信息不存储在配置文件中

### 7.3 性能优化
1. **配置水平自动扩缩容**：根据CPU/内存使用率自动调整副本数
2. **配置Pod资源限制**：避免单个Pod占用过多资源
3. **开启节点自动扩缩容**：集群资源不足时自动添加节点

---

## 八、故障排查

### 8.1 Pod启动失败
```bash
# 查看Pod详情
kubectl describe pod <pod-name> -n webgis-prod

# 查看容器日志
kubectl logs <pod-name> -n webgis-prod --previous
```

### 8.2 服务无法访问
```bash
# 检查服务是否正常
kubectl get endpoints webgis-api-service -n webgis-prod

# 检查Ingress配置
kubectl describe ingress webgis-ingress -n webgis-prod

# 检查Ingress Controller日志
kubectl logs -n ingress-nginx <ingress-controller-pod>
```

### 8.3 性能问题
```bash
# 查看Pod资源使用
kubectl top pods -n webgis-prod

# 查看节点资源使用
kubectl top nodes
```

---

## 九、卸载部署
```bash
# 卸载所有资源
kubectl delete -k .

# 卸载命名空间（会删除所有资源）
kubectl delete namespace webgis-prod
```

---

## 附录

### 配置文件说明
| 文件名 | 说明 |
|--------|------|
| `00-namespace.yaml` | Kubernetes 命名空间配置 |
| `01-configmap.yaml` | 应用配置和敏感信息（Secret） |
| `02-api-deployment.yaml` | API服务Deployment + Service配置 |
| `03-celery-deployment.yaml` | Celery Worker Deployment + PVC配置 |
| `04-ingress.yaml` | Ingress + HPA自动扩缩容配置 |
| `05-deps-optional.yaml` | 可选内部PostgreSQL + Redis配置（测试用） |
| `kustomization.yaml` | Kustomize 一键部署配置 |

### 相关文档
- [Docker Compose 部署指南](./deployment-production.md)
- [API 接口文档](./api-docs.md)
- [架构设计说明书](./architecture.md)