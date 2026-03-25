# BUG-E2E-001: 后端启动失败 - 缺少 app.db 模块

## 问题描述
后端服务启动失败，提示 `ModuleNotFoundError: No module named 'app.db'`。

## 复现步骤
1. 进入项目目录：`cd ~/projects/webgis-ai-agent/frontend`
2. 启动后端服务：`python -m uvicorn app.main:app --host 0.0.0.0 --port 8002`
3. 查看错误日志

## 预期结果
后端服务正常启动，健康检查接口 `/api/v1/health` 返回 200。

## 实际结果
```
ModuleNotFoundError: No module named 'app.db'
File ".../frontend/app/api/routes/layer.py", line 16, in <module>
    from app.db.session import get_db
```

## 原因分析
- `app/db/` 目录位于 `app/` 而非 `frontend/app/`
- 前端代码 (frontend/app/) 尝试导入 `app.db` 但找不到
- 目录结构混淆

## 建议修复
1. 在 `frontend/app/` 下创建 `db/` 链接或复制 `app/db/`
2. 或者调整 imports 使用相对路径

## 优先级
🔴 高 - 阻塞端到端测试

## 关联
- 测试任务：TST002
- 之前 Issue：BUG-001（项目目录结构混乱）
--- 
**报告人**: tester  
**日期**: 2026-03-25