@echo off
start "WebGIS - AI Backend" cmd /k "set PYTHONPATH=. && venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8001"
