@echo off
chcp 65001 >nul
echo ==============================================================
echo  正在启动 WebGIS AI Agent (V2.m) 核心服务模块
echo  [包含了 MCP超脑、盲区爬行者、3D视界引擎]
echo ==============================================================

echo.
echo [1/2] 启动中枢总线与模型网络 (FastAPI + MCP)...
start "WebGIS - AI Backend" cmd /k "set PYTHONPATH=. && venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8001"
timeout /t 3 /nobreak >nul

echo [2/2] 启动具身感官视界 (Next.js 14 HUD)...
start "WebGIS - 3D Frontend" cmd /k "cd frontend && npm run dev"

echo.
echo ==============================================================
echo  所有服务核已部署完成并在独立终端窗口中运行！
echo.
echo  - 🚀 战术指挥台 (HUD): http://localhost:3000
echo  - 🎬 汇报大屏 (StoryMap): http://localhost:3000/story
echo  - 🧠 中枢 API (Swagger): http://localhost:8001/docs
echo ==============================================================
echo.
echo 提示：如需关闭服务，请分别关闭弹出的黑色终端界面拉断连接。
pause
